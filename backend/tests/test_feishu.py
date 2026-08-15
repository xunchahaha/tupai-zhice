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
from app.config import FEISHU_OPTIONAL_CLEANUP_SCOPES, FEISHU_REQUIRED_SCOPES
from app.db import SessionLocal
from app.models import (
    FeishuAppConfiguration,
    FeishuConnection,
    FeishuOAuthState,
    FeishuRecordBinding,
    FeishuTableBinding,
    FeishuWorkspace,
    User,
)
from app.services.feishu import (
    TABLE_SCHEMAS,
    FeishuService,
    FeishuServiceError,
    TokenCipher,
    normalize_business_key,
)


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
    assert set(FEISHU_REQUIRED_SCOPES) | set(FEISHU_OPTIONAL_CLEANUP_SCOPES) == set(
        query["scope"][0].split()
    )
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
        if method == "GET" and url.endswith("/tables"):
            return response(
                method,
                url,
                {
                    "code": 0,
                    "data": {
                        "items": [
                            {"table_id": "tbl-default", "name": "接入说明"},
                            *[
                                {"table_id": f"tbl-{index}", "name": item["name"]}
                                for index, item in enumerate(created_tables, start=1)
                            ],
                        ],
                        "has_more": False,
                    },
                },
            )
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
        if method == "GET" and url.endswith("/fields"):
            table_index = int(table_id.removeprefix("tbl-")) - 1
            return response(
                method,
                url,
                {
                    "code": 0,
                    "data": {
                        "items": [
                            {"field_name": field["field_name"], "type": field["type"]}
                            for field in created_tables[table_index]["fields"]
                        ],
                        "has_more": False,
                    },
                },
            )
        if method == "POST" and url.endswith("/records/search"):
            assert body["field_names"][0] == "业务标识"
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
        "班级公开课表",
        "公开调课通知",
    ]
    assert all(table["fields"][0]["field_name"] == "业务标识" for table in created_tables)

    repeated_workspace = client.post(
        "/api/v1/integrations/feishu/workspaces",
        headers=auth_headers,
        json={"name": "途排智策 - 示范校 - 2026 秋"},
    )
    assert repeated_workspace.status_code == 201
    assert app_create_count == 1

    # A later display-name change must repair the established timetable Base,
    # not create a second Bitable and break the MiaoDa page already bound to it.
    renamed_workspace = client.post(
        "/api/v1/integrations/feishu/workspaces",
        headers=auth_headers,
        json={"name": "展示名称已调整"},
    )
    assert renamed_workspace.status_code == 201
    assert renamed_workspace.json()["id"] == workspace.json()["id"]
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
    assert second.json()["detail"]["records_updated"] == 0
    assert second.json()["detail"]["records_skipped"] == 6
    assert len(remote_records["tbl-1"]) == 6

    batch = client.post(
        "/api/v1/integrations/feishu/sync-batch",
        headers=auth_headers,
        json={},
    )
    assert batch.status_code == 200, batch.text
    payload = batch.json()
    assert payload["schedule_set_id"] == "default"
    assert payload["status"] == "completed"
    assert payload["completed_count"] == len(TABLE_SCHEMAS)
    assert payload["failed_count"] == 0
    assert [item["resource"] for item in payload["results"]] == list(TABLE_SCHEMAS)
    assert all(item["detail"]["trigger"] == "manual_batch" for item in payload["results"])
    assert all(item["detail"]["schedule_set_id"] == "default" for item in payload["results"])

    history = client.get("/api/v1/integrations/feishu/syncs", headers=auth_headers)
    assert history.status_code == 200
    assert {item["resource"] for item in history.json()} >= set(TABLE_SCHEMAS)
    with SessionLocal() as db:
        assert db.scalar(select(func.count(FeishuRecordBinding.id))) >= 6


