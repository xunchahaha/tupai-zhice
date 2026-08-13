from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.api import settings
from app.config import FEISHU_REQUIRED_SCOPES
from app.db import SessionLocal
from app.models import (
    FeishuAppConfiguration,
    FeishuConnection,
    FeishuOAuthState,
    FeishuRecordBinding,
    User,
)
from app.services.feishu import TABLE_SCHEMAS, FeishuService, FeishuServiceError, TokenCipher


def response(
    method: str,
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        headers=headers,
        request=httpx.Request(method, url),
    )


def configure_feishu(monkeypatch: Any) -> None:
    monkeypatch.setattr(settings, "feishu_app_id", "cli_test")
    monkeypatch.setattr(settings, "feishu_app_secret", "test-secret")
    monkeypatch.setattr(
        settings,
        "feishu_token_encryption_key",
        Fernet.generate_key().decode("ascii"),
    )
    monkeypatch.setattr(
        settings,
        "feishu_oauth_redirect_uri",
        "http://testserver/api/v1/integrations/feishu/oauth/callback",
    )
    monkeypatch.setattr(settings, "frontend_url", "http://frontend.test")


def start_authorization(
    client: TestClient,
    auth_headers: dict[str, str],
) -> tuple[str, dict[str, list[str]]]:
    started = client.post(
        "/api/v1/integrations/feishu/oauth/start",
        headers=auth_headers,
    )
    assert started.status_code == 200
    authorization_url = started.json()["authorization_url"]
    query = parse_qs(urlparse(authorization_url).query)
    return query["state"][0], query


