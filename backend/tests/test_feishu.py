from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.api import settings
from app.config import (
    FEISHU_OPTIONAL_BITABLE_APP_SCOPES,
    FEISHU_OPTIONAL_CLEANUP_SCOPES,
    FEISHU_OPTIONAL_VIEW_SCOPES,
    FEISHU_REQUIRED_SCOPES,
    FEISHU_RESOURCES,
)
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
    authorization_url,
    normalize_business_key,
    open_api_url,
    token_url,
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
    assert (
        set(FEISHU_REQUIRED_SCOPES)
        | set(FEISHU_OPTIONAL_BITABLE_APP_SCOPES)
        | set(FEISHU_OPTIONAL_CLEANUP_SCOPES)
        | set(FEISHU_OPTIONAL_VIEW_SCOPES)
    ) == set(query["scope"][0].split())
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


def test_connection_status_requires_reauthorization_when_scope_grant_is_stale(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    configure_feishu(monkeypatch)
    complete_authorization(client, auth_headers, monkeypatch)
    with SessionLocal() as db:
        connection = db.scalar(select(FeishuConnection))
        assert connection is not None
        connection.scopes = [
            scope
            for scope in connection.scopes
            if scope not in {"base:field:read", "bitable:app", "bitable:app:readonly"}
        ]
        db.commit()

    status = client.get("/api/v1/integrations/feishu/connection", headers=auth_headers)
    assert status.status_code == 200
    payload = status.json()
    assert payload["status"] == "reauthorization_required"
    assert payload["authorized"] is False
    assert {"base:field:read", "bitable:app:readonly"} <= set(payload["missing_scopes"])
    assert "重新授权管理员账号" in payload["message"]


def test_bitable_app_scope_accepts_either_feishu_read_variant(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    configure_feishu(monkeypatch)
    complete_authorization(client, auth_headers, monkeypatch)
    with SessionLocal() as db:
        connection = db.scalar(select(FeishuConnection))
        assert connection is not None
        connection.scopes = [
            "offline_access",
            "base:app:create",
            "base:app:read",
            "base:table:create",
            "base:table:read",
            "base:table:update",
            "base:field:read",
            "bitable:app",
            "base:record:create",
            "base:record:retrieve",
            "base:record:update",
            "calendar:calendar.event:create",
            "calendar:calendar.event:update",
            "calendar:calendar.free_busy:read",
        ]
        db.commit()

    status = client.get("/api/v1/integrations/feishu/connection", headers=auth_headers)
    assert status.status_code == 200
    payload = status.json()
    assert payload["status"] == "connected"
    assert payload["authorized"] is True
    assert "bitable:app:readonly" not in payload["missing_scopes"]
    assert "base:view:write_only" not in payload["missing_scopes"]


def test_class_view_projection_is_optional_without_write_scope() -> None:
    service = FeishuService(settings, object())  # type: ignore[arg-type]
    connection = type("Connection", (), {"scopes": []})()
    workspace = type("Workspace", (), {"app_token": "app-class-view"})()

    result = service._reconcile_class_views(
        connection,
        "token",
        workspace,
        "tbl-class-schedule",
        [{"班级名称": "一班"}],
    )

    assert result == {
        "status": "skipped_missing_scope",
        "missing_scope": "base:view:write_only 或 bitable:app",
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "links": {},
    }


def test_patch_class_view_uses_documented_filter_property(monkeypatch: Any) -> None:
    service = FeishuService(settings, object())  # type: ignore[arg-type]
    workspace = type("Workspace", (), {"app_token": "app-class-view"})()
    requests: list[dict[str, Any]] = []

    def request(method: str, url: str, **kwargs: Any) -> tuple[dict[str, Any], str]:
        requests.append({"method": method, "url": url, **kwargs})
        return {"view": {"view_id": "view-1"}}, "log-patch"

    monkeypatch.setattr(service, "_request", request)

    log_id = service._patch_class_view(
        "token",
        workspace,
        "tbl-class-schedule",
        "view-1",
        "班级｜一班",
        "fld-class",
        "campus-a:class-a",
        ["fld-internal", "fld-teacher"],
    )

    assert log_id == "log-patch"
    assert len(requests) == 1
    sent = requests[0]
    assert sent["method"] == "PATCH"
    assert sent["url"].endswith(
        "/bitable/v1/apps/app-class-view/tables/tbl-class-schedule/views/view-1"
    )
    assert sent["json_body"] == {
        "view_name": "班级｜一班",
        "property": {
            "filter_info": {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_id": "fld-class",
                        "operator": "is",
                        "value": '["campus-a:class-a"]',
                    }
                ],
            },
            "hidden_fields": ["fld-internal", "fld-teacher"],
        },
    }
    assert "sort_info" not in sent["json_body"]["property"]


