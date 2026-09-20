"""集成抽象层 v1：registry、manifest 完整性、清单端点、适配器与日历接缝。"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.config import get_settings
from app.db import SessionLocal
from app.integrations import registry
from app.integrations.base import Capability
from app.integrations.dingtalk import DingTalkAdapter
from app.integrations.feishu.adapter import UNCONFIGURED_DETAIL, FeishuAdapter
from app.integrations.local import LocalAdapter
from app.integrations.wecom import WeComAdapter
from app.models import (
    Campus,
    CourseSession,
    DataSnapshot,
    FeishuAppConfiguration,
    FeishuConnection,
    Room,
    ScheduleAssignment,
    ScheduleVersion,
    SolverRun,
    TimeSlot,
)
from app.services.feishu import FeishuServiceError

ADAPTER_IDS = {"local", "feishu", "dingtalk", "wecom"}
PLANNED_IDS = {"google_workspace"}


def _clear_feishu_app_configuration() -> None:
    """测试自管飞书配置状态（与 test_feishu.py 同一约定：自建自清理）。

    除应用配置外一并清理用户授权连接：test_feishu.py 的 OAuth 用例会以种子
    管理员身份留下 status="active" 的连接，其令牌密文由该用例经 monkeypatch
    注入的一次性随机 Fernet 主密钥加密（用例结束即失效）。若不清理，日历
    下发会命中该连接并解密失败，而非走到本文件断言的「尚未授权」分支。
    """

    with SessionLocal() as db:
        db.execute(delete(FeishuAppConfiguration))
        db.execute(delete(FeishuConnection))
        db.commit()


def _configure_feishu_app(client: TestClient, auth_headers: dict[str, str]) -> None:
    """走公开 API 保存应用配置（密文由服务层真实加密，同时确保 seed 已执行）。"""

    response = client.post(
        "/api/v1/integrations/feishu/app-configuration",
        headers=auth_headers,
        json={
            "app_id": "cli_integration_test",
            "app_secret": "integration-test-secret",
            "oauth_redirect_uri": (
                "https://feishu.example.com/api/v1/integrations/feishu/oauth/callback"
            ),
            "frontend_url": "https://feishu.example.com",
        },
    )
    assert response.status_code == 200, response.text


def test_registry_lists_builtin_integrations() -> None:
    entries = registry.entries()
    ids = [entry.manifest.id for entry in entries]
    assert set(ids) == ADAPTER_IDS | PLANNED_IDS
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    for entry in entries:
        if entry.manifest.id in ADAPTER_IDS:
            assert entry.adapter is not None
        else:
            assert entry.adapter is None
            assert entry.manifest.status_class == "planned"
    with SessionLocal() as db:
        settings = get_settings()
        assert isinstance(registry.get_integration("local", settings, db), LocalAdapter)
        assert isinstance(registry.get_integration("feishu", settings, db), FeishuAdapter)
        assert isinstance(registry.get_integration("dingtalk", settings, db), DingTalkAdapter)
        assert isinstance(registry.get_integration("wecom", settings, db), WeComAdapter)
        assert registry.get_integration("missing", settings, db) is None


def test_manifests_are_complete() -> None:
    by_id = {manifest.id: manifest for manifest in registry.manifests()}
    assert set(by_id) == ADAPTER_IDS | PLANNED_IDS
    for manifest in by_id.values():
        assert manifest.id
        assert manifest.name
        assert manifest.description
        assert manifest.capabilities
        assert manifest.capabilities <= frozenset(Capability)
        assert manifest.status_class in ("available", "configured", "planned")
        assert manifest.docs_url
    assert by_id["local"].status_class == "configured"
    assert by_id["local"].capabilities == frozenset(
        {Capability.TABLE_STORE, Capability.NOTIFIER}
    )
    assert by_id["feishu"].status_class == "available"
    assert by_id["feishu"].capabilities == frozenset(Capability)


def test_local_adapter_is_always_configured() -> None:
    with SessionLocal() as db:
        adapter = LocalAdapter(settings=get_settings(), db=db)
        assert adapter.capabilities() == {Capability.TABLE_STORE, Capability.NOTIFIER}
        result = asyncio.run(adapter.verify())
    assert result.ok is True
    assert result.detail


def test_feishu_adapter_degrades_when_unconfigured() -> None:
    _clear_feishu_app_configuration()
    with SessionLocal() as db:
        adapter = FeishuAdapter(settings=get_settings(), db=db)
        assert adapter.capabilities() == set()
        result = asyncio.run(adapter.verify())
    assert result.ok is False
    assert result.detail == UNCONFIGURED_DETAIL


def test_feishu_adapter_negotiates_capabilities_when_configured(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _clear_feishu_app_configuration()
    _configure_feishu_app(client, auth_headers)
    try:
        with SessionLocal() as db:
            adapter = FeishuAdapter(settings=get_settings(), db=db)
            assert adapter.capabilities() == set(Capability)
            result = asyncio.run(adapter.verify())
            assert result.ok is True
    finally:
        _clear_feishu_app_configuration()


def test_list_integrations_requires_admin_or_scheduler(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    assert client.get("/api/v1/integrations").status_code == 401
    username = f"viewer_{uuid4().hex[:8]}"
    created = client.post(
        "/api/v1/users",
        headers=auth_headers,
        json={"username": username, "password": "viewer-pass-2026"},
    )
    assert created.status_code == 201
    login = client.post(
        "/api/v1/auth/token", data={"username": username, "password": "viewer-pass-2026"}
    )
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/api/v1/integrations", headers=viewer_headers).status_code == 403

    promoted = client.patch(
        f"/api/v1/users/{created.json()['id']}/role",
        headers=auth_headers,
        json={"role": "scheduler"},
    )
    assert promoted.status_code == 200
    assert client.get("/api/v1/integrations", headers=viewer_headers).status_code == 200
    assert client.get("/api/v1/integrations", headers=auth_headers).status_code == 200


def test_list_integrations_content(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _clear_feishu_app_configuration()
    response = client.get("/api/v1/integrations", headers=auth_headers)
    assert response.status_code == 200
    items = {item["id"]: item for item in response.json()}
    assert set(items) == ADAPTER_IDS | PLANNED_IDS
    assert items["local"]["status"] == "configured"
    assert items["local"]["capabilities"] == ["notifier", "table_store"]
    # 未配置飞书应用时清单回落到 manifest 声明的 available。
    assert items["feishu"]["status"] == "available"
    assert items["feishu"]["capabilities"] == sorted(
        capability.value for capability in Capability
    )
    for planned in PLANNED_IDS:
        assert items[planned]["status"] == "planned"
        assert items[planned]["capabilities"]
        assert items[planned]["docs_url"]
    # 钉钉/企业微信已是适配器：未配置凭据时清单回落到 manifest 的 available。
    assert items["dingtalk"]["status"] == "available"
    assert items["dingtalk"]["capabilities"] == ["approval", "calendar", "notifier", "table_store"]
    assert items["wecom"]["status"] == "available"
    assert items["wecom"]["capabilities"] == ["calendar", "notifier", "table_store"]


def _calendar_scoped_schedule(
    client: TestClient, auth_headers: dict[str, str], *, map_calendar_user: bool
) -> tuple[dict[str, str], str]:
    """复刻 test_local_workflow_regressions 的最小方案夹具，可选日历账号映射。"""

    created = client.post(
        "/api/v1/schedule-sets", headers=auth_headers, json={"name": f"集成-{uuid4().hex[:8]}"}
    )
    assert created.status_code == 201, created.text
    scope_id = created.json()["id"]
    headers = {**auth_headers, "X-Schedule-Set-Id": scope_id}
    with SessionLocal() as db:
        campus = Campus(schedule_set_id=scope_id, business_id="C", name="集成校区")
        snapshot = DataSnapshot(
            schedule_set_id=scope_id, revision=1, checksum=uuid4().hex, payload={}
        )
        db.add_all([campus, snapshot])
        db.flush()
        db.add_all(
            [
                Room(schedule_set_id=scope_id, campus_id=campus.id, business_id="R", name="教室"),
                TimeSlot(
                    schedule_set_id=scope_id,
                    campus_id=campus.id,
                    business_id="S",
                    weekday="周一",
                    start_time="08:30",
                    end_time="11:30",
                ),
            ]
        )
        run = SolverRun(
            schedule_set_id=scope_id,
            snapshot_id=snapshot.id,
            status="completed",
            request_payload={},
        )
        db.add(run)
        db.flush()
        version = ScheduleVersion(
            schedule_set_id=scope_id,
            version_no=1,
            name="日历接缝草稿",
            solver_run_id=run.id,
            status="draft",
        )
        db.add(version)
        db.flush()
        course = CourseSession(
            schedule_set_id=scope_id,
            campus_id=campus.id,
            business_id="L0",
            class_business_id="B0",
            teacher_business_id="T",
            lesson_date=date(2026, 9, 7),
            fixed_start_time="08:30",
            fixed_end_time="11:30",
            calendar_user_id="ou_integration_gate" if map_calendar_user else None,
        )
        db.add(course)
        db.flush()
        db.add(
            ScheduleAssignment(
                schedule_version_id=version.id,
                course_session_id=course.id,
                lesson_date=date(2026, 9, 7),
                slot_business_id="S",
                room_business_id="R",
            )
        )
        db.commit()
        return headers, version.id


def test_calendar_publish_unconfigured_keeps_guidance(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _clear_feishu_app_configuration()
    headers, version_id = _calendar_scoped_schedule(client, auth_headers, map_calendar_user=True)
    response = client.post(
        f"/api/v1/schedules/{version_id}/calendar-publish", headers=headers, json={"dry_run": True}
    )
    # 集成抽象层接缝：无可用的日历集成时，提示与原 FeishuServiceError 文案一致，
    # 状态码按运行约定归入 409。
    assert response.status_code == 409
    assert response.json()["detail"] == UNCONFIGURED_DETAIL


def test_calendar_publish_without_publishable_rows_stays_ok(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _clear_feishu_app_configuration()
    headers, version_id = _calendar_scoped_schedule(client, auth_headers, map_calendar_user=False)
    response = client.post(
        f"/api/v1/schedules/{version_id}/calendar-publish", headers=headers, json={"dry_run": True}
    )
    # 没有可下发课次时不触发日历门控，与原实现的 200 空结果行为一致。
    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    assert payload["would_publish"] == 0
    assert payload["skipped_unmapped"] == 1


def test_calendar_publish_with_configured_app_reaches_service_layer(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _clear_feishu_app_configuration()
    _configure_feishu_app(client, auth_headers)
    try:
        headers, version_id = _calendar_scoped_schedule(
            client, auth_headers, map_calendar_user=True
        )
        # 应用已配置 → 门控放行，请求进入原服务层；连接未授权时错误原样上抛
        # （与改造前完全一致），证明接缝只是前置协商、未改写服务层行为。
        with pytest.raises(FeishuServiceError, match="尚未授权"):
            client.post(
                f"/api/v1/schedules/{version_id}/calendar-publish",
                headers=headers,
                json={"dry_run": True},
            )
    finally:
        _clear_feishu_app_configuration()


def test_verify_endpoint_reports_local_and_unconfigured_feishu(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """统一「测试连接」端点：local 恒 ok；未配置飞书 ok=False 软失败。"""

    local = client.post("/api/v1/integrations/local/verify", headers=auth_headers)
    assert local.status_code == 200
    assert local.json()["ok"] is True
    assert local.json()["detail"]

    _clear_feishu_app_configuration()
    feishu = client.post("/api/v1/integrations/feishu/verify", headers=auth_headers)
    assert feishu.status_code == 200
    body = feishu.json()
    assert body["ok"] is False
    assert body["detail"] == UNCONFIGURED_DETAIL


def test_verify_endpoint_404s_planned_and_unknown_ids(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """planned 集成（无适配器实例）与未知 id 同形 404。"""

    forged_detail = None
    for integration_id in ("google_workspace", "nope"):
        response = client.post(
            f"/api/v1/integrations/{integration_id}/verify", headers=auth_headers
        )
        assert response.status_code == 404
        if forged_detail is None:
            forged_detail = response.json()
        else:
            assert response.json() == forged_detail