def complete_authorization(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
    *,
    expires_in: int = 7200,
) -> str:
    state, query = start_authorization(client, auth_headers)
    assert "code_challenge" not in query
    assert "code_challenge_method" not in query
    assert "offline_access" in query["scope"][0]
    assert set(FEISHU_REQUIRED_SCOPES) == set(query["scope"][0].split())
    assert {
        "calendar:calendar.event:create",
        "calendar:calendar.event:update",
        "calendar:calendar.free_busy:read",
    } <= set(query["scope"][0].split())

    def token_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        body = kwargs["json"]
        assert body["grant_type"] == "authorization_code"
        assert body["client_id"] == "cli_test"
        assert "code_verifier" not in body
        return response(
            method,
            url,
            {
                "code": 0,
                "access_token": "user-access-1",
                "refresh_token": "user-refresh-1",
                "expires_in": expires_in,
                "refresh_expires_in": 2_592_000,
                "scope": query["scope"][0],
            },
        )

    monkeypatch.setattr("app.services.feishu.httpx.request", token_request)
    callback = client.get(
        "/api/v1/integrations/feishu/oauth/callback",
        params={"code": "oauth-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code in {302, 307}
    assert callback.headers["location"] == "http://frontend.test/integrations?feishu=connected"
    return state


def test_start_aily_skill_decodes_workflow_output(monkeypatch: Any) -> None:
    service = object.__new__(FeishuService)
    connection = type("Connection", (), {"scopes": ["aily:skill:write"]})()
    monkeypatch.setattr(service, "access_token", lambda user_id: (connection, "token"))
    monkeypatch.setattr(service, "_require_scopes", lambda connection, scopes: None)

    def request(method: str, url: str, **kwargs: Any) -> tuple[dict[str, Any], None]:
        assert url.endswith("/aily/v1/apps/spring_demo__c/skills/skill_demo/start")
        assert kwargs["json_body"]["query"] == "安排考研班"
        return {"output": '{"business_lines":["考研"],"date_window_days":2}'}, None

    monkeypatch.setattr(service, "_request", request)
    result = service.start_aily_skill(
        "user",
        app_id="spring_demo__c",
        skill_id="skill_demo",
        query="安排考研班",
    )
    assert result["business_lines"] == ["考研"]


def test_admin_configures_app_from_frontend_and_secret_is_encrypted(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    monkeypatch.setattr(settings, "feishu_app_id", "")
    monkeypatch.setattr(settings, "feishu_app_secret", "")
    monkeypatch.setattr(settings, "feishu_token_encryption_key", "")
    key_file = tmp_path / "feishu.key"
    monkeypatch.setattr(settings, "feishu_token_key_file", key_file)
    configured = client.post(
        "/api/v1/integrations/feishu/app-configuration",
        headers=auth_headers,
        json={
            "app_id": "cli_frontend_test",
            "app_secret": "frontend-secret-value",
            "oauth_redirect_uri": ("http://testserver/api/v1/integrations/feishu/oauth/callback"),
            "frontend_url": "http://frontend.test",
            "aily_app_id": "spring_frontend_test",
            "aily_skill_id": "skill_frontend_test",
        },
    )
    assert configured.status_code == 200, configured.text
    assert configured.json() == {
        "configured": True,
        "source": "frontend",
        "app_id": "cli_frontend_test",
        "secret_configured": True,
        "oauth_redirect_uri": "http://testserver/api/v1/integrations/feishu/oauth/callback",
        "frontend_url": "http://frontend.test",
        "aily_configured": True,
        "aily_app_id": "spring_frontend_test",
        "aily_skill_id": "skill_frontend_test",
    }
    assert "frontend-secret-value" not in configured.text
    assert key_file.exists()

    started = client.post(
        "/api/v1/integrations/feishu/oauth/start",
        headers=auth_headers,
    )
    assert started.status_code == 200
    query = parse_qs(urlparse(started.json()["authorization_url"]).query)
    assert query["client_id"] == ["cli_frontend_test"]
    assert query["response_type"] == ["code"]
    with SessionLocal() as db:
        stored = db.get(FeishuAppConfiguration, "default")
        assert stored is not None
        assert stored.app_secret_encrypted != "frontend-secret-value"
        cipher = TokenCipher(key_file.read_text(encoding="ascii").strip())
        assert cipher.decrypt(stored.app_secret_encrypted) == "frontend-secret-value"
        assert stored.aily_app_id == "spring_frontend_test"
        assert stored.aily_skill_id == "skill_frontend_test"
        db.execute(delete(FeishuOAuthState))
        db.delete(stored)
        db.commit()


def test_oauth_pkce_encrypts_tokens_and_rejects_replay(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    configure_feishu(monkeypatch)
    state = complete_authorization(client, auth_headers, monkeypatch)

    with SessionLocal() as db:
        oauth_state = db.scalar(
            select(FeishuOAuthState).where(
                FeishuOAuthState.state_hash == FeishuService._state_hash(state)
            )
        )
        connection = db.scalar(select(FeishuConnection))
        assert oauth_state is not None and oauth_state.used_at is not None
        assert connection is not None
        assert connection.access_token_encrypted != "user-access-1"
        assert connection.refresh_token_encrypted != "user-refresh-1"
        cipher = TokenCipher(settings.feishu_token_encryption_key)
        assert cipher.decrypt(connection.access_token_encrypted) == "user-access-1"

    status = client.get("/api/v1/integrations/feishu/connection", headers=auth_headers)
    assert status.status_code == 200
    assert status.json()["status"] == "connected"
    assert status.json()["missing_scopes"] == []

    replay = client.get(
        "/api/v1/integrations/feishu/oauth/callback",
        params={"code": "oauth-code", "state": state},
        follow_redirects=False,
    )
    assert replay.headers["location"] == "http://frontend.test/integrations?feishu=error"


def test_refresh_rotates_refresh_token_atomically(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    configure_feishu(monkeypatch)
    complete_authorization(client, auth_headers, monkeypatch, expires_in=1)
    with SessionLocal() as db:
        connection = db.scalar(select(FeishuConnection))
        assert connection is not None
        connection.access_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

    def refresh_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        body = kwargs["json"]
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "user-refresh-1"
        return response(
            method,
            url,
            {
                "code": 0,
                "access_token": "user-access-2",
                "refresh_token": "user-refresh-2",
                "expires_in": 7200,
                "refresh_expires_in": 2_592_000,
                "scope": "offline_access base:app:create",
            },
        )

    monkeypatch.setattr("app.services.feishu.httpx.request", refresh_request)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        assert admin is not None
        _, token = FeishuService(settings, db).access_token(admin.id)
        connection = db.scalar(select(FeishuConnection).where(FeishuConnection.user_id == admin.id))
        assert connection is not None
        cipher = TokenCipher(settings.feishu_token_encryption_key)
        assert token == "user-access-2"
        assert cipher.decrypt(connection.refresh_token_encrypted) == "user-refresh-2"


def test_auto_create_workspace_and_sync_idempotently(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    configure_feishu(monkeypatch)
    complete_authorization(client, auth_headers, monkeypatch)
    created_tables: list[dict[str, Any]] = []
    remote_records: dict[str, list[dict[str, Any]]] = {}
    app_create_count = 0

    def feishu_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        nonlocal app_create_count
        body = kwargs.get("json") or {}
        if method == "POST" and url.endswith("/bitable/v1/apps"):
            app_create_count += 1
            return response(
                method,
                url,
                {
                    "code": 0,
                    "data": {
                        "app": {
                            "app_token": "base-test",
                            "default_table_id": "tbl-default",
                            "folder_token": "fld-test",
                            "url": "https://example.feishu.cn/base/base-test",
                        }
                    },
                },
            )
        if method == "PATCH" and "/tables/tbl-default" in url:
            assert body == {"name": "接入说明"}
            return response(method, url, {"code": 0, "data": {}})
        if method == "POST" and url.endswith("/tables"):
            created_tables.append(body["table"])
            table_id = f"tbl-{len(created_tables)}"
            remote_records[table_id] = []
            return response(
                method,
                url,
                {"code": 0, "data": {"table": {"table_id": table_id}}},
            )
        table_id = next(
            (part for part in url.split("/") if part.startswith("tbl-") and part != "tbl-default"),
            "",
        )
        if method == "POST" and url.endswith("/records/search"):
            assert body == {"field_names": ["业务标识"]}
            return response(
                method,
                url,
                {"code": 0, "data": {"items": remote_records[table_id], "has_more": False}},
                headers={"x-tt-logid": "log-list"},
            )
        if method == "POST" and url.endswith("/records/batch_create"):
            created: list[dict[str, Any]] = []
            for item in body["records"]:
                record_id = f"rec-{len(remote_records[table_id]) + 1}"
                record = {"record_id": record_id, "fields": item["fields"]}
                remote_records[table_id].append(record)
                created.append(record)
            return response(
                method,
                url,
                {"code": 0, "data": {"records": created}},
                headers={"x-tt-logid": "log-create"},
            )
        if method == "POST" and url.endswith("/records/batch_update"):
            indexed = {item["record_id"]: item for item in remote_records[table_id]}
            for item in body["records"]:
                indexed[item["record_id"]]["fields"] = item["fields"]
            return response(
                method,
                url,
                {"code": 0, "data": {"records": body["records"]}},
                headers={"x-tt-logid": "log-update"},
            )
        raise AssertionError(f"unexpected Feishu request: {method} {url}")

    monkeypatch.setattr("app.services.feishu.httpx.request", feishu_request)
    workspace = client.post(
        "/api/v1/integrations/feishu/workspaces",
        headers=auth_headers,
        json={"name": "途排智策 - 示范校 - 2026 秋"},
    )
    assert workspace.status_code == 201, workspace.text
    assert workspace.json()["status"] == "active"
    assert len(workspace.json()["tables"]) == len(TABLE_SCHEMAS)
    assert [table["name"] for table in created_tables] == [
        "教师",
        "班级",
        "教室",
        "时段",
        "课程场次",
        "规则",
        "课表",
        "公开展示汇总",
    ]
    assert all(table["fields"][0]["field_name"] == "业务标识" for table in created_tables)

    repeated_workspace = client.post(
        "/api/v1/integrations/feishu/workspaces",
        headers=auth_headers,
        json={"name": "途排智策 - 示范校 - 2026 秋"},
    )
    assert repeated_workspace.status_code == 201
    assert app_create_count == 1

    first = client.post(
        "/api/v1/integrations/feishu/sync",
        headers=auth_headers,
        json={"resource": "teachers"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["records_written"] == 6
    assert first.json()["detail"]["records_created"] == 6

    second = client.post(
        "/api/v1/integrations/feishu/sync",
        headers=auth_headers,
        json={"resource": "teachers"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["detail"]["records_created"] == 0
    assert second.json()["detail"]["records_updated"] == 6
    assert len(remote_records["tbl-1"]) == 6
    with SessionLocal() as db:
        assert db.scalar(select(func.count(FeishuRecordBinding.id))) == 6


def test_calendar_table_schemas_include_binding_and_fixed_time_fields() -> None:
    teacher_fields = {name for name, _ in TABLE_SCHEMAS["teachers"][1]}
    session_fields = {name for name, _ in TABLE_SCHEMAS["course_sessions"][1]}
    schedule_fields = {name for name, _ in TABLE_SCHEMAS["schedule"][1]}

    assert "飞书用户标识" in teacher_fields
    assert {
        "业务线",
        "产品班型",
        "固定开始时间",
        "固定结束时间",
        "原始教室标识",
        "具体日程账号",
    } <= session_fields
    assert {"上课日期", "固定开始时间", "固定结束时间"} <= schedule_fields


def test_calendar_requests_use_user_token_and_official_payloads(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []
    service = FeishuService(settings, object())  # type: ignore[arg-type]
    connection = FeishuConnection(
        user_id="admin-user",
        access_token_encrypted="unused",
        refresh_token_encrypted="unused",
        access_expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=list(FEISHU_REQUIRED_SCOPES),
    )
    monkeypatch.setattr(service, "access_token", lambda user_id: (connection, "calendar-token"))

    def calendar_request(method: str, url: str, **kwargs: Any) -> tuple[dict[str, Any], str]:
        calls.append({"method": method, "url": url, **kwargs})
        if url.endswith("/freebusy/batch"):
            return {"freebusy_lists": []}, "log-freebusy"
        if url.endswith("/attendees"):
            return {"attendees": [{"attendee_id": "attendee-1"}]}, "log-attendee"
        return {"event": {"event_id": "event-1"}}, "log-event"

    monkeypatch.setattr(service, "_request", calendar_request)

    freebusy = service.batch_freebusy(
        "admin-user",
        user_ids=["ou_teacher_1", "ou_teacher_2"],
        time_min="2026-08-17T08:00:00+08:00",
        time_max="2026-08-24T18:00:00+08:00",
    )
    event = service.create_calendar_event(
        "admin-user",
        calendar_id="feishu.cn_teacher/calendar@primary",
        idempotency_key="schedule-assignment-000000000000001",
        event={
            "summary": "课程安排",
            "start_time": {"timestamp": "1786928400", "timezone": "Asia/Shanghai"},
            "end_time": {"timestamp": "1786939200", "timezone": "Asia/Shanghai"},
            "free_busy_status": "busy",
        },
    )
    attendee = service.add_event_attendee(
        "admin-user",
        calendar_id="feishu.cn_teacher/calendar@primary",
        event_id="event/1",
        attendee_user_id="ou_teacher_1",
    )

    assert freebusy == {"freebusy_lists": []}
    assert event["event"]["event_id"] == "event-1"
    assert attendee["attendees"][0]["attendee_id"] == "attendee-1"
    assert calls[0] == {
        "method": "POST",
        "url": "https://open.feishu.cn/open-apis/calendar/v4/freebusy/batch",
        "token": "calendar-token",
        "params": {"user_id_type": "open_id"},
        "json_body": {
            "time_min": "2026-08-17T08:00:00+08:00",
            "time_max": "2026-08-24T18:00:00+08:00",
            "user_ids": ["ou_teacher_1", "ou_teacher_2"],
            "include_external_calendar": True,
            "only_busy": True,
            "need_rsvp_status": True,
        },
    }
    assert calls[1]["url"].endswith(
        "/calendar/v4/calendars/feishu.cn_teacher%2Fcalendar%40primary/events"
    )
    assert calls[1]["params"] == {
        "user_id_type": "open_id",
        "idempotency_key": "schedule-assignment-000000000000001",
    }
    assert calls[1]["json_body"]["free_busy_status"] == "busy"
    assert calls[2]["url"].endswith(
        "/calendar/v4/calendars/feishu.cn_teacher%2Fcalendar%40primary/events/"
        "event%2F1/attendees"
    )
    assert calls[2]["json_body"] == {
        "attendees": [
            {"type": "user", "user_id": "ou_teacher_1", "is_optional": False}
        ],
        "need_notification": True,
    }


def test_batch_freebusy_rejects_oversized_user_and_time_windows(monkeypatch: Any) -> None:
    service = FeishuService(settings, object())  # type: ignore[arg-type]
    monkeypatch.setattr(
        service,
        "access_token",
        lambda user_id: (_ for _ in ()).throw(AssertionError("validation must run first")),
    )

    with pytest.raises(FeishuServiceError, match="1 至 10"):
        service.batch_freebusy(
            "admin-user",
            user_ids=[f"ou_{index}" for index in range(11)],
            time_min="2026-08-01T00:00:00+08:00",
            time_max="2026-08-02T00:00:00+08:00",
        )
    with pytest.raises(FeishuServiceError, match="不能超过两周"):
        service.batch_freebusy(
            "admin-user",
            user_ids=["ou_teacher"],
            time_min="2026-08-01T00:00:00+08:00",
            time_max="2026-08-16T00:00:00+08:00",
        )