def test_class_view_projection_reconciles_with_write_only_scope(monkeypatch: Any) -> None:
    service = FeishuService(settings, object())  # type: ignore[arg-type]
    connection = type("Connection", (), {"scopes": ["base:view:write_only"]})()
    workspace = type("Workspace", (), {"app_token": "app-class-view"})()
    created_names: list[str] = []
    patched_names: list[str] = []

    monkeypatch.setattr(
        service,
        "_list_fields",
        lambda *_args: ([{"field_name": "班级名称", "field_id": "fld-class"}], ["log-fields"]),
    )
    monkeypatch.setattr(service, "_list_views", lambda *_args: ([], ["log-views"]))

    def create_view(*args: Any) -> tuple[str, str]:
        view_name = str(args[-1])
        created_names.append(view_name)
        return f"view-{len(created_names)}", f"log-create-{len(created_names)}"

    def patch_view(*args: Any) -> str:
        patched_names.append(str(args[4]))
        return f"log-patch-{len(patched_names)}"

    monkeypatch.setattr(service, "_create_view", create_view)
    monkeypatch.setattr(service, "_patch_class_view", patch_view)

    result = service._reconcile_class_views(
        connection,
        "token",
        workspace,
        "tbl-class-schedule",
        [{"班级名称": "一班"}, {"班级名称": "二班"}, {"班级名称": "一班"}],
    )

    assert result["status"] == "completed"
    assert result["classes"] == 2
    assert result["created"] == 2
    assert result["updated"] == 0
    assert result["deleted"] == 0
    assert created_names == ["班级｜一班", "班级｜二班"]
    assert patched_names == created_names


def test_class_view_projection_repairs_view_left_by_failed_patch(monkeypatch: Any) -> None:
    service = FeishuService(settings, object())  # type: ignore[arg-type]
    connection = type("Connection", (), {"scopes": ["base:view:write_only"]})()
    workspace = type(
        "Workspace",
        (),
        {
            "app_token": "app-class-view",
            "url": "https://example.feishu.cn/base/app-class-view",
        },
    )()
    patched: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        service,
        "_list_fields",
        lambda *_args: (
            [
                {
                    "field_name": "业务标识",
                    "field_id": "fld-primary",
                    "is_primary": True,
                },
                {"field_name": "班级标识", "field_id": "fld-class-id"},
                {"field_name": "班级名称", "field_id": "fld-class-name"},
                {"field_name": "上课日期", "field_id": "fld-date"},
                {"field_name": "教师标识", "field_id": "fld-teacher"},
            ],
            ["log-fields"],
        ),
    )
    monkeypatch.setattr(
        service,
        "_list_views",
        lambda *_args: (
            [
                {
                    "view_name": "班级｜一班",
                    "view_id": "view-orphan",
                    "view_type": "grid",
                }
            ],
            ["log-views"],
        ),
    )
    monkeypatch.setattr(
        service,
        "_get_view",
        lambda *_args: (
            {
                "view_id": "view-orphan",
                "view_name": "班级｜一班",
                "view_type": "grid",
                "property": {},
            },
            "log-get",
        ),
    )
    monkeypatch.setattr(
        service,
        "_patch_class_view",
        lambda *args: patched.append(args) or "log-patch",
    )

    result = service._reconcile_class_views(
        connection,
        "token",
        workspace,
        "tbl-class-schedule",
        [{"班级标识": "campus-a:class-a", "班级名称": "一班"}],
    )

    assert result["status"] == "completed"
    assert result["created"] == 0
    assert result["updated"] == 1
    assert result["deleted"] == 0
    assert result["links"] == {
        "campus-a:class-a": (
            "https://example.feishu.cn/base/app-class-view"
            "?table=tbl-class-schedule&view=view-orphan"
        )
    }
    assert result["request_log_ids"] == [
        "log-fields",
        "log-views",
        "log-get",
        "log-patch",
    ]
    assert len(patched) == 1
    assert patched[0][5:] == (
        "fld-class-id",
        "campus-a:class-a",
        ["fld-class-id", "fld-teacher"],
    )