def test_sync_preflight_adopts_existing_table_and_adds_only_missing_fields(
    monkeypatch: Any,
) -> None:
    """An upgraded app must repair the existing Base instead of cloning it."""

    with SessionLocal() as db:
        user = User(
            username="preflight_existing_table_fixture",
            password_hash="not-used-in-this-test",
            role="admin",
        )
        db.add(user)
        db.flush()
        connection = FeishuConnection(
            user_id=user.id,
            access_token_encrypted="preflight-access",
            refresh_token_encrypted="preflight-refresh",
            access_expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=[
                "base:record:create",
                "base:record:retrieve",
                "base:record:update",
                "base:table:read",
                "base:table:update",
            ],
            status="active",
        )
        db.add(connection)
        db.flush()
        workspace = FeishuWorkspace(
            connection_id=connection.id,
            schedule_set_id="default",
            name="已有飞书原表",
            app_token="app-existing-public-notice",
            default_table_id="tbl-default",
            url="https://example.test/existing-public-notice",
            status="active",
        )
        db.add(workspace)
        db.commit()

        service = FeishuService(settings, db)
        monkeypatch.setattr(service, "_app_configuration", lambda: None)
        monkeypatch.setattr(service, "access_token", lambda _user_id: (connection, "token"))
        added_fields: list[dict[str, Any]] = []

        def request(method: str, url: str, **kwargs: Any) -> tuple[dict[str, Any], None]:
            if method == "GET" and url.endswith("/tables"):
                return (
                    {
                        "items": [{"table_id": "tbl-existing-notice", "name": "公开调课通知"}],
                        "has_more": False,
                    },
                    None,
                )
            if method == "GET" and url.endswith("/fields"):
                return (
                    {
                        "items": [
                            {"field_name": "业务标识", "type": 1},
                            {"field_name": "是否展示", "type": 1},
                        ],
                        "has_more": False,
                    },
                    None,
                )
            if method == "POST" and url.endswith("/fields"):
                added_fields.append(kwargs["json_body"])
                return {}, None
            if method == "POST" and url.endswith("/tables"):
                raise AssertionError("matching remote table must be adopted, not recreated")
            raise AssertionError(f"unexpected request: {method} {url}")

        monkeypatch.setattr(service, "_request", request)
        prepared = service.prepare_sync_resources(user.id, ["public_adjustment_notice"])
        table = prepared["public_adjustment_notice"]
        assert table.table_id == "tbl-existing-notice"
        assert {item["field_name"] for item in added_fields} == {
            field_name
            for field_name, _field_type in TABLE_SCHEMAS["public_adjustment_notice"][1]
        } - {"业务标识", "是否展示"}
        stored = db.get(FeishuTableBinding, table.id)
        assert stored is not None and stored.table_id == "tbl-existing-notice"

        db.delete(stored)
        db.delete(workspace)
        db.delete(connection)
        db.delete(user)
        db.commit()


def test_sync_request_retries_a_transient_transport_failure(monkeypatch: Any) -> None:
    service = FeishuService(settings, object())  # type: ignore[arg-type]
    calls = 0

    def flaky_request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("temporary", request=httpx.Request(method, url))
        return response(method, url, {"code": 0, "data": {"items": []}})

    monkeypatch.setattr("app.services.feishu.httpx.request", flaky_request)
    monkeypatch.setattr("app.services.feishu.time.sleep", lambda _delay: None)
    data, _ = service._request(
        "POST",
        "https://example.test/records/search",
        retry_attempts=2,
        timeout=0.01,
    )
    assert data == {"items": []}
    assert calls == 2


