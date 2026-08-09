from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api import settings
from app.db import SessionLocal
from app.models import FeishuConnection, FeishuOAuthState, FeishuRecordBinding, User
from app.services.feishu import FeishuService, TokenCipher


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
    assert query["code_challenge_method"] == ["S256"]
    assert "offline_access" in query["scope"][0]

    def token_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        body = kwargs["json"]
        assert body["grant_type"] == "authorization_code"
        assert body["code_verifier"]
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
        if method == "GET" and url.endswith("/records"):
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
    assert len(workspace.json()["tables"]) == 7
    assert [table["name"] for table in created_tables] == [
        "教师",
        "班级",
        "教室",
        "时段",
        "课程场次",
        "规则",
        "课表",
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