def test_class_view_projection_skips_already_configured_view(monkeypatch: Any) -> None:
    service = FeishuService(settings, object())  # type: ignore[arg-type]
    connection = type("Connection", (), {"scopes": ["base:view:write_only"]})()
    workspace = type("Workspace", (), {"app_token": "app-class-view", "url": ""})()

    monkeypatch.setattr(
        service,
        "_list_fields",
        lambda *_args: (
            [
                {
                    "field_name": "业务标识",
                    "field_id": "fld-primary",
                    "is_primary": True,
                },
                {"field_name": "班级标识", "field_id": "fld-class-id"},
                {"field_name": "班级名称", "field_id": "fld-class-name"},
                {"field_name": "教师标识", "field_id": "fld-teacher"},
            ],
            [],
        ),
    )
    monkeypatch.setattr(
        service,
        "_list_views",
        lambda *_args: (
            [{"view_name": "班级｜一班", "view_id": "view-ready"}],
            [],
        ),
    )
    monkeypatch.setattr(
        service,
        "_get_view",
        lambda *_args: (
            {
                "view_id": "view-ready",
                "property": {
                    "filter_info": {
                        "conjunction": "and",
                        "conditions": [
                            {
                                "field_id": "fld-class-id",
                                "operator": "is",
                                "value": '["campus-a:class-a"]',
                            }
                        ],
                    },
                    "hidden_fields": ["fld-class-id", "fld-teacher"],
                },
            },
            None,
        ),
    )
    monkeypatch.setattr(
        service,
        "_patch_class_view",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not patch unchanged view")),
    )

    result = service._reconcile_class_views(
        connection,
        "token",
        workspace,
        "tbl-class-schedule",
        [{"班级标识": "campus-a:class-a", "班级名称": "一班"}],
    )

    assert result["status"] == "completed"
    assert result["created"] == 0
    assert result["updated"] == 0
    assert result["deleted"] == 0


def test_remote_99991679_marks_user_connection_for_reauthorization(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    configure_feishu(monkeypatch)
    complete_authorization(client, auth_headers, monkeypatch)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.username == "admin"))
        assert admin is not None
        service = FeishuService(settings, db)
        connection, token = service.access_token(admin.id)

        def unauthorized_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            del kwargs
            return httpx.Response(
                400,
                json={
                    "code": 99991679,
                    "msg": "Unauthorized. Please request user re-authorization",
                },
                request=httpx.Request(method, url),
            )

        monkeypatch.setattr("app.services.feishu.httpx.request", unauthorized_request)
        with pytest.raises(FeishuServiceError, match="99991679") as raised:
            service._request(
                "GET",
                "https://open.feishu.cn/open-apis/bitable/v1/apps/app",
                token=token,
            )
        assert raised.value.reauthorization_required is True
        assert connection.status == "reauthorization_required"
        db.refresh(connection)
        assert connection.status == "reauthorization_required"
        assert connection.last_error is not None
        assert "99991679" in connection.last_error