def test_public_sync_normalizes_rich_text_and_repairs_historical_duplicate(
    monkeypatch: Any,
) -> None:
    """The old bug left rec-old unbound after replacing its binding with rec-new."""

    with SessionLocal() as db:
        user = User(
            username="public_dedupe_fixture",
            password_hash="not-used-in-this-test",
            role="admin",
        )
        db.add(user)
        db.flush()
        connection = FeishuConnection(
            user_id=user.id,
            access_token_encrypted="test-access",
            refresh_token_encrypted="test-refresh",
            access_expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=[
                "base:record:create",
                "base:record:retrieve",
                "base:record:update",
                "base:record:delete",
            ],
            status="active",
        )
        db.add(connection)
        db.flush()
        workspace = FeishuWorkspace(
            connection_id=connection.id,
            schedule_set_id="default",
            name="公开去重回归",
            app_token="app-public-dedupe",
            default_table_id="tbl-default",
            url="https://example.test/public-dedupe",
            status="active",
        )
        db.add(workspace)
        db.flush()
        table = FeishuTableBinding(
            workspace_id=workspace.id,
            resource="public_summary",
            table_name="公开展示汇总",
            table_id="tbl-public-summary",
        )
        db.add(table)
        db.flush()
        binding = FeishuRecordBinding(
            table_binding_id=table.id,
            business_key="total_sessions",
            record_id="rec-new",
        )
        db.add(binding)
        other_binding = FeishuRecordBinding(
            table_binding_id=table.id,
            business_key="room_utilization",
            record_id="rec-foreign-canonical",
        )
        db.add(other_binding)
        db.commit()

        assert normalize_business_key(
            [{"type": "text", "text": "total_"}, {"type": "text", "text": "sessions"}]
        ) == "total_sessions"
        remote_records = [
            {
                "record_id": "rec-old",
                "fields": {
                    "业务标识": [
                        {"type": "text", "text": "total_"},
                        {"type": "text", "text": "sessions"},
                    ]
                },
            },
            {
                "record_id": "rec-new",
                "fields": {"业务标识": [{"type": "text", "text": "total_sessions"}]},
            },
            {
                "record_id": "rec-stale",
                "fields": {"业务标识": [{"type": "text", "text": "old_month_metric"}]},
            },
            {
                # This record belongs to a second current key but its remote
                # text was manually changed to the first key. It is still a
                # canonical record and must not be deleted as a duplicate.
                "record_id": "rec-foreign-canonical",
                "fields": {"业务标识": [{"type": "text", "text": "total_sessions"}]},
            },
        ]
        service = FeishuService(settings, db)
        monkeypatch.setattr(service, "_app_configuration", lambda: None)
        monkeypatch.setattr(service, "access_token", lambda _user_id: (connection, "token"))
        monkeypatch.setattr(service, "prepare_sync_resources", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(
            service,
            "_list_records",
            lambda _token, _workspace, _table_id, _field_names: (remote_records, ["log-list"]),
        )
        updates: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            service,
            "_batch_update",
            lambda _token, _workspace, _table_id, rows: updates.extend(rows) or ["log-update"],
        )
        deleted: list[str] = []
        monkeypatch.setattr(
            service,
            "_batch_delete",
            lambda _token, _workspace, _table_id, record_ids: (
                deleted.extend(record_ids) or set(record_ids),
                set(),
                ["log-delete"],
                None,
            ),
        )

        result = service.sync_rows(
            user.id,
            "public_summary",
            [
                {"业务标识": "total_sessions", "指标名称": "总课次", "指标值": "12"},
                {
                    "业务标识": "room_utilization",
                    "指标名称": "教室利用率",
                    "指标值": "0.6",
                },
            ],
        )
        assert result["records_created"] == 0
        assert result["records_updated"] == 2
        assert updates == [
            ("rec-new", {"业务标识": "total_sessions", "指标名称": "总课次", "指标值": "12"}),
            (
                "rec-foreign-canonical",
                {
                    "业务标识": "room_utilization",
                    "指标名称": "教室利用率",
                    "指标值": "0.6",
                },
            ),
        ]
        assert deleted == ["rec-old", "rec-stale"]
        assert result["duplicate_cleanup"] == {
            "status": "completed",
            "system_owned_public_table": True,
            "managed_candidates": 2,
            "duplicate_candidates": 1,
            "stale_candidates": 1,
            "deleted": 2,
            "failed": 0,
            "skipped_missing_delete_scope": 0,
            "unmanaged_duplicates": 0,
            "unmanaged_stale_records": 0,
            "removed_stale_bindings": 0,
            "error": None,
        }
        db.refresh(binding)
        assert binding.record_id == "rec-new"
        db.refresh(other_binding)
        assert other_binding.record_id == "rec-foreign-canonical"
        db.delete(binding)
        db.delete(other_binding)
        db.delete(table)
        db.delete(workspace)
        db.delete(connection)
        db.delete(user)
        db.commit()


