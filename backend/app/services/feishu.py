from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import FEISHU_REQUIRED_SCOPES, FEISHU_RESOURCES, Settings
from ..models import (
    FeishuAppConfiguration,
    FeishuConnection,
    FeishuOAuthState,
    FeishuRecordBinding,
    FeishuTableBinding,
    FeishuWorkspace,
)

AUTHORIZATION_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
TOKEN_URL = "https://accounts.feishu.cn/oauth/v3/token"
OPEN_API_URL = "https://open.feishu.cn/open-apis"
BUSINESS_KEY_FIELD = "业务标识"
_refresh_locks: dict[str, threading.Lock] = {}
_refresh_locks_guard = threading.Lock()

TABLE_SCHEMAS: dict[str, tuple[str, list[tuple[str, int]]]] = {
    "teachers": (
        "教师",
        [
            (BUSINESS_KEY_FIELD, 1),
            ("教师名称", 1),
            ("学科", 1),
            ("周最大课时", 2),
            ("不可用时段", 1),
            ("偏好时段", 1),
            ("数据级别", 1),
        ],
    ),
    "class_groups": (
        "班级",
        [
            (BUSINESS_KEY_FIELD, 1),
            ("班级名称", 1),
            ("年级", 1),
            ("学科", 1),
            ("学生数", 2),
            ("优先级", 1),
            ("设备需求", 1),
            ("教师标识", 1),
        ],
    ),
    "rooms": (
        "教室",
        [
            (BUSINESS_KEY_FIELD, 1),
            ("教室名称", 1),
            ("容量", 2),
            ("设备", 1),
            ("可用时段", 1),
            ("是否启用", 1),
        ],
    ),
    "time_slots": (
        "时段",
        [
            (BUSINESS_KEY_FIELD, 1),
            ("星期", 1),
            ("开始时间", 1),
            ("结束时间", 1),
            ("类型", 1),
            ("顺序", 2),
            ("是否开放", 1),
        ],
    ),
    "course_sessions": (
        "课程场次",
        [
            (BUSINESS_KEY_FIELD, 1),
            ("班级标识", 1),
            ("教师标识", 1),
            ("学科", 1),
            ("学生数", 2),
            ("设备需求", 1),
            ("时长分钟", 2),
            ("建议时段", 1),
            ("是否锁定", 1),
        ],
    ),
    "rules": (
        "规则",
        [
            (BUSINESS_KEY_FIELD, 1),
            ("规则原文", 1),
            ("作用对象类型", 1),
            ("作用对象标识", 1),
            ("约束类型", 1),
            ("约束范围", 1),
            ("硬软类型", 1),
            ("权重", 2),
            ("状态", 1),
            ("版本", 2),
            ("来源文档", 1),
        ],
    ),
    "schedule": (
        "课表",
        [
            (BUSINESS_KEY_FIELD, 1),
            ("版本标识", 1),
            ("版本号", 2),
            ("版本名称", 1),
            ("是否当前版本", 1),
            ("发布状态", 1),
            ("场次标识", 1),
            ("班级标识", 1),
            ("教师标识", 1),
            ("学科", 1),
            ("时段标识", 1),
            ("星期", 1),
            ("开始时间", 1),
            ("结束时间", 1),
            ("教室标识", 1),
            ("教室名称", 1),
            ("变更类型", 1),
        ],
    ),
}


class FeishuServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class FeishuAppCredentials:
    app_id: str
    app_secret: str
    oauth_redirect_uri: str
    frontend_url: str
    source: str


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _scope_list(value: object) -> list[str]:
    if isinstance(value, list):
        return sorted({str(item) for item in value if item})
    if isinstance(value, str):
        return sorted({item for item in value.replace(",", " ").split() if item})
    return []


def _refresh_lock(user_id: str) -> threading.Lock:
    with _refresh_locks_guard:
        return _refresh_locks.setdefault(user_id, threading.Lock())


class TokenCipher:
    def __init__(self, key: str) -> None:
        try:
            self.fernet = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise FeishuServiceError("飞书加密主密钥格式不正确") from exc

    def encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self.fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise FeishuServiceError("飞书令牌密文校验失败，请重新授权") from exc