def test_auto_create_workspace_and_sync_idempotently(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    configure_feishu(monkeypatch)
    complete_authorization(client, auth_headers, monkeypatch)
    created_tables: list[dict[str, Any]] = []
    remote_records: dict[str, list[dict[str, Any]]] = {}
    remote_views: dict[str, list[dict[str, Any]]] = {}
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
            remote_views[table_id] = []
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
                                {
                                    "field_id": f"fld-{field_index}",
                                    "field_name": field["field_name"],
                                    "type": field["type"],
                                }
                                for field_index, field in enumerate(
                                    created_tables[table_index]["fields"], start=1
                                )
                            ],
                        "has_more": False,
                    },
                },
            )
        if method == "GET" and url.endswith("/views"):
            return response(
                method,
                url,
                {
                    "code": 0,
                    "data": {"items": remote_views[table_id], "has_more": False},
                },
            )
        if method == "POST" and url.endswith("/views"):
            view_id = f"vew-{len(remote_views[table_id]) + 1}"
            remote_views[table_id].append(
                {"view_id": view_id, "view_name": body["view_name"]}
            )
            return response(
                method,
                url,
                {"code": 0, "data": {"view": {"view_id": view_id}}},
            )
        if method == "PATCH" and "/views/" in url:
            return response(method, url, {"code": 0, "data": {}})
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
    assert len(workspace.json()["tables"]) == len(FEISHU_RESOURCES)
    assert [table["name"] for table in created_tables] == [
        "教师",
        "班级",
        "教室",
        "时段",
        "课程场次",
        "规则",
        "课表",
        "公开展示汇总",
        "公开调课通知",
        "班级链接索引",
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
    failed_results = [item for item in payload["results"] if item["status"] != "completed"]
    assert not failed_results, failed_results
    assert payload["status"] == "completed"
    assert payload["completed_count"] == len(FEISHU_RESOURCES)
    assert payload["failed_count"] == 0
    assert [item["resource"] for item in payload["results"]] == list(FEISHU_RESOURCES)
    assert all(item["detail"]["trigger"] == "manual_batch" for item in payload["results"])
    assert all(item["detail"]["schedule_set_id"] == "default" for item in payload["results"])

    history = client.get("/api/v1/integrations/feishu/syncs", headers=auth_headers)
    assert history.status_code == 200
    assert {item["resource"] for item in history.json()} >= set(FEISHU_RESOURCES)
    with SessionLocal() as db:
        assert db.scalar(select(func.count(FeishuRecordBinding.id))) >= 6


def test_workspace_view_excludes_legacy_business_table_binding() -> None:
    """Legacy public-class rows must not block the current ten-table contract."""
    suffix = uuid4().hex
    with SessionLocal() as db:
        user = User(
            username=f"workspace_view_legacy_{suffix}",
            password_hash="not-used-in-this-test",
            role="admin",
        )
        db.add(user)
        db.flush()
        connection = FeishuConnection(
            user_id=user.id,
            access_token_encrypted="workspace-view-access",
            refresh_token_encrypted="workspace-view-refresh",
            access_expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=[],
            status="active",
        )
        db.add(connection)
        db.flush()
        workspace = FeishuWorkspace(
            connection_id=connection.id,
            schedule_set_id="default",
            name="workspace view legacy fixture",
            app_token=f"app-workspace-view-{suffix}",
            default_table_id="tbl-default",
            url="https://example.test/workspace-view",
            status="active",
        )
        db.add(workspace)
        db.flush()
        for index, resource in enumerate(FEISHU_RESOURCES, start=1):
            db.add(
                FeishuTableBinding(
                    workspace_id=workspace.id,
                    resource=resource,
                    table_name=TABLE_SCHEMAS[resource][0],
                    table_id=f"tbl-current-{index}-{suffix}",
                )
            )
        db.add(
            FeishuTableBinding(
                workspace_id=workspace.id,
                resource="public_class_schedule",
                table_name="班级公开课表",
                table_id=f"tbl-legacy-{suffix}",
            )
        )
        db.commit()

        view = FeishuService(settings, db).workspace_view(workspace)
        assert len(view["tables"]) == len(FEISHU_RESOURCES)
        assert {item.resource for item in view["tables"]} == set(FEISHU_RESOURCES)

        db.execute(
            delete(FeishuTableBinding).where(FeishuTableBinding.workspace_id == workspace.id)
        )
        db.delete(workspace)
        db.delete(connection)
        db.delete(user)
        db.commit()


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
                "base:field:read",
                "base:field:create",
                "bitable:app:readonly",
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
    assert service._request_retry_count == 1


def test_batch_create_uses_large_batches_for_timetable_exports(monkeypatch: Any) -> None:
    service = FeishuService(settings, object())  # type: ignore[arg-type]
    workspace = type("Workspace", (), {"app_token": "app-batch"})()
    rows = [{"业务标识": f"course-{index}", "课节名称": "数学"} for index in range(2501)]
    calls: list[dict[str, Any]] = []

    def request(method: str, url: str, **kwargs: Any) -> tuple[dict[str, Any], str]:
        assert method == "POST"
        assert url.endswith("/records/batch_create")
        calls.append(kwargs)
        records = [
            {"record_id": f"rec-{item['fields']['业务标识']}", "fields": item["fields"]}
            for item in kwargs["json_body"]["records"]
        ]
        return {"records": records}, f"log-{len(calls)}"

    monkeypatch.setattr(service, "_request", request)
    created, logs = service._batch_create("token", workspace, "tbl-courses", rows)

    assert [len(item["json_body"]["records"]) for item in calls] == [1000, 1000, 501]
    assert all(item["timeout"] == 30.0 and item["retry_attempts"] == 1 for item in calls)
    assert len(created) == len(rows)
    assert logs == ["log-1", "log-2", "log-3"]


def test_batch_create_reconciles_a_dropped_response_before_retry(monkeypatch: Any) -> None:
    service = FeishuService(settings, object())  # type: ignore[arg-type]
    workspace = type("Workspace", (), {"app_token": "app-batch"})()
    rows = [{"业务标识": "course-a"}, {"业务标识": "course-b"}]
    requests: list[list[str]] = []

    def request(method: str, url: str, **kwargs: Any) -> tuple[dict[str, Any], str]:
        assert method == "POST"
        assert url.endswith("/records/batch_create")
        batch = kwargs["json_body"]["records"]
        requests.append([item["fields"]["业务标识"] for item in batch])
        if len(requests) == 1:
            raise httpx.RemoteProtocolError(
                "proxy disconnected after commit",
                request=httpx.Request(method, url),
            )
        return {
            "records": [
                {"record_id": "rec-course-b", "fields": batch[0]["fields"]}
            ]
        }, "log-create"

    monkeypatch.setattr(service, "_request", request)
    monkeypatch.setattr(service, "_retry_request", lambda *_args: None)
    monkeypatch.setattr(
        service,
        "_reconcile_created_batch",
        lambda *_args: (
            {"course-a": {"record_id": "rec-course-a", "fields": rows[0]}},
            ["log-reconcile"],
        ),
    )

    created, logs = service._batch_create("token", workspace, "tbl-courses", rows)

    assert requests == [["course-a", "course-b"], ["course-b"]]
    assert [item["record_id"] for item in created] == ["rec-course-a", "rec-course-b"]
    assert logs == ["log-reconcile", "log-create"]


def test_batch_delete_retries_and_continues_after_a_failed_batch(monkeypatch: Any) -> None:
    service = FeishuService(settings, object())  # type: ignore[arg-type]
    workspace = type("Workspace", (), {"app_token": "app-batch"})()
    record_ids = [f"rec-{index}" for index in range(1001)]
    calls: list[dict[str, Any]] = []

    def request(method: str, url: str, **kwargs: Any) -> tuple[dict[str, Any], str]:
        assert method == "POST"
        assert url.endswith("/records/batch_delete")
        calls.append(kwargs)
        if len(calls) == 1:
            raise httpx.ReadTimeout("temporary disconnect", request=httpx.Request(method, url))
        batch = kwargs["json_body"]["records"]
        return {
            "records": [{"record_id": record_id, "deleted": True} for record_id in batch]
        }, "log-delete"

    monkeypatch.setattr(service, "_request", request)
    deleted, failed, logs, error = service._batch_delete(
        "token", workspace, "tbl-schedule", record_ids
    )

    assert len(calls) == 3
    assert all(item["retry_attempts"] == 2 for item in calls)
    assert len(failed) == 500
    assert len(deleted) == 501
    assert error is not None
    assert logs == ["log-delete", "log-delete"]


def test_field_comparison_ignores_volatile_projection_update_time() -> None:
    field_types = dict(TABLE_SCHEMAS["public_summary"][1])
    remote = {
        "业务标识": [{"type": "text", "text": "total_sessions"}],
        "指标名称": [{"type": "text", "text": "总课次"}],
        "指标值": [{"type": "text", "text": "12"}],
        "更新时间": [{"type": "text", "text": "2026-08-15T08:00:00+00:00"}],
    }
    local = {
        "业务标识": "total_sessions",
        "指标名称": "总课次",
        "指标值": "12",
        "更新时间": "2026-08-15T08:05:00+00:00",
    }

    assert FeishuService._fields_match(local, remote, field_types)
    local["指标值"] = "13"
    assert not FeishuService._fields_match(local, remote, field_types)


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

def test_schedule_sync_reuses_current_legacy_version_row(monkeypatch: Any) -> None:
    """V2 replaces the old V2 row instead of creating a second current row."""

    with SessionLocal() as db:
        user = User(
            username="schedule_legacy_key_fixture",
            password_hash="not-used-in-this-test",
            role="admin",
        )
        db.add(user)
        db.flush()
        connection = FeishuConnection(
            user_id=user.id,
            access_token_encrypted="schedule-legacy-access",
            refresh_token_encrypted="schedule-legacy-refresh",
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
            name="课表历史键迁移",
            app_token="app-schedule-legacy",
            default_table_id="tbl-default",
            url="https://example.test/schedule-legacy",
            status="active",
        )
        db.add(workspace)
        db.flush()
        table = FeishuTableBinding(
            workspace_id=workspace.id,
            resource="schedule",
            table_name="课表",
            table_id="tbl-schedule-legacy",
        )
        db.add(table)
        db.flush()
        old_v2 = FeishuRecordBinding(
            table_binding_id=table.id,
            business_key="old-v2-key",
            record_id="rec-v2",
        )
        old_v1 = FeishuRecordBinding(
            table_binding_id=table.id,
            business_key="old-v1-key",
            record_id="rec-v1",
        )
        db.add_all([old_v2, old_v1])
        db.commit()

        remote_records = [
            {
                "record_id": "rec-v2",
                "fields": {
                    "业务标识": "old-v2-key",
                    "场次标识": "CS-1",
                    "版本标识": "version-v2",
                },
            },
            {
                "record_id": "rec-v1",
                "fields": {
                    "业务标识": "old-v1-key",
                    "场次标识": "CS-1",
                    "版本标识": "version-v1",
                },
            },
        ]
        service = FeishuService(settings, db)
        monkeypatch.setattr(service, "_app_configuration", lambda: None)
        monkeypatch.setattr(service, "access_token", lambda _user_id: (connection, "token"))
        monkeypatch.setattr(service, "prepare_sync_resources", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(
            service,
            "_list_records",
            lambda _token, _workspace, _table_id, _field_names: (remote_records, []),
        )
        monkeypatch.setattr(service, "_batch_create", lambda *_args: ([], []))
        updates: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            service,
            "_batch_update",
            lambda _token, _workspace, _table_id, rows: updates.extend(rows) or [],
        )
        deleted: list[str] = []
        monkeypatch.setattr(
            service,
            "_batch_delete",
            lambda _token, _workspace, _table_id, record_ids: (
                deleted.extend(record_ids) or set(record_ids),
                set(),
                [],
                None,
            ),
        )

        result = service.sync_rows(
            user.id,
            "schedule",
            [
                {
                    "业务标识": "stable-schedule-key",
                    "版本标识": "version-v2",
                    "场次标识": "CS-1",
                    "版本号": 2,
                    "版本名称": "V2",
                }
            ],
        )
        assert result["records_created"] == 0
        assert result["records_updated"] == 1
        assert result["records_deleted"] == 1
        assert updates[0][0] == "rec-v2"
        assert deleted == ["rec-v1"]
        bindings = list(
            db.scalars(
                select(FeishuRecordBinding).where(
                    FeishuRecordBinding.table_binding_id == table.id
                )
            )
        )
        assert [(item.business_key, item.record_id) for item in bindings] == [
            ("stable-schedule-key", "rec-v2")
        ]
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


def test_class_link_sync_prefers_remote_public_share_over_generated_view(
    monkeypatch: Any,
) -> None:
    with SessionLocal() as db:
        user = User(
            username="class_link_public_url_fixture",
            password_hash="not-used-in-this-test",
            role="admin",
        )
        db.add(user)
        db.flush()
        connection = FeishuConnection(
            user_id=user.id,
            access_token_encrypted="class-link-access",
            refresh_token_encrypted="class-link-refresh",
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
            name="班级链接人工公开地址",
            app_token="app-class-link-public-url",
            default_table_id="tbl-default",
            url="https://example.test/base",
            status="active",
        )
        db.add(workspace)
        db.flush()
        table = FeishuTableBinding(
            workspace_id=workspace.id,
            resource="public_class_links",
            table_name="班级链接索引",
            table_id="tbl-class-links",
        )
        db.add(table)
        db.flush()
        business_key = "class-link-key"
        binding = FeishuRecordBinding(
            table_binding_id=table.id,
            business_key=business_key,
            record_id="rec-class-link",
        )
        db.add(binding)
        db.commit()

        remote_public_url = "https://example.test/public/share-token"
        generated_view_url = "https://example.test/base?table=tbl-schedule&view=vew-class"
        remote_records = [
            {
                "record_id": "rec-class-link",
                "fields": {
                    "业务标识": business_key,
                    "课表版本": "V1",
                    "班级标识": "campus-a:class-a",
                    "班级名称": "OMO Smart199班",
                    "学生/家长妙搭链接": "https://miaoda.example.test/class-a",
                    "公开视图链接": remote_public_url,
                    "公开入口类型": "妙搭 + 多维表格视图",
                    "访问模式": "互联网公开",
                    "状态": "已配置",
                    "失效时间": "2026-12-31T23:59:59+08:00",
                    "备注": "管理员确认过的正式入口",
                },
            }
        ]
        service = FeishuService(settings, db)
        service._class_view_links[(workspace.id, "default")] = {
            "campus-a:class-a": generated_view_url
        }
        monkeypatch.setattr(service, "_app_configuration", lambda: None)
        monkeypatch.setattr(service, "access_token", lambda _user_id: (connection, "token"))
        monkeypatch.setattr(service, "prepare_sync_resources", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(service, "_list_records", lambda *_args: (remote_records, []))
        monkeypatch.setattr(
            service,
            "_batch_create",
            lambda *_args: (_ for _ in ()).throw(AssertionError("must update existing link row")),
        )
        updates: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            service,
            "_batch_update",
            lambda _token, _workspace, _table_id, rows: updates.extend(rows) or [],
        )
        monkeypatch.setattr(
            service,
            "_batch_delete",
            lambda *_args: (set(), set(), [], None),
        )

        result = service.sync_rows(
            user.id,
            "public_class_links",
            [
                {
                    "业务标识": business_key,
                    "课表版本": "V2",
                    "班级标识": "campus-a:class-a",
                    "班级名称": "OMO Smart199班",
                    "学生/家长妙搭链接": "",
                    "公开视图链接": "",
                    "公开入口类型": "",
                    "访问模式": "",
                    "状态": "",
                    "更新时间": "2026-08-16T12:00:00+08:00",
                    "失效时间": "",
                    "备注": "",
                }
            ],
            workspace.id,
        )

        assert result["records_created"] == 0
        assert result["records_updated"] == 1
        assert updates[0][0] == "rec-class-link"
        updated_fields = updates[0][1]
        assert updated_fields["课表版本"] == "V2"
        assert updated_fields["公开视图链接"] == remote_public_url
        assert updated_fields["公开视图链接"] != generated_view_url
        assert updated_fields["学生/家长妙搭链接"] == "https://miaoda.example.test/class-a"
        assert updated_fields["访问模式"] == "互联网公开"
        assert updated_fields["失效时间"] == "2026-12-31T23:59:59+08:00"
        assert updated_fields["备注"] == "管理员确认过的正式入口"

        db.delete(binding)
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
    class_link_fields = {name for name, _ in TABLE_SCHEMAS["public_class_links"][1]}
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
    assert {
        "上课日期",
        "排序键",
        "班级名称",
        "课程名称",
        "固定开始时间",
        "固定结束时间",
    } <= schedule_fields
    assert {
        "是否展示",
        "班级标识",
        "班级名称",
        "上课日期",
        "排序键",
        "上课地点",
        "课表版本",
    } <= public_class_fields
    assert {
        "班级标识",
        "班级名称",
        "学生/家长妙搭链接",
        "公开视图链接",
        "公开入口类型",
        "访问模式",
        "状态",
        "备注",
    } <= class_link_fields
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


def test_open_api_urls_follow_base_url_setting(monkeypatch: Any) -> None:
    """Lark 国际版切换：URL 工厂按调用时 settings.feishu_base_url 取值。

    开放 API 与控制台走配置域名；OAuth 授权/令牌端点在飞书与 Lark 两侧都在
    accounts.* 域名，按 open.→accounts. 前缀规则推导。
    """

    monkeypatch.setattr(settings, "feishu_base_url", "https://open.larksuite.com")
    assert open_api_url() == "https://open.larksuite.com/open-apis"
    assert (
        authorization_url()
        == "https://accounts.larksuite.com/open-apis/authen/v1/authorize"
    )
    assert token_url() == "https://accounts.larksuite.com/oauth/v3/token"

    monkeypatch.setattr(settings, "feishu_base_url", "https://open.feishu.cn")
    assert open_api_url() == "https://open.feishu.cn/open-apis"
    assert (
        authorization_url() == "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
    )
    assert token_url() == "https://accounts.feishu.cn/oauth/v3/token"