def test_duplicate_cleanup_without_optional_delete_scope_keeps_primary_upsert(
    monkeypatch: Any,
) -> None:
    with SessionLocal() as db:
        user = User(
            username="public_dedupe_no_delete_fixture",
            password_hash="not-used-in-this-test",
            role="admin",
        )
        db.add(user)
        db.flush()
        connection = FeishuConnection(
            user_id=user.id,
            access_token_encrypted="test-access-no-delete",
            refresh_token_encrypted="test-refresh-no-delete",
            access_expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=["base:record:create", "base:record:retrieve", "base:record:update"],
            status="active",
        )
        db.add(connection)
        db.flush()
        workspace = FeishuWorkspace(
            connection_id=connection.id,
            schedule_set_id="default",
            name="公开去重缺少删除权限",
            app_token="app-public-no-delete",
            default_table_id="tbl-default",
            url="https://example.test/public-no-delete",
            status="active",
        )
        db.add(workspace)
        db.flush()
        table = FeishuTableBinding(
            workspace_id=workspace.id,
            resource="public_summary",
            table_name="公开展示汇总",
            table_id="tbl-public-summary-no-delete",
        )
        db.add(table)
        db.flush()
        binding = FeishuRecordBinding(
            table_binding_id=table.id,
            business_key="total_sessions",
            record_id="rec-new-no-delete",
        )
        db.add(binding)
        db.commit()

        service = FeishuService(settings, db)
        monkeypatch.setattr(service, "_app_configuration", lambda: None)
        monkeypatch.setattr(service, "access_token", lambda _user_id: (connection, "token"))
        monkeypatch.setattr(service, "prepare_sync_resources", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(
            service,
            "_list_records",
            lambda _token, _workspace, _table_id, _field_names: (
                [
                    {
                        "record_id": "rec-old-no-delete",
                        "fields": {"业务标识": [{"type": "text", "text": "total_sessions"}]},
                    },
                    {
                        "record_id": "rec-new-no-delete",
                        "fields": {"业务标识": [{"type": "text", "text": "total_sessions"}]},
                    },
                ],
                [],
            ),
        )
        monkeypatch.setattr(service, "_batch_update", lambda *_args: [])
        monkeypatch.setattr(
            service,
            "_batch_delete",
            lambda *_args: (_ for _ in ()).throw(AssertionError("delete must remain optional")),
        )

        result = service.sync_rows(
            user.id,
            "public_summary",
            [{"业务标识": "total_sessions", "指标名称": "总课次", "指标值": "12"}],
        )
        cleanup = result["duplicate_cleanup"]
        assert cleanup["status"] == "skipped_missing_delete_scope"
        assert cleanup["skipped_missing_delete_scope"] == 1
        assert result["records_updated"] == 1
        db.delete(binding)
        db.delete(table)
        db.delete(workspace)
        db.delete(connection)
        db.delete(user)
        db.commit()


def test_sync_keeps_binding_when_remote_search_temporarily_omits_record(
    monkeypatch: Any,
) -> None:
    with SessionLocal() as db:
        user = User(
            username="binding_reconcile_fixture",
            password_hash="not-used-in-this-test",
            role="admin",
        )
        db.add(user)
        db.flush()
        connection = FeishuConnection(
            user_id=user.id,
            access_token_encrypted="test-access-reconcile",
            refresh_token_encrypted="test-refresh-reconcile",
            access_expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=["base:record:create", "base:record:retrieve", "base:record:update"],
            status="active",
        )
        db.add(connection)
        db.flush()
        workspace = FeishuWorkspace(
            connection_id=connection.id,
            schedule_set_id="default",
            name="绑定对账回归",
            app_token="app-binding-reconcile",
            default_table_id="tbl-default",
            url="https://example.test/binding-reconcile",
            status="active",
        )
        db.add(workspace)
        db.flush()
        table = FeishuTableBinding(
            workspace_id=workspace.id,
            resource="teachers",
            table_name="教师",
            table_id="tbl-teachers-reconcile",
        )
        db.add(table)
        db.flush()
        binding = FeishuRecordBinding(
            table_binding_id=table.id,
            business_key="T-RECONCILE",
            record_id="rec-search-omitted",
        )
        db.add(binding)
        db.commit()
        binding_id = binding.id

        service = FeishuService(settings, db)
        monkeypatch.setattr(service, "_app_configuration", lambda: None)
        monkeypatch.setattr(service, "access_token", lambda _user_id: (connection, "token"))
        monkeypatch.setattr(service, "prepare_sync_resources", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(service, "_list_records", lambda *_args: ([], []))
        monkeypatch.setattr(
            service,
            "_batch_create",
            lambda *_args: ([{"record_id": "rec-recreated"}], []),
        )
        monkeypatch.setattr(service, "_batch_update", lambda *_args: [])

        result = service.sync_rows(
            user.id,
            "teachers",
            [{"业务标识": "T-RECONCILE", "教师名称": "对账教师"}],
        )
        assert result["records_created"] == 1
        refreshed = db.get(FeishuRecordBinding, binding_id)
        assert refreshed is not None
        assert refreshed.record_id == "rec-recreated"
        assert (
            db.scalar(
                select(func.count(FeishuRecordBinding.id)).where(
                    FeishuRecordBinding.table_binding_id == table.id
                )
            )
            == 1
        )
        db.delete(refreshed)
        db.delete(table)
        db.delete(workspace)
        db.delete(connection)
        db.delete(user)
        db.commit()


def test_calendar_table_schemas_include_binding_and_fixed_time_fields() -> None:
    teacher_fields = {name for name, _ in TABLE_SCHEMAS["teachers"][1]}
    session_fields = {name for name, _ in TABLE_SCHEMAS["course_sessions"][1]}
    schedule_fields = {name for name, _ in TABLE_SCHEMAS["schedule"][1]}
    public_class_fields = {name for name, _ in TABLE_SCHEMAS["public_class_schedule"][1]}
    notice_fields = {name for name, _ in TABLE_SCHEMAS["public_adjustment_notice"][1]}

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
    assert {
        "是否展示",
        "班级名称",
        "上课日期",
        "上课地点",
        "课表版本",
    } <= public_class_fields
    assert {
        "是否展示",
        "通用提示",
        "调整类型",
        "原上课时间",
        "新上课时间",
        "生效版本",
    } <= notice_fields


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
        if url.endswith("/freebusy/list"):
            return {"freebusy_list": [{"start_time": "s", "end_time": "e"}]}, "log-freebusy"
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

    # 官方接口一次只查一个用户，服务层逐用户请求后汇总。
    assert freebusy == {
        "freebusy_lists": [
            {"user_id": "ou_teacher_1", "freebusy_list": [{"start_time": "s", "end_time": "e"}]},
            {"user_id": "ou_teacher_2", "freebusy_list": [{"start_time": "s", "end_time": "e"}]},
        ]
    }
    assert event["event"]["event_id"] == "event-1"
    assert attendee["attendees"][0]["attendee_id"] == "attendee-1"
    assert calls[0] == {
        "method": "POST",
        "url": "https://open.feishu.cn/open-apis/calendar/v4/freebusy/list",
        "token": "calendar-token",
        "params": {"user_id_type": "open_id"},
        "json_body": {
            "time_min": "2026-08-17T08:00:00+08:00",
            "time_max": "2026-08-24T18:00:00+08:00",
            "user_id": "ou_teacher_1",
            "include_external_calendar": True,
            "only_busy": True,
        },
    }
    assert calls[1]["json_body"]["user_id"] == "ou_teacher_2"
    assert calls[2]["url"].endswith(
        "/calendar/v4/calendars/feishu.cn_teacher%2Fcalendar%40primary/events"
    )
    assert calls[2]["params"] == {
        "user_id_type": "open_id",
        "idempotency_key": "schedule-assignment-000000000000001",
    }
    assert calls[2]["json_body"]["free_busy_status"] == "busy"
    assert calls[3]["url"].endswith(
        "/calendar/v4/calendars/feishu.cn_teacher%2Fcalendar%40primary/events/"
        "event%2F1/attendees"
    )
    assert calls[3]["json_body"] == {
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