class FeishuService:
    def __init__(self, settings: Settings, db: Session) -> None:
        self.settings = settings
        self.db = db

    def _cipher(self) -> TokenCipher:
        key = self.settings.feishu_token_encryption_key.strip()
        if not key:
            path = self.settings.feishu_token_key_file
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                key = path.read_text(encoding="ascii").strip()
            else:
                generated = Fernet.generate_key()
                try:
                    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    key = path.read_text(encoding="ascii").strip()
                else:
                    try:
                        os.write(descriptor, generated + b"\n")
                    finally:
                        os.close(descriptor)
                    key = generated.decode("ascii")
        return TokenCipher(key)

    def _environment_configuration(self) -> FeishuAppCredentials | None:
        if not self.settings.feishu_environment_configured:
            return None
        return FeishuAppCredentials(
            app_id=self.settings.feishu_app_id,
            app_secret=self.settings.feishu_app_secret,
            oauth_redirect_uri=self.settings.feishu_oauth_redirect_uri,
            frontend_url=self.settings.frontend_url,
            source="environment",
        )

    def _stored_configuration(self) -> FeishuAppConfiguration | None:
        return self.db.get(FeishuAppConfiguration, "default")

    def _app_configuration(self) -> FeishuAppCredentials:
        environment = self._environment_configuration()
        if environment is not None:
            return environment
        stored = self._stored_configuration()
        if stored is None:
            raise FeishuServiceError("请先在当前页面填写飞书应用编号和应用密钥")
        return FeishuAppCredentials(
            app_id=stored.app_id,
            app_secret=self._cipher().decrypt(stored.app_secret_encrypted),
            oauth_redirect_uri=stored.oauth_redirect_uri,
            frontend_url=stored.frontend_url,
            source="frontend",
        )

    def configuration_view(self) -> dict[str, Any]:
        environment = self._environment_configuration()
        if environment is not None:
            return {
                "configured": True,
                "source": "environment",
                "app_id": environment.app_id,
                "secret_configured": True,
                "oauth_redirect_uri": environment.oauth_redirect_uri,
                "frontend_url": environment.frontend_url,
            }
        stored = self._stored_configuration()
        if stored is not None:
            return {
                "configured": True,
                "source": "frontend",
                "app_id": stored.app_id,
                "secret_configured": True,
                "oauth_redirect_uri": stored.oauth_redirect_uri,
                "frontend_url": stored.frontend_url,
            }
        return {
            "configured": False,
            "source": "none",
            "app_id": None,
            "secret_configured": False,
            "oauth_redirect_uri": self.settings.feishu_oauth_redirect_uri,
            "frontend_url": self.settings.frontend_url,
        }

    @staticmethod
    def _validate_url(value: str, label: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise FeishuServiceError(f"{label}必须是完整的 HTTP 或 HTTPS 地址")
        return normalized

    def save_app_configuration(
        self,
        user_id: str,
        app_id: str,
        app_secret: str,
        oauth_redirect_uri: str,
        frontend_url: str,
    ) -> FeishuAppConfiguration:
        if self._environment_configuration() is not None:
            raise FeishuServiceError("当前飞书应用由部署环境统一管理")
        normalized_app_id = app_id.strip()
        if not normalized_app_id.startswith("cli_"):
            raise FeishuServiceError("飞书应用编号应以 cli_ 开头")
        normalized_secret = app_secret.strip()
        if len(normalized_secret) < 8:
            raise FeishuServiceError("飞书应用密钥长度不正确")
        redirect_uri = self._validate_url(oauth_redirect_uri, "授权回调地址")
        if not redirect_uri.endswith("/api/v1/integrations/feishu/oauth/callback"):
            raise FeishuServiceError("授权回调地址必须指向途排智策飞书回调接口")
        normalized_frontend_url = self._validate_url(frontend_url, "前端地址")

        stored = self._stored_configuration()
        if stored is not None and stored.app_id != normalized_app_id:
            connected = self.db.scalar(select(FeishuConnection.id).limit(1))
            if connected is not None:
                raise FeishuServiceError("更换飞书应用前请先解除现有飞书账号连接")
        if stored is None:
            stored = FeishuAppConfiguration(
                id="default",
                app_id=normalized_app_id,
                app_secret_encrypted="",
                oauth_redirect_uri=redirect_uri,
                frontend_url=normalized_frontend_url,
                configured_by=user_id,
            )
            self.db.add(stored)
        stored.app_id = normalized_app_id
        stored.app_secret_encrypted = self._cipher().encrypt(normalized_secret)
        stored.oauth_redirect_uri = redirect_uri
        stored.frontend_url = normalized_frontend_url
        stored.configured_by = user_id
        self.db.commit()
        self.db.refresh(stored)
        return stored

    def frontend_url(self) -> str:
        try:
            return self._app_configuration().frontend_url.rstrip("/")
        except FeishuServiceError:
            return self.settings.frontend_url.rstrip("/")

    @staticmethod
    def _state_hash(state: str) -> str:
        return hashlib.sha256(state.encode("utf-8")).hexdigest()

    @staticmethod
    def _request_log_id(response: httpx.Response) -> str | None:
        return response.headers.get("x-tt-logid") or response.headers.get("x-request-id")

    def _request(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = 30,
    ) -> tuple[dict[str, Any], str | None]:
        headers = {"Authorization": f"Bearer {token}"} if token else None
        response = httpx.request(
            method,
            url,
            headers=headers,
            json=json_body,
            params=params,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise FeishuServiceError("飞书接口返回了非预期响应")
        if payload.get("code") not in (None, 0):
            message = payload.get("msg") or payload.get("error_description") or "接口返回错误"
            raise FeishuServiceError(f"飞书接口请求失败：{message}")
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            raise FeishuServiceError("飞书接口响应缺少数据对象")
        return data, self._request_log_id(response)

    def create_oauth_start(self, user_id: str) -> dict[str, Any]:
        app = self._app_configuration()
        cipher = self._cipher()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        expires_at = _utcnow() + timedelta(minutes=10)
        self.db.add(
            FeishuOAuthState(
                user_id=user_id,
                state_hash=self._state_hash(state),
                pkce_verifier_encrypted=cipher.encrypt(verifier),
                expires_at=expires_at,
            )
        )
        self.db.commit()
        query = urlencode(
            {
                "app_id": app.app_id,
                "redirect_uri": app.oauth_redirect_uri,
                "scope": " ".join(FEISHU_REQUIRED_SCOPES),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return {"authorization_url": f"{AUTHORIZATION_URL}?{query}", "expires_at": expires_at}

    def complete_oauth(self, code: str, state: str) -> FeishuConnection:
        app = self._app_configuration()
        oauth_state = self.db.scalar(
            select(FeishuOAuthState).where(FeishuOAuthState.state_hash == self._state_hash(state))
        )
        now = _utcnow()
        if oauth_state is None or oauth_state.used_at is not None:
            raise FeishuServiceError("飞书授权状态无效或已经使用，请重新发起授权")
        if _aware(oauth_state.expires_at) <= now:
            raise FeishuServiceError("飞书授权状态已过期，请重新发起授权")

        verifier = self._cipher().decrypt(oauth_state.pkce_verifier_encrypted)
        oauth_state.used_at = now
        self.db.commit()
        data, _ = self._request(
            "POST",
            TOKEN_URL,
            json_body={
                "grant_type": "authorization_code",
                "client_id": app.app_id,
                "client_secret": app.app_secret,
                "code": code,
                "redirect_uri": app.oauth_redirect_uri,
                "code_verifier": verifier,
            },
            timeout=15,
        )
        access_token = str(data.get("access_token") or "")
        refresh_token = str(data.get("refresh_token") or "")
        if not access_token or not refresh_token:
            raise FeishuServiceError("飞书未返回完整用户令牌，请确认已申请 offline_access")

        cipher = self._cipher()
        connection = self.db.scalar(
            select(FeishuConnection).where(FeishuConnection.user_id == oauth_state.user_id)
        )
        if connection is None:
            connection = FeishuConnection(
                user_id=oauth_state.user_id,
                access_token_encrypted="",
                refresh_token_encrypted="",
                access_expires_at=now,
            )
            self.db.add(connection)
        connection.access_token_encrypted = cipher.encrypt(access_token)
        connection.refresh_token_encrypted = cipher.encrypt(refresh_token)
        connection.access_expires_at = now + timedelta(
            seconds=max(1, int(data.get("expires_in", 7200)))
        )
        refresh_expires_in = data.get("refresh_expires_in")
        connection.refresh_expires_at = (
            now + timedelta(seconds=max(1, int(refresh_expires_in)))
            if refresh_expires_in is not None
            else None
        )
        connection.scopes = _scope_list(data.get("scope"))
        connection.status = "active"
        connection.last_error = None
        self.db.commit()
        self.db.refresh(connection)
        return connection

    def _connection(self, user_id: str, *, lock: bool = False) -> FeishuConnection:
        statement = select(FeishuConnection).where(FeishuConnection.user_id == user_id)
        if lock:
            statement = statement.with_for_update()
        connection = self.db.scalar(statement)
        if connection is None or connection.status != "active":
            raise FeishuServiceError("飞书账号尚未授权或授权已经失效")
        return connection

    def access_token(self, user_id: str) -> tuple[FeishuConnection, str]:
        app = self._app_configuration()
        with _refresh_lock(user_id):
            connection = self._connection(user_id)
            now = _utcnow()
            if _aware(connection.access_expires_at) > now + timedelta(minutes=2):
                return connection, self._cipher().decrypt(connection.access_token_encrypted)
            connection = self._connection(user_id, lock=True)
            self.db.refresh(connection)
            now = _utcnow()
            if _aware(connection.access_expires_at) > now + timedelta(minutes=2):
                return connection, self._cipher().decrypt(connection.access_token_encrypted)
            if connection.refresh_expires_at and _aware(connection.refresh_expires_at) <= now:
                connection.status = "reauthorization_required"
                connection.last_error = "刷新令牌已过期"
                self.db.commit()
                raise FeishuServiceError("飞书授权已过期，请重新授权")

            cipher = self._cipher()
            refresh_token = cipher.decrypt(connection.refresh_token_encrypted)
            try:
                data, _ = self._request(
                    "POST",
                    TOKEN_URL,
                    json_body={
                        "grant_type": "refresh_token",
                        "client_id": app.app_id,
                        "client_secret": app.app_secret,
                        "refresh_token": refresh_token,
                    },
                    timeout=15,
                )
            except FeishuServiceError as exc:
                connection.status = "reauthorization_required"
                connection.last_error = str(exc)
                self.db.commit()
                raise
            except httpx.HTTPError as exc:
                connection.last_error = f"飞书令牌刷新网络请求失败：{exc}"
                self.db.commit()
                raise FeishuServiceError(connection.last_error) from exc

            access_token = str(data.get("access_token") or "")
            new_refresh_token = str(data.get("refresh_token") or "")
            if not access_token or not new_refresh_token:
                connection.status = "reauthorization_required"
                connection.last_error = "刷新响应缺少新令牌"
                self.db.commit()
                raise FeishuServiceError("飞书刷新响应缺少新令牌，请重新授权")

            connection.access_token_encrypted = cipher.encrypt(access_token)
            connection.refresh_token_encrypted = cipher.encrypt(new_refresh_token)
            connection.access_expires_at = now + timedelta(
                seconds=max(1, int(data.get("expires_in", 7200)))
            )
            refresh_expires_in = data.get("refresh_expires_in")
            connection.refresh_expires_at = (
                now + timedelta(seconds=max(1, int(refresh_expires_in)))
                if refresh_expires_in is not None
                else connection.refresh_expires_at
            )
            connection.scopes = _scope_list(data.get("scope")) or connection.scopes
            connection.last_error = None
            self.db.commit()
            return connection, access_token

    def workspace_view(self, workspace: FeishuWorkspace) -> dict[str, Any]:
        tables = list(
            self.db.scalars(
                select(FeishuTableBinding)
                .where(FeishuTableBinding.workspace_id == workspace.id)
                .order_by(FeishuTableBinding.created_at)
            )
        )
        return {
            "id": workspace.id,
            "name": workspace.name,
            "url": workspace.url,
            "status": workspace.status,
            "last_error": workspace.last_error,
            "tables": tables,
            "created_at": workspace.created_at,
        }

    def connection_view(self, user_id: str) -> dict[str, Any]:
        app_configuration = self.configuration_view()
        missing_fields = [] if app_configuration["configured"] else ["应用编号", "应用密钥"]
        connection = self.db.scalar(
            select(FeishuConnection).where(FeishuConnection.user_id == user_id)
        )
        workspace = None
        if connection is not None:
            workspace = self.db.scalar(
                select(FeishuWorkspace)
                .where(FeishuWorkspace.connection_id == connection.id)
                .order_by(FeishuWorkspace.created_at.desc())
            )
        granted = connection.scopes if connection else []
        missing_scopes = sorted(set(FEISHU_REQUIRED_SCOPES) - set(granted))
        if not app_configuration["configured"]:
            status = "unconfigured"
            message = "请在当前页面填写飞书应用编号和应用密钥。"
        elif connection is None:
            status = "not_authorized"
            message = "应用配置已就绪，请授权飞书管理员账号。"
        elif connection.status != "active":
            status = "reauthorization_required"
            message = "飞书授权已失效，请重新授权管理员账号。"
        else:
            status = "connected"
            message = "飞书管理员账号已授权。"
            if missing_scopes:
                message += f" 仍缺少 {len(missing_scopes)} 项权限。"
        return {
            "status": status,
            "app_configured": bool(app_configuration["configured"]),
            "authorized": bool(connection and connection.status == "active"),
            "missing_fields": missing_fields,
            "granted_scopes": granted,
            "missing_scopes": missing_scopes,
            "access_expires_at": connection.access_expires_at if connection else None,
            "message": message,
            "console_url": "https://open.feishu.cn/app/",
            "docs_url": (
                "https://open.feishu.cn/document/authentication-management/"
                "access-token/obtain-oauth-code"
            ),
            "app_configuration": app_configuration,
            "workspace": self.workspace_view(workspace) if workspace else None,
        }

    def _require_scopes(self, connection: FeishuConnection, scopes: set[str]) -> None:
        missing = sorted(scopes - set(connection.scopes))
        if missing:
            raise FeishuServiceError(f"飞书授权缺少权限：{'、'.join(missing)}")

    def create_workspace(self, user_id: str, name: str) -> FeishuWorkspace:
        connection, token = self.access_token(user_id)
        self._require_scopes(
            connection,
            {"base:app:create", "base:table:create", "base:table:update"},
        )
        workspace = self.db.scalar(
            select(FeishuWorkspace)
            .where(
                FeishuWorkspace.connection_id == connection.id,
                FeishuWorkspace.name == name,
            )
            .order_by(FeishuWorkspace.created_at.desc())
        )
        if workspace and workspace.status == "active":
            return workspace
        try:
            if workspace is None:
                data, _ = self._request(
                    "POST",
                    f"{OPEN_API_URL}/bitable/v1/apps",
                    token=token,
                    json_body={"name": name},
                )
                app = data.get("app", data)
                if not isinstance(app, dict):
                    raise FeishuServiceError("创建多维表格响应缺少应用对象")
                app_token = str(app.get("app_token") or "")
                default_table_id = str(app.get("default_table_id") or "")
                if not app_token or not default_table_id:
                    raise FeishuServiceError("创建多维表格响应缺少表格标识")
                workspace = FeishuWorkspace(
                    connection_id=connection.id,
                    name=name,
                    app_token=app_token,
                    default_table_id=default_table_id,
                    folder_token=app.get("folder_token"),
                    url=str(app.get("url") or f"https://feishu.cn/base/{app_token}"),
                    status="creating",
                )
                self.db.add(workspace)
                self.db.commit()
                self.db.refresh(workspace)
            else:
                workspace.status = "creating"
                workspace.last_error = None
                self.db.commit()

            self._request(
                "PATCH",
                f"{OPEN_API_URL}/bitable/v1/apps/{workspace.app_token}/tables/"
                f"{workspace.default_table_id}",
                token=token,
                json_body={"name": "接入说明"},
            )
            existing = {
                item.resource
                for item in self.db.scalars(
                    select(FeishuTableBinding).where(
                        FeishuTableBinding.workspace_id == workspace.id
                    )
                )
            }
            for resource in FEISHU_RESOURCES:
                if resource in existing:
                    continue
                table_name, fields = TABLE_SCHEMAS[resource]
                data, _ = self._request(
                    "POST",
                    f"{OPEN_API_URL}/bitable/v1/apps/{workspace.app_token}/tables",
                    token=token,
                    json_body={
                        "table": {
                            "name": table_name,
                            "default_view_name": "全部记录",
                            "fields": [
                                {"field_name": field_name, "type": field_type}
                                for field_name, field_type in fields
                            ],
                        }
                    },
                )
                table = data.get("table", data)
                if not isinstance(table, dict) or not table.get("table_id"):
                    raise FeishuServiceError(f"创建“{table_name}”数据表后未返回标识")
                self.db.add(
                    FeishuTableBinding(
                        workspace_id=workspace.id,
                        resource=resource,
                        table_name=table_name,
                        table_id=str(table["table_id"]),
                    )
                )
                self.db.commit()
            workspace.status = "active"
            workspace.last_error = None
            self.db.commit()
            self.db.refresh(workspace)
            return workspace
        except (FeishuServiceError, httpx.HTTPError) as exc:
            if workspace is not None:
                workspace.status = "failed"
                workspace.last_error = str(exc)
                self.db.commit()
            if isinstance(exc, FeishuServiceError):
                raise
            raise FeishuServiceError(f"飞书多维表格创建失败：{exc}") from exc

    def _active_workspace(self, connection_id: str, workspace_id: str | None) -> FeishuWorkspace:
        statement = select(FeishuWorkspace).where(
            FeishuWorkspace.connection_id == connection_id,
            FeishuWorkspace.status == "active",
        )
        if workspace_id:
            statement = statement.where(FeishuWorkspace.id == workspace_id)
        else:
            statement = statement.order_by(FeishuWorkspace.created_at.desc())
        workspace = self.db.scalar(statement)
        if workspace is None:
            raise FeishuServiceError("请先创建排课多维表格")
        return workspace

    def _list_records(
        self, token: str, workspace: FeishuWorkspace, table_id: str
    ) -> tuple[list[dict[str, Any]], list[str]]:
        records: list[dict[str, Any]] = []
        log_ids: list[str] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            data, log_id = self._request(
                "GET",
                f"{OPEN_API_URL}/bitable/v1/apps/{workspace.app_token}/tables/{table_id}/records",
                token=token,
                params=params,
            )
            if log_id:
                log_ids.append(log_id)
            items = data.get("items", [])
            if not isinstance(items, list):
                raise FeishuServiceError("飞书记录列表格式不正确")
            records.extend(item for item in items if isinstance(item, dict))
            if not data.get("has_more"):
                return records, log_ids
            page_token = str(data.get("page_token") or "")
            if not page_token:
                raise FeishuServiceError("飞书记录分页响应缺少下一页标识")

    def _batch_create(
        self, token: str, workspace: FeishuWorkspace, table_id: str, rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        created: list[dict[str, Any]] = []
        log_ids: list[str] = []
        for start in range(0, len(rows), 500):
            batch = rows[start : start + 500]
            data, log_id = self._request(
                "POST",
                f"{OPEN_API_URL}/bitable/v1/apps/{workspace.app_token}/tables/"
                f"{table_id}/records/batch_create",
                token=token,
                json_body={"records": [{"fields": item} for item in batch]},
            )
            if log_id:
                log_ids.append(log_id)
            records = data.get("records", data.get("items", []))
            if isinstance(records, list):
                created.extend(item for item in records if isinstance(item, dict))
        return created, log_ids

    def _batch_update(
        self,
        token: str,
        workspace: FeishuWorkspace,
        table_id: str,
        rows: list[tuple[str, dict[str, Any]]],
    ) -> list[str]:
        log_ids: list[str] = []
        for start in range(0, len(rows), 500):
            batch = rows[start : start + 500]
            _, log_id = self._request(
                "POST",
                f"{OPEN_API_URL}/bitable/v1/apps/{workspace.app_token}/tables/"
                f"{table_id}/records/batch_update",
                token=token,
                json_body={
                    "records": [
                        {"record_id": record_id, "fields": fields} for record_id, fields in batch
                    ]
                },
            )
            if log_id:
                log_ids.append(log_id)
        return log_ids

    def sync_rows(
        self,
        user_id: str,
        resource: str,
        rows: list[dict[str, Any]],
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        connection, token = self.access_token(user_id)
        self._require_scopes(
            connection,
            {"base:record:create", "base:record:retrieve", "base:record:update"},
        )
        workspace = self._active_workspace(connection.id, workspace_id)
        table = self.db.scalar(
            select(FeishuTableBinding).where(
                FeishuTableBinding.workspace_id == workspace.id,
                FeishuTableBinding.resource == resource,
            )
        )
        if table is None:
            raise FeishuServiceError("当前多维表格缺少对应业务数据表")

        normalized: list[dict[str, Any]] = []
        local_keys: set[str] = set()
        for row in rows:
            fields = {key: value for key, value in row.items() if value is not None}
            business_key = str(fields.get(BUSINESS_KEY_FIELD) or "").strip()
            if not business_key:
                raise FeishuServiceError("同步数据缺少“业务标识”")
            if business_key in local_keys:
                raise FeishuServiceError(f"本地同步数据存在重复业务标识：{business_key}")
            local_keys.add(business_key)
            normalized.append(fields)

        remote_records, log_ids = self._list_records(token, workspace, table.table_id)
        remote_by_key: dict[str, str] = {}
        for record in remote_records:
            fields = record.get("fields", {})
            if not isinstance(fields, dict):
                continue
            key = str(fields.get(BUSINESS_KEY_FIELD) or "").strip()
            record_id = str(record.get("record_id") or "")
            if not key or not record_id:
                continue
            if key in remote_by_key and remote_by_key[key] != record_id:
                raise FeishuServiceError(f"飞书数据表存在重复业务标识：{key}")
            remote_by_key[key] = record_id

        to_create = [row for row in normalized if str(row[BUSINESS_KEY_FIELD]) not in remote_by_key]
        to_update = [
            (remote_by_key[str(row[BUSINESS_KEY_FIELD])], row)
            for row in normalized
            if str(row[BUSINESS_KEY_FIELD]) in remote_by_key
        ]
        created_records, create_logs = self._batch_create(
            token, workspace, table.table_id, to_create
        )
        log_ids.extend(create_logs)
        if to_create:
            if len(created_records) == len(to_create):
                for row, record in zip(to_create, created_records, strict=True):
                    record_id = str(record.get("record_id") or "")
                    if record_id:
                        remote_by_key[str(row[BUSINESS_KEY_FIELD])] = record_id
            if any(str(row[BUSINESS_KEY_FIELD]) not in remote_by_key for row in to_create):
                refreshed, refresh_logs = self._list_records(token, workspace, table.table_id)
                log_ids.extend(refresh_logs)
                for record in refreshed:
                    fields = record.get("fields", {})
                    if isinstance(fields, dict) and record.get("record_id"):
                        key = str(fields.get(BUSINESS_KEY_FIELD) or "").strip()
                        if key:
                            remote_by_key[key] = str(record["record_id"])
        log_ids.extend(self._batch_update(token, workspace, table.table_id, to_update))

        bindings = {
            item.business_key: item
            for item in self.db.scalars(
                select(FeishuRecordBinding).where(FeishuRecordBinding.table_binding_id == table.id)
            )
        }
        for row in normalized:
            key = str(row[BUSINESS_KEY_FIELD])
            bound_record_id = remote_by_key.get(key)
            if not bound_record_id:
                raise FeishuServiceError(f"飞书写入后未找到记录绑定：{key}")
            binding = bindings.get(key)
            if binding is None:
                self.db.add(
                    FeishuRecordBinding(
                        table_binding_id=table.id,
                        business_key=key,
                        record_id=bound_record_id,
                    )
                )
            else:
                binding.record_id = bound_record_id
        self.db.commit()
        return {
            "workspace_id": workspace.id,
            "workspace_url": workspace.url,
            "table_id": table.table_id,
            "records_read": len(remote_records),
            "records_created": len(to_create),
            "records_updated": len(to_update),
            "records_written": len(to_create) + len(to_update),
            "request_log_ids": list(dict.fromkeys(log_ids)),
        }

    def disconnect(self, user_id: str) -> None:
        connection = self.db.scalar(
            select(FeishuConnection).where(FeishuConnection.user_id == user_id)
        )
        if connection is not None:
            workspace_ids = list(
                self.db.scalars(
                    select(FeishuWorkspace.id).where(FeishuWorkspace.connection_id == connection.id)
                )
            )
            table_ids = list(
                self.db.scalars(
                    select(FeishuTableBinding.id).where(
                        FeishuTableBinding.workspace_id.in_(workspace_ids)
                    )
                )
            )
            if table_ids:
                self.db.execute(
                    delete(FeishuRecordBinding).where(
                        FeishuRecordBinding.table_binding_id.in_(table_ids)
                    )
                )
            if workspace_ids:
                self.db.execute(
                    delete(FeishuTableBinding).where(
                        FeishuTableBinding.workspace_id.in_(workspace_ids)
                    )
                )
                self.db.execute(
                    delete(FeishuWorkspace).where(FeishuWorkspace.id.in_(workspace_ids))
                )
            self.db.delete(connection)
        self.db.execute(delete(FeishuOAuthState).where(FeishuOAuthState.user_id == user_id))
        self.db.commit()


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
