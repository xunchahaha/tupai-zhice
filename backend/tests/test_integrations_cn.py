"""钉钉/企业微信适配器 v1：mock HTTP 全覆盖，全程无真实网络。

覆盖：token 获取与缓存、钉钉记录 upsert 分批、双平台通知限频入参、配置端点
加密落盘与脱敏回读、verify 轻量探测、registry 能力协商。凭据加密主密钥经
monkeypatch 注入一次性 Fernet key，不读写 data/secrets 下的真实 key 文件。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.config import get_settings
from app.db import SessionLocal
from app.integrations import registry
from app.integrations.base import Capability
from app.integrations.credentials import CredentialStore
from app.integrations.dingtalk import (
    UNCONFIGURED_DETAIL as DINGTALK_UNCONFIGURED_DETAIL,
)
from app.integrations.dingtalk import DingTalkAdapter
from app.integrations.dingtalk import adapter as dingtalk_adapter
from app.integrations.dingtalk.client import (
    RECORD_BATCH_SIZE,
    DingTalkClient,
    DingTalkConfig,
)
from app.integrations.platform_api import cached_token, clear_token_cache, store_token
from app.integrations.wecom import UNCONFIGURED_DETAIL as WECOM_UNCONFIGURED_DETAIL
from app.integrations.wecom import WeComAdapter
from app.integrations.wecom.client import (
    NOTIFY_MAX_RECEIVERS as WECOM_NOTIFY_MAX_RECEIVERS,
)
from app.integrations.wecom.client import (
    WeComClient,
    WeComConfig,
)
from app.models import IntegrationCredential


@pytest.fixture(autouse=True)
def _cn_isolation() -> Iterator[None]:
    """凭据与令牌缓存自管自清：不向其他测试文件泄漏 dingtalk/wecom 配置状态。"""

    clear_token_cache()
    yield
    clear_token_cache()
    with SessionLocal() as db:
        db.execute(delete(IntegrationCredential))
        db.commit()


@pytest.fixture
def cipher_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """注入一次性加密主密钥（api 模块与测试共用同一 settings 实例）。"""

    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(get_settings(), "integration_token_encryption_key", key)
    return key


def test_cn_adapters_registered_with_manifests() -> None:
    entries = {entry.manifest.id: entry for entry in registry.entries()}
    assert entries["dingtalk"].adapter is not None
    assert entries["wecom"].adapter is not None
    assert entries["google_workspace"].adapter is None
    dingtalk = entries["dingtalk"].manifest
    wecom = entries["wecom"].manifest
    assert dingtalk.capabilities == frozenset(
        {
            Capability.TABLE_STORE,
            Capability.CALENDAR,
            Capability.NOTIFIER,
            Capability.APPROVAL,
        }
    )
    # 企业微信平台无审批代发起 API：manifest 明确不声明 approval。
    assert wecom.capabilities == frozenset(
        {Capability.TABLE_STORE, Capability.CALENDAR, Capability.NOTIFIER}
    )
    assert dingtalk.docs_url == "docs/integrations/dingtalk.md"
    assert wecom.docs_url == "docs/integrations/wecom.md"
    assert dingtalk.config_schema is not None
    assert wecom.config_schema is not None
    assert dingtalk.config_schema["required"] == ["app_key", "app_secret"]
    assert wecom.config_schema["required"] == ["corp_id", "corp_secret", "agent_id"]
    assert "app_secret" in dingtalk.config_schema["properties"]
    assert "corp_secret" in wecom.config_schema["properties"]


def test_capabilities_negotiate_with_stored_credentials(cipher_key: str) -> None:
    with SessionLocal() as db:
        settings = get_settings()
        dingtalk = registry.get_integration("dingtalk", settings, db)
        wecom = registry.get_integration("wecom", settings, db)
        assert dingtalk is not None
        assert wecom is not None
        assert dingtalk.capabilities() == set()
        assert wecom.capabilities() == set()

        store = CredentialStore(settings, db)
        store.save(
            "dingtalk",
            {"app_key": "dk-key", "app_secret": "dk-secret", "process_code": "PROC-1"},
        )
        store.save("wecom", {"corp_id": "corp", "corp_secret": "wc-secret", "agent_id": 100})
        db.commit()
        assert dingtalk.capabilities() == {
            Capability.TABLE_STORE,
            Capability.CALENDAR,
            Capability.NOTIFIER,
            Capability.APPROVAL,
        }
        assert wecom.capabilities() == {
            Capability.TABLE_STORE,
            Capability.CALENDAR,
            Capability.NOTIFIER,
        }
        assert set(dingtalk.capabilities()) <= set(dingtalk.manifest.capabilities)
        assert set(wecom.capabilities()) <= set(wecom.manifest.capabilities)

        # 清除可选的 process_code 后运行时不再声明审批能力。
        store.save("dingtalk", {"process_code": None})
        db.commit()
        assert dingtalk.capabilities() == {
            Capability.TABLE_STORE,
            Capability.CALENDAR,
            Capability.NOTIFIER,
        }


def test_dingtalk_token_is_cached_across_clients() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1.0/oauth2/accessToken":
            return httpx.Response(200, json={"accessToken": "dt-token", "expireIn": 7200})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    first = DingTalkClient(
        DingTalkConfig(app_key="dk-cache", app_secret="s1"), transport=transport
    )
    second = DingTalkClient(
        DingTalkConfig(app_key="dk-cache", app_secret="s1"), transport=transport
    )
    assert asyncio.run(first.access_token()) == "dt-token"
    assert asyncio.run(second.access_token()) == "dt-token"
    assert len(calls) == 1
    assert json.loads(calls[0].content) == {"appKey": "dk-cache", "appSecret": "s1"}

    clear_token_cache("dingtalk")
    assert asyncio.run(second.access_token()) == "dt-token"
    assert len(calls) == 2


def test_wecom_token_is_cached() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(
                200, json={"errcode": 0, "access_token": "wc-token", "expires_in": 7200}
            )
        return httpx.Response(200, json={"errcode": 0})

    client = WeComClient(
        WeComConfig(corp_id="corp", corp_secret="wc-sec", agent_id=1),
        transport=httpx.MockTransport(handler),
    )
    assert asyncio.run(client.access_token()) == "wc-token"
    assert asyncio.run(client.access_token()) == "wc-token"
    token_calls = [request for request in calls if request.url.path == "/cgi-bin/gettoken"]
    assert len(token_calls) == 1
    assert token_calls[0].url.params["corpid"] == "corp"
    assert token_calls[0].url.params["corpsecret"] == "wc-sec"

    clear_token_cache("wecom")
    assert asyncio.run(client.access_token()) == "wc-token"
    assert len(calls) == 2


def test_dingtalk_upsert_splits_into_batches() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1.0/oauth2/accessToken":
            return httpx.Response(200, json={"accessToken": "dt-token", "expireIn": 7200})
        return httpx.Response(200, json={"records": []})

    client = DingTalkClient(
        DingTalkConfig(app_key="dk-batch", app_secret="s"),
        transport=httpx.MockTransport(handler),
    )
    rows: list[dict[str, Any]] = [
        {"fields": {"标题": f"row-{index}"}} for index in range(RECORD_BATCH_SIZE * 2 + 7)
    ]
    rows.extend({"id": str(index), "fields": {"标题": "upd"}} for index in range(5))
    result = asyncio.run(client.upsert_records("base-1", "排课表", rows))
    assert result == {"created": RECORD_BATCH_SIZE * 2 + 7, "updated": 5}

    create_sizes = [
        len(json.loads(request.content)["records"])
        for request in calls
        if request.method == "POST" and request.url.path.endswith("/records")
    ]
    update_sizes = [
        len(json.loads(request.content)["records"])
        for request in calls
        if request.method == "PUT"
    ]
    assert create_sizes == [RECORD_BATCH_SIZE, RECORD_BATCH_SIZE, 7]
    assert update_sizes == [5]
    update_call = next(request for request in calls if request.method == "PUT")
    assert update_call.headers["x-acs-dingtalk-access-token"] == "dt-token"
    assert "/notable/bases/base-1/sheets/" in str(update_call.url)


def test_dingtalk_work_notification_chunks_to_100() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1.0/oauth2/accessToken":
            return httpx.Response(200, json={"accessToken": "dt-token", "expireIn": 7200})
        return httpx.Response(200, json={"errcode": 0, "result": {"task_id": 2077}})

    client = DingTalkClient(
        DingTalkConfig(app_key="dk-notify", app_secret="s"),
        transport=httpx.MockTransport(handler),
    )
    user_ids = [f"user{index:03d}" for index in range(250)]
    task_ids = asyncio.run(
        client.send_work_notification(agent_id="2077", user_ids=user_ids, content="课表已发布")
    )
    assert task_ids == ["2077", "2077", "2077"]
    notify_calls = [
        request
        for request in calls
        if request.url.path == "/topapi/message/corpconversation/asyncsend_v2"
    ]
    assert len(notify_calls) == 3
    receiver_counts = [
        len(json.loads(request.content)["userid_list"].split(",")) for request in notify_calls
    ]
    assert receiver_counts == [100, 100, 50]
    body = json.loads(notify_calls[0].content)
    assert body["agent_id"] == "2077"
    assert body["msg"] == {"msgtype": "text", "text": {"content": "课表已发布"}}
    # 旧版 topapi 以查询参数携带 access_token。
    assert notify_calls[0].url.params["access_token"] == "dt-token"


def test_wecom_message_chunks_to_1000() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(
                200, json={"errcode": 0, "access_token": "wc-token", "expires_in": 7200}
            )
        return httpx.Response(200, json={"errcode": 0})

    client = WeComClient(
        WeComConfig(corp_id="corp", corp_secret="s", agent_id=1000002),
        transport=httpx.MockTransport(handler),
    )
    user_ids = [f"user{index:05d}" for index in range(WECOM_NOTIFY_MAX_RECEIVERS + 250)]
    invalid = asyncio.run(client.send_message(user_ids, "调课通知"))
    assert invalid == []
    send_calls = [request for request in calls if request.url.path == "/cgi-bin/message/send"]
    assert len(send_calls) == 2
    first_body = json.loads(send_calls[0].content)
    second_body = json.loads(send_calls[1].content)
    assert len(first_body["touser"].split("|")) == WECOM_NOTIFY_MAX_RECEIVERS
    assert len(second_body["touser"].split("|")) == 250
    assert first_body["msgtype"] == "text"
    assert first_body["agentid"] == 1000002
    assert first_body["text"] == {"content": "调课通知"}
    assert send_calls[0].url.params["access_token"] == "wc-token"


def test_wecom_schedule_uses_app_owned_calendar() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(
                200, json={"errcode": 0, "access_token": "wc-token", "expires_in": 7200}
            )
        return httpx.Response(200, json={"errcode": 0, "schedule_id": "sch-1"})

    client = WeComClient(
        WeComConfig(corp_id="corp", corp_secret="s", agent_id=1000003),
        transport=httpx.MockTransport(handler),
    )
    schedule_id = asyncio.run(
        client.create_schedule(
            title="高一（1）班 数学",
            start_time=1780000000,
            end_time=1780010000,
            attendee_ids=("teacher_a",),
        )
    )
    assert schedule_id == "sch-1"
    add_call = next(request for request in calls if request.url.path == "/cgi-bin/oa/schedule/add")
    body = json.loads(add_call.content)
    # 企业微信限制：日程只能落在应用自建日历下，请求里没有也不允许指定日历 ID。
    assert "calendar_id" not in body
    assert body["agentid"] == 1000003
    assert body["schedule"]["attendees"] == [{"userid": "teacher_a"}]
    assert add_call.url.params["access_token"] == "wc-token"


def test_configuration_endpoint_encrypts_and_masks(
    client: TestClient, auth_headers: dict[str, str], cipher_key: str
) -> None:
    secret = "dingtalk-secret-2026!"
    store_token("dingtalk", "dk-app", "stale-token", 7200)

    response = client.put(
        "/api/v1/integrations/dingtalk/configuration",
        headers=auth_headers,
        json={
            "config": {
                "app_key": "dk-app",
                "app_secret": secret,
                "base_url": "https://api.dingtalk.com",
                "process_code": "PROC-100",
            }
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["integration_id"] == "dingtalk"
    assert body["configured"] is True
    assert body["config"]["app_key"] == "dk-app"
    assert body["config"]["process_code"] == "PROC-100"
    assert body["secrets_configured"] == {"app_secret": True}
    assert secret not in json.dumps(body)
    # 保存后旧 access_token 立即失效。
    assert cached_token("dingtalk", "dk-app") is None

    with SessionLocal() as db:
        row = db.get(IntegrationCredential, "dingtalk")
        assert row is not None
        assert row.config["app_key"] == "dk-app"
        # 落库的是 Fernet 密文，明文不出现在任何列。
        assert secret not in row.secrets_encrypted
        stored = CredentialStore(get_settings(), db).load("dingtalk")
        assert stored is not None
        assert stored.merged()["app_secret"] == secret

    # 密钥留空 = 保持已存值；非密钥字段可显式置 null 清除。
    keep = client.put(
        "/api/v1/integrations/dingtalk/configuration",
        headers=auth_headers,
        json={"config": {"app_secret": "", "process_code": None}},
    )
    assert keep.status_code == 200
    kept = keep.json()
    assert kept["secrets_configured"] == {"app_secret": True}
    assert "process_code" not in kept["config"]
    # 表/日历/通知仍协商为可用，审批随 process_code 清除退出。
    assert kept["configured"] is True

    fetched = client.get(
        "/api/v1/integrations/dingtalk/configuration", headers=auth_headers
    )
    assert fetched.status_code == 200
    payload = fetched.json()
    assert payload["configured"] is True
    assert "app_secret" not in payload["config"]
    assert payload["secrets_configured"] == {"app_secret": True}
    assert secret not in fetched.text


def test_configuration_endpoint_guards_access(
    client: TestClient, auth_headers: dict[str, str], cipher_key: str
) -> None:
    assert (
        client.put(
            "/api/v1/integrations/dingtalk/configuration", json={"config": {}}
        ).status_code
        == 401
    )
    assert (
        client.get("/api/v1/integrations/wecom/configuration").status_code == 401
    )

    username = f"viewer_{uuid4().hex[:8]}"
    created = client.post(
        "/api/v1/users",
        headers=auth_headers,
        json={"username": username, "password": "viewer-pass-2026"},
    )
    assert created.status_code == 201
    viewer_id = created.json()["id"]
    login = client.post(
        "/api/v1/auth/token", data={"username": username, "password": "viewer-pass-2026"}
    )
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (
        client.put(
            "/api/v1/integrations/dingtalk/configuration",
            headers=viewer_headers,
            json={"config": {}},
        ).status_code
        == 403
    )

    # 排课员可读脱敏配置，但保存仍仅限管理员。
    promoted = client.patch(
        f"/api/v1/users/{viewer_id}/role", headers=auth_headers, json={"role": "scheduler"}
    )
    assert promoted.status_code == 200
    saved = client.put(
        "/api/v1/integrations/wecom/configuration",
        headers=auth_headers,
        json={"config": {"corp_id": "corp", "corp_secret": "wc", "agent_id": 1}},
    )
    assert saved.status_code == 200
    assert client.get(
        "/api/v1/integrations/wecom/configuration", headers=viewer_headers
    ).status_code == 200
    assert (
        client.put(
            "/api/v1/integrations/wecom/configuration",
            headers=viewer_headers,
            json={"config": {}},
        ).status_code
        == 403
    )

    # 未声明 config schema 的集成（local/feishu）与未知 id、planned 集成一律 404。
    for integration_id in ("google_workspace", "local", "feishu", "nope"):
        assert (
            client.put(
                f"/api/v1/integrations/{integration_id}/configuration",
                headers=auth_headers,
                json={"config": {}},
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/v1/integrations/{integration_id}/configuration",
                headers=auth_headers,
            ).status_code
            == 404
        )


def test_dingtalk_verify_probe_softly_reports_failures(cipher_key: str) -> None:
    with SessionLocal() as db:
        settings = get_settings()
        adapter = registry.get_integration("dingtalk", settings, db)
        assert adapter is not None
        unconfigured = asyncio.run(adapter.verify())
        assert unconfigured.ok is False
        assert unconfigured.detail == DINGTALK_UNCONFIGURED_DETAIL

        CredentialStore(settings, db).save(
            "dingtalk", {"app_key": "dk-verify", "app_secret": "sv"}
        )
        db.commit()

        def ok_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1.0/oauth2/accessToken":
                return httpx.Response(200, json={"accessToken": "tk", "expireIn": 7200})
            return httpx.Response(200, json={})

        ok_adapter = DingTalkAdapter(settings, db, transport=httpx.MockTransport(ok_handler))
        success = asyncio.run(ok_adapter.verify())
        assert success.ok is True
        assert "access_token" in success.detail

        def bad_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"code": "InvalidAuthentication", "message": "appSecret 不正确"},
            )

        clear_token_cache("dingtalk")
        bad_adapter = DingTalkAdapter(settings, db, transport=httpx.MockTransport(bad_handler))
        failure = asyncio.run(bad_adapter.verify())
        assert failure.ok is False
        assert "InvalidAuthentication" in failure.detail


def test_wecom_verify_probe_softly_reports_failures(cipher_key: str) -> None:
    with SessionLocal() as db:
        settings = get_settings()
        adapter = registry.get_integration("wecom", settings, db)
        assert adapter is not None
        unconfigured = asyncio.run(adapter.verify())
        assert unconfigured.ok is False
        assert unconfigured.detail == WECOM_UNCONFIGURED_DETAIL

        CredentialStore(settings, db).save(
            "wecom", {"corp_id": "corp", "corp_secret": "sv", "agent_id": 1}
        )
        db.commit()

        def bad_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"errcode": 40001, "errmsg": "invalid credential"})

        bad_adapter = WeComAdapter(settings, db, transport=httpx.MockTransport(bad_handler))
        failure = asyncio.run(bad_adapter.verify())
        assert failure.ok is False
        assert "40001" in failure.detail

        def ok_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/cgi-bin/gettoken":
                return httpx.Response(
                    200, json={"errcode": 0, "access_token": "tk", "expires_in": 7200}
                )
            return httpx.Response(200, json={"errcode": 0})

        ok_adapter = WeComAdapter(settings, db, transport=httpx.MockTransport(ok_handler))
        success = asyncio.run(ok_adapter.verify())
        assert success.ok is True
        assert "access_token" in success.detail


def test_verify_endpoint_soft_fails_on_bad_dingtalk_credentials(
    client: TestClient,
    auth_headers: dict[str, str],
    cipher_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """verify 端点经真实 registry 实例化适配器：凭据错误 200 + ok=False 软失败。"""

    with SessionLocal() as db:
        CredentialStore(get_settings(), db).save(
            "dingtalk", {"app_key": "dk-endpoint", "app_secret": "sv"}
        )
        db.commit()

    real_client = DingTalkClient

    def patched_client(config: DingTalkConfig, transport: Any = None) -> DingTalkClient:
        def bad_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"code": "InvalidAuthentication", "message": "appSecret 不正确"},
            )

        return real_client(config, transport=httpx.MockTransport(bad_handler))

    monkeypatch.setattr(dingtalk_adapter, "DingTalkClient", patched_client)

    response = client.post("/api/v1/integrations/dingtalk/verify", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "InvalidAuthentication" in body["detail"]
