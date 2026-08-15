from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import (
    AILY_OPTIONAL_SCOPES,
    FEISHU_OPTIONAL_CLEANUP_SCOPES,
    FEISHU_REQUIRED_SCOPES,
    FEISHU_RESOURCES,
    Settings,
)
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
SYSTEM_OWNED_PUBLIC_RESOURCES = frozenset(
    {"public_summary", "public_class_schedule", "public_adjustment_notice"}
)
SYNC_RECORD_PAGE_SIZE = 200
SYNC_BATCH_SIZE = 200
SYNC_REQUEST_TIMEOUT_SECONDS = 10.0
SYNC_RETRY_ATTEMPTS = 2
_RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_RETRYABLE_FEISHU_ERROR_CODES = frozenset({1254290, 1255001, 1255002})
_refresh_locks: dict[str, threading.Lock] = {}
_refresh_locks_guard = threading.Lock()
logger = logging.getLogger(__name__)

TABLE_SCHEMAS: dict[str, tuple[str, list[tuple[str, int]]]] = {
    "teachers": (
        "教师",
        [
            (BUSINESS_KEY_FIELD, 1),
            ("教师名称", 1),
            ("学科", 1),
            ("飞书用户标识", 1),
        ],
    ),
    "class_groups": (
        "班级",
        [
            (BUSINESS_KEY_FIELD, 1),
            ("班级名称", 1),
            ("班型", 1),
            ("业务线", 1),
            ("教师标识", 1),
        ],
    ),
    "rooms": (
        "教室",
        [
            (BUSINESS_KEY_FIELD, 1),
            ("教室名称", 1),
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
            ("业务线", 1),
            ("产品班型", 1),
            ("班级标识", 1),
            ("教师标识", 1),
            ("具体日程账号", 1),
            ("学科", 1),
            ("课节名称", 1),
            ("编排来源", 1),
            ("编排阶段", 1),
            ("计划课次", 2),
            ("计划课时", 2),
            ("课次序号", 2),
            ("上课日期", 1),
            ("时长分钟", 2),
            ("建议时段", 1),
            ("固定开始时间", 1),
            ("固定结束时间", 1),
            ("原始教室标识", 1),
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
            ("业务线", 1),
            ("产品班型", 1),
            ("班级标识", 1),
            ("教师标识", 1),
            ("具体日程账号", 1),
            ("学科", 1),
            ("上课日期", 1),
            ("时段标识", 1),
            ("星期", 1),
            ("开始时间", 1),
            ("结束时间", 1),
            ("固定开始时间", 1),
            ("固定结束时间", 1),
            ("原始教室标识", 1),
            ("教室标识", 1),
            ("教室名称", 1),
            ("变更类型", 1),
        ],
    ),
    "public_summary": (
        "公开展示汇总",
        [
            (BUSINESS_KEY_FIELD, 1),
            ("指标名称", 1),
            ("指标值", 1),
            ("月份", 1),
            ("产品线匿名标签", 1),
            ("更新时间", 1),
        ],
    ),
    "public_class_schedule": (
        "班级公开课表",
        [
            (BUSINESS_KEY_FIELD, 1),
            ("是否展示", 1),
            ("班级名称", 1),
            ("上课日期", 1),
            ("星期", 1),
            ("开始时间", 1),
            ("结束时间", 1),
            ("课程名称", 1),
            ("学科", 1),
            ("上课地点", 1),
            ("课表版本", 1),
            ("更新时间", 1),
        ],
    ),
    "public_adjustment_notice": (
        "公开调课通知",
        [
            (BUSINESS_KEY_FIELD, 1),
            ("是否展示", 1),
            ("公告状态", 1),
            ("通用提示", 1),
            ("调整类型", 1),
            ("班级名称", 1),
            ("课程名称", 1),
            ("原上课时间", 1),
            ("新上课时间", 1),
            ("原上课地点", 1),
            ("新上课地点", 1),
            ("生效版本", 1),
            ("更新时间", 1),
        ],
    ),
}


class FeishuServiceError(RuntimeError):
    pass


_ZERO_WIDTH_KEY_CHARACTERS = "\ufeff\u200b\u200c\u200d\u2060"


def normalize_business_key(value: object) -> str:
    """Turn Bitable's plain/rich-text key shape into one stable local key.

    A Bitable text field is returned as a list of rich-text fragments rather
    than the plain string sent during a write.  Converting that list with
    ``str(value)`` creates a different key on every read and used to cause an
    idempotent sync to create a fresh record.  This parser deliberately keeps
    ordinary internal whitespace/case intact; only invisible transport marks
    and outer whitespace are removed.
    """

    def text_from(candidate: object) -> str:
        if candidate is None:
            return ""
        if isinstance(candidate, str):
            return candidate
        if isinstance(candidate, list):
            parts: list[str] = []
            for item in candidate:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(str(item["text"]))
                    continue
                raise FeishuServiceError("飞书“业务标识”字段格式不正确")
            return "".join(parts)
        if isinstance(candidate, dict):
            if isinstance(candidate.get("text"), str):
                return str(candidate["text"])
            for key in ("value", "content"):
                if key in candidate:
                    return text_from(candidate[key])
        raise FeishuServiceError("飞书“业务标识”字段格式不正确")

    return text_from(value).translate(str.maketrans("", "", _ZERO_WIDTH_KEY_CHARACTERS)).strip()


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
        # A batch invokes ``sync_rows`` once per resource.  Reuse the resolved
        # workspace/token and the table-readiness result within that one service
        # instance so clicking "全部同步" does not repeat the same metadata
        # discovery ten times.
        self._sync_contexts: dict[
            tuple[str, str, str | None], tuple[FeishuWorkspace, FeishuConnection, str]
        ] = {}
        self._ready_table_ids: dict[tuple[str, str], str] = {}

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
                "aily_configured": bool(
                    self.settings.aily_app_id and self.settings.aily_skill_id
                ),
                "aily_app_id": self.settings.aily_app_id or None,
                "aily_skill_id": self.settings.aily_skill_id or None,
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
                "aily_configured": bool(stored.aily_app_id and stored.aily_skill_id),
                "aily_app_id": stored.aily_app_id or None,
                "aily_skill_id": stored.aily_skill_id or None,
            }
        return {
            "configured": False,
            "source": "none",
            "app_id": None,
            "secret_configured": False,
            "oauth_redirect_uri": self.settings.feishu_oauth_redirect_uri,
            "frontend_url": self.settings.frontend_url,
            "aily_configured": False,
            "aily_app_id": None,
            "aily_skill_id": None,
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
        app_secret: str | None,
        oauth_redirect_uri: str,
        frontend_url: str,
        aily_app_id: str,
        aily_skill_id: str,
    ) -> FeishuAppConfiguration:
        if self._environment_configuration() is not None:
            raise FeishuServiceError("当前飞书应用由部署环境统一管理")
        normalized_app_id = app_id.strip()
        if not normalized_app_id.startswith("cli_"):
            raise FeishuServiceError("飞书应用编号应以 cli_ 开头")
        normalized_secret = (app_secret or "").strip()
        redirect_uri = self._validate_url(oauth_redirect_uri, "授权回调地址")
        if not redirect_uri.endswith("/api/v1/integrations/feishu/oauth/callback"):
            raise FeishuServiceError("授权回调地址必须指向途排智策飞书回调接口")
        normalized_frontend_url = self._validate_url(frontend_url, "前端地址")
        normalized_aily_app_id = aily_app_id.strip()
        normalized_aily_skill_id = aily_skill_id.strip()
        if bool(normalized_aily_app_id) != bool(normalized_aily_skill_id):
            raise FeishuServiceError("Aily 应用标识和技能标识需要同时填写或同时留空")
        if normalized_aily_app_id and not normalized_aily_app_id.startswith("spring_"):
            raise FeishuServiceError("Aily 应用标识应以 spring_ 开头")
        if normalized_aily_skill_id and not normalized_aily_skill_id.startswith("skill_"):
            raise FeishuServiceError("Aily 技能标识应以 skill_ 开头")

        stored = self._stored_configuration()
        if stored is None and len(normalized_secret) < 8:
            raise FeishuServiceError("首次配置必须填写正确的飞书应用密钥")
        if normalized_secret and len(normalized_secret) < 8:
            raise FeishuServiceError("飞书应用密钥长度不正确")
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
                aily_app_id=normalized_aily_app_id,
                aily_skill_id=normalized_aily_skill_id,
                configured_by=user_id,
            )
            self.db.add(stored)
        stored.app_id = normalized_app_id
        if normalized_secret:
            stored.app_secret_encrypted = self._cipher().encrypt(normalized_secret)
        stored.oauth_redirect_uri = redirect_uri
        stored.frontend_url = normalized_frontend_url
        stored.aily_app_id = normalized_aily_app_id
        stored.aily_skill_id = normalized_aily_skill_id
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
        retry_attempts: int = 1,
    ) -> tuple[dict[str, Any], str | None]:
        headers = {"Authorization": f"Bearer {token}"} if token else None

        # Do not retry a mutation by default: a connection can die after
        # Feishu has committed a create/delete, and blindly sending it again
        # can make the remote state worse.  Safe read/update callers opt in
        # below with ``retry_attempts``.
        attempts = max(1, retry_attempts)
        for attempt in range(attempts):
            try:
                response = httpx.request(
                    method,
                    url,
                    headers=headers,
                    json=json_body,
                    params=params,
                    timeout=timeout,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt + 1 >= attempts:
                    raise
                self._retry_request(method, url, attempt + 1, attempts, str(exc))
                continue

            if response.status_code in _RETRYABLE_HTTP_STATUS_CODES and attempt + 1 < attempts:
                self._retry_request(
                    method,
                    url,
                    attempt + 1,
                    attempts,
                    f"HTTP {response.status_code}",
                )
                continue

            if response.status_code >= 400:
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    code = payload.get("code")
                    message = (
                        payload.get("msg")
                        or payload.get("error_description")
                        or payload.get("error")
                        or "接口返回错误"
                    )
                    raise FeishuServiceError(
                        f"飞书接口请求失败（HTTP {response.status_code}，错误码 {code}）：{message}"
                    )
                response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise FeishuServiceError("飞书接口返回了非预期响应")
            code = payload.get("code")
            if code not in (None, 0):
                if code in _RETRYABLE_FEISHU_ERROR_CODES and attempt + 1 < attempts:
                    self._retry_request(method, url, attempt + 1, attempts, f"错误码 {code}")
                    continue
                message = payload.get("msg") or payload.get("error_description") or "接口返回错误"
                raise FeishuServiceError(f"飞书接口请求失败（错误码 {code}）：{message}")
            data = payload.get("data", payload)
            if not isinstance(data, dict):
                raise FeishuServiceError("飞书接口响应缺少数据对象")
            return data, self._request_log_id(response)

        # The loop either returned or raised above.  Keep mypy and future
        # refactors honest with an explicit terminal error.
        raise FeishuServiceError("飞书接口重试结束但没有返回结果")

    @staticmethod
    def _retry_request(method: str, url: str, attempt: int, total: int, reason: str) -> None:
        # A bounded sub-second backoff repairs transient Feishu disconnects
        # without converting one failed click into a long opaque wait.
        delay = 0.25 * attempt
        logger.info(
            "飞书请求短暂失败，准备重试：method=%s url=%s attempt=%s/%s reason=%s",
            method,
            url,
            attempt,
            total,
            reason,
        )
        time.sleep(delay)

    def create_oauth_start(self, user_id: str) -> dict[str, Any]:
        app = self._app_configuration()
        scopes = [*FEISHU_REQUIRED_SCOPES, *FEISHU_OPTIONAL_CLEANUP_SCOPES]
        if self.configuration_view()["aily_configured"]:
            scopes.extend(AILY_OPTIONAL_SCOPES)
        state = secrets.token_urlsafe(32)
        expires_at = _utcnow() + timedelta(minutes=10)
        self.db.add(
            FeishuOAuthState(
                user_id=user_id,
                state_hash=self._state_hash(state),
                pkce_verifier_encrypted="",
                expires_at=expires_at,
            )
        )
        self.db.commit()
        query = urlencode(
            {
                "client_id": app.app_id,
                "response_type": "code",
                "redirect_uri": app.oauth_redirect_uri,
                "scope": " ".join(scopes),
                "state": state,
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
            "created_at": _aware(workspace.created_at),
        }

    def connection_view(self, user_id: str, schedule_set_id: str | None = None) -> dict[str, Any]:
        app_configuration = self.configuration_view()
        missing_fields = [] if app_configuration["configured"] else ["应用编号", "应用密钥"]
        personal_connection = self.db.scalar(
            select(FeishuConnection).where(FeishuConnection.user_id == user_id)
        )
        workspace = None
        if schedule_set_id:
            workspace = self.db.scalar(
                select(FeishuWorkspace)
                .where(
                    FeishuWorkspace.schedule_set_id == schedule_set_id,
                    FeishuWorkspace.status == "active",
                )
                .order_by(FeishuWorkspace.created_at.asc())
            )
        elif personal_connection is not None:
            workspace = self.db.scalar(
                select(FeishuWorkspace)
                .where(
                    FeishuWorkspace.connection_id == personal_connection.id,
                    FeishuWorkspace.status == "active",
                )
                .order_by(FeishuWorkspace.created_at.asc())
            )

        # The Bitable target is bound to the timetable, not to the operator who
        # happens to open this page.  A scheduler granted plan A therefore sees
        # the administrator-owned plan-A workspace as ready and can run the
        # permitted sync action without re-authorizing a second Feishu account.
        workspace_connection = None
        if workspace is not None:
            workspace_connection = self.db.get(FeishuConnection, workspace.connection_id)
        connection = workspace_connection or personal_connection
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
            message = (
                "当前课表方案已绑定飞书管理员账号。"
                if workspace_connection is not None and workspace_connection.user_id != user_id
                else "飞书管理员账号已授权。"
            )
            if missing_scopes:
                message += f" 仍缺少 {len(missing_scopes)} 项权限。"
        return {
            "status": status,
            "app_configured": bool(app_configuration["configured"]),
            "authorized": bool(connection and connection.status == "active"),
            "missing_fields": missing_fields,
            "granted_scopes": granted,
            "missing_scopes": missing_scopes,
            "access_expires_at": _aware(connection.access_expires_at) if connection else None,
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

    @staticmethod
    def _rfc3339_datetime(value: str, field_name: str) -> datetime:
        normalized = value.strip()
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError as exc:
            raise FeishuServiceError(f"{field_name}必须是 RFC 3339 日期时间") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise FeishuServiceError(f"{field_name}必须包含时区偏移")
        return parsed

    def batch_freebusy(
        self,
        user_id: str,
        *,
        user_ids: list[str],
        time_min: str,
        time_max: str,
        user_id_type: str = "open_id",
        include_external_calendar: bool = True,
        only_busy: bool = True,
    ) -> dict[str, Any]:
        """查询若干用户主日历的忙闲区间。

        官方「查询主日历日程忙闲信息」接口是
        ``POST /open-apis/calendar/v4/freebusy/list``，一次只接受**一个**
        ``user_id`` 或 ``room_id``（参数为 time_min、time_max、user_id/room_id、
        include_external_calendar、only_busy）。此前实现调用的
        ``/calendar/v4/freebusy/batch`` 与复数 ``user_ids`` 在官方文档中不存在，
        真实环境会直接失败。这里改为逐用户请求后汇总，对外仍返回
        ``freebusy_lists``，调用方无需改动。

        单批仍限制在 10 个用户、两周窗口内，用来控制请求量。
        """
        normalized_user_ids = [item.strip() for item in user_ids if item.strip()]
        if not 1 <= len(normalized_user_ids) <= 10:
            raise FeishuServiceError("单次飞书忙闲查询必须包含 1 至 10 个用户")
        if len(set(normalized_user_ids)) != len(normalized_user_ids):
            raise FeishuServiceError("单次飞书忙闲查询不能包含重复用户")
        start = self._rfc3339_datetime(time_min, "忙闲查询开始时间")
        end = self._rfc3339_datetime(time_max, "忙闲查询结束时间")
        if end <= start:
            raise FeishuServiceError("忙闲查询结束时间必须晚于开始时间")
        if end - start > timedelta(days=14):
            raise FeishuServiceError("单次飞书忙闲查询时间范围不能超过两周")

        connection, token = self.access_token(user_id)
        self._require_scopes(connection, {"calendar:calendar.free_busy:read"})
        freebusy_lists: list[dict[str, Any]] = []
        for target_user_id in normalized_user_ids:
            data, _ = self._request(
                "POST",
                f"{OPEN_API_URL}/calendar/v4/freebusy/list",
                token=token,
                params={"user_id_type": user_id_type},
                json_body={
                    "time_min": time_min,
                    "time_max": time_max,
                    "user_id": target_user_id,
                    "include_external_calendar": include_external_calendar,
                    "only_busy": only_busy,
                },
            )
            entries = data.get("freebusy_list")
            freebusy_lists.append(
                {
                    "user_id": target_user_id,
                    "freebusy_list": entries if isinstance(entries, list) else [],
                }
            )
        return {"freebusy_lists": freebusy_lists}

    def create_calendar_event(
        self,
        user_id: str,
        *,
        calendar_id: str,
        event: dict[str, Any],
        idempotency_key: str | None = None,
        user_id_type: str = "open_id",
    ) -> dict[str, Any]:
        """Create an event in a primary or shared calendar with the supplied event body."""
        normalized_calendar_id = calendar_id.strip()
        if not normalized_calendar_id:
            raise FeishuServiceError("创建飞书日程时必须提供日历标识")
        if not isinstance(event.get("start_time"), dict) or not isinstance(
            event.get("end_time"), dict
        ):
            raise FeishuServiceError("创建飞书日程时必须提供开始时间和结束时间对象")
        params: dict[str, Any] = {"user_id_type": user_id_type}
        if idempotency_key is not None:
            normalized_key = idempotency_key.strip()
            if not 32 <= len(normalized_key) <= 128:
                raise FeishuServiceError("飞书日程幂等键长度必须为 32 至 128 个字符")
            params["idempotency_key"] = normalized_key

        connection, token = self.access_token(user_id)
        self._require_scopes(connection, {"calendar:calendar.event:create"})
        data, _ = self._request(
            "POST",
            f"{OPEN_API_URL}/calendar/v4/calendars/{quote(normalized_calendar_id, safe='')}/events",
            token=token,
            params=params,
            json_body=dict(event),
        )
        return data

    def add_event_attendee(
        self,
        user_id: str,
        *,
        calendar_id: str,
        event_id: str,
        attendee_user_id: str,
        user_id_type: str = "open_id",
        is_optional: bool = False,
        need_notification: bool = True,
    ) -> dict[str, Any]:
        """Add one in-tenant user as an attendee of an existing calendar event."""
        normalized_calendar_id = calendar_id.strip()
        normalized_event_id = event_id.strip()
        normalized_attendee_id = attendee_user_id.strip()
        if not normalized_calendar_id or not normalized_event_id:
            raise FeishuServiceError("添加日程参与人时必须提供日历标识和日程标识")
        if not normalized_attendee_id:
            raise FeishuServiceError("添加日程参与人时必须提供飞书用户标识")

        connection, token = self.access_token(user_id)
        self._require_scopes(connection, {"calendar:calendar.event:update"})
        data, _ = self._request(
            "POST",
            (
                f"{OPEN_API_URL}/calendar/v4/calendars/"
                f"{quote(normalized_calendar_id, safe='')}/events/"
                f"{quote(normalized_event_id, safe='')}/attendees"
            ),
            token=token,
            params={"user_id_type": user_id_type},
            json_body={
                "attendees": [
                    {
                        "type": "user",
                        "user_id": normalized_attendee_id,
                        "is_optional": is_optional,
                    }
                ],
                "need_notification": need_notification,
            },
        )
        return data

    def start_aily_skill(
        self,
        user_id: str,
        *,
        app_id: str,
        skill_id: str,
        query: str,
        input_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke a configured Aily workflow skill and decode its JSON output."""
        normalized_app_id = app_id.strip()
        normalized_skill_id = skill_id.strip()
        if not normalized_app_id.startswith("spring_") or not normalized_skill_id.startswith(
            "skill_"
        ):
            raise FeishuServiceError("Aily 应用标识或技能标识格式不正确")
        connection, token = self.access_token(user_id)
        self._require_scopes(connection, {"aily:skill:write"})
        body: dict[str, Any] = {"query": query}
        if input_payload is not None:
            body["input"] = json.dumps(input_payload, ensure_ascii=False)
        data, _ = self._request(
            "POST",
            (
                f"{OPEN_API_URL}/aily/v1/apps/{quote(normalized_app_id, safe='')}"
                f"/skills/{quote(normalized_skill_id, safe='')}/start"
            ),
            token=token,
            json_body=body,
        )
        if data.get("status") not in (None, "success"):
            raise FeishuServiceError(f"Aily 技能执行未成功：{data.get('status')}")
        output = data.get("output")
        if not isinstance(output, str) or not output.strip():
            raise FeishuServiceError("Aily 技能响应缺少 output")
        try:
            decoded = json.loads(output)
        except json.JSONDecodeError as exc:
            raise FeishuServiceError("Aily 技能 output 不是合法 JSON") from exc
        if not isinstance(decoded, dict):
            raise FeishuServiceError("Aily 技能 output 必须是 JSON 对象")
        return decoded

    def create_workspace(
        self, user_id: str, name: str, schedule_set_id: str = "default"
    ) -> FeishuWorkspace:
        connection, token = self.access_token(user_id)
        self._require_scopes(
            connection,
            {"base:app:create", "base:table:create", "base:table:read", "base:table:update"},
        )
        # A timetable owns exactly one working Bitable.  The name is a label,
        # not a second identity: old workspaces created before the timetable
        # name was added must be repaired in place rather than silently
        # creating a second Base and disconnecting existing MiaoDa views.
        workspace = self.db.scalar(
            select(FeishuWorkspace)
            .where(
                FeishuWorkspace.connection_id == connection.id,
                FeishuWorkspace.schedule_set_id == schedule_set_id,
                FeishuWorkspace.status.in_(("active", "creating", "failed")),
            )
            .order_by(FeishuWorkspace.created_at.asc())
        )
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
                    schedule_set_id=schedule_set_id,
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
            # A Base can outlive local bindings (for example after an upgrade
            # added a public projection table).  Inspect the actual Base first:
            # adopt matching existing tables, add only missing tables/fields,
            # and never create a second Base merely because a local row is old.
            self._reconcile_workspace_resources(
                connection,
                token,
                workspace,
                FEISHU_RESOURCES,
            )
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

    def _active_workspace(
        self,
        workspace_id: str | None,
        schedule_set_id: str = "default",
        owner_user_id: str | None = None,
    ) -> FeishuWorkspace:
        statement = select(FeishuWorkspace).where(
            FeishuWorkspace.schedule_set_id == schedule_set_id,
            FeishuWorkspace.status == "active",
        )
        if workspace_id:
            statement = statement.where(FeishuWorkspace.id == workspace_id)
        else:
            # The administrator who owns the current OAuth connection should
            # get their timetable Base first.  The fallback below preserves
            # scheduler/approver access to a workspace created by an admin.
            if owner_user_id:
                owned_workspace = self.db.scalar(
                    statement.join(
                        FeishuConnection,
                        FeishuConnection.id == FeishuWorkspace.connection_id,
                    )
                    .where(FeishuConnection.user_id == owner_user_id)
                    .order_by(FeishuWorkspace.created_at.asc())
                )
                if owned_workspace is not None:
                    return owned_workspace
            # Prefer the original timetable workspace.  This also repairs
            # installations that once created a duplicate Base due to a later
            # display-name change; automatic publish/sync must never jump to
            # the newest one behind the user's back.
            statement = statement.order_by(FeishuWorkspace.created_at.asc())
        workspace = self.db.scalar(statement)
        if workspace is None:
            raise FeishuServiceError("请先创建排课多维表格")
        return workspace

    def _list_tables(
        self, token: str, workspace: FeishuWorkspace
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Read all actual Bitable tables before deciding whether to create one."""

        tables: list[dict[str, Any]] = []
        log_ids: list[str] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data, log_id = self._request(
                "GET",
                f"{OPEN_API_URL}/bitable/v1/apps/{workspace.app_token}/tables",
                token=token,
                params=params,
                timeout=SYNC_REQUEST_TIMEOUT_SECONDS,
                retry_attempts=SYNC_RETRY_ATTEMPTS,
            )
            if log_id:
                log_ids.append(log_id)
            items = data.get("items", [])
            if not isinstance(items, list):
                raise FeishuServiceError("飞书数据表列表格式不正确")
            tables.extend(item for item in items if isinstance(item, dict))
            if not data.get("has_more"):
                return tables, log_ids
            page_token = str(data.get("page_token") or "")
            if not page_token:
                raise FeishuServiceError("飞书数据表分页响应缺少下一页标识")

    def _list_fields(
        self, token: str, workspace: FeishuWorkspace, table_id: str
    ) -> tuple[list[dict[str, Any]], list[str]]:
        fields: list[dict[str, Any]] = []
        log_ids: list[str] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data, log_id = self._request(
                "GET",
                f"{OPEN_API_URL}/bitable/v1/apps/{workspace.app_token}/tables/{table_id}/fields",
                token=token,
                params=params,
                timeout=SYNC_REQUEST_TIMEOUT_SECONDS,
                retry_attempts=SYNC_RETRY_ATTEMPTS,
            )
            if log_id:
                log_ids.append(log_id)
            items = data.get("items", [])
            if not isinstance(items, list):
                raise FeishuServiceError("飞书字段列表格式不正确")
            fields.extend(item for item in items if isinstance(item, dict))
            if not data.get("has_more"):
                return fields, log_ids
            page_token = str(data.get("page_token") or "")
            if not page_token:
                raise FeishuServiceError("飞书字段分页响应缺少下一页标识")

    def _create_table(
        self,
        token: str,
        workspace: FeishuWorkspace,
        table_name: str,
        fields: list[tuple[str, int]],
    ) -> str:
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
        return str(table["table_id"])

    def _create_field(
        self,
        token: str,
        workspace: FeishuWorkspace,
        table_id: str,
        field_name: str,
        field_type: int,
    ) -> None:
        self._request(
            "POST",
            f"{OPEN_API_URL}/bitable/v1/apps/{workspace.app_token}/tables/{table_id}/fields",
            token=token,
            json_body={"field_name": field_name, "type": field_type},
        )

    def _reconcile_workspace_resources(
        self,
        connection: FeishuConnection,
        token: str,
        workspace: FeishuWorkspace,
        resources: tuple[str, ...] | list[str],
    ) -> dict[str, FeishuTableBinding]:
        """Adopt/reconcile tables in an existing Base without creating a new Base.

        Local bindings are a cache, not the source of truth.  This is important
        for upgrades: a pre-existing Base may already contain a table that the
        local database has not yet bound, and an old binding can point to a
        manually deleted table.  We inspect the Base first, then add only what
        is missing.
        """

        unique_resources = tuple(dict.fromkeys(resources))
        unknown = set(unique_resources) - set(TABLE_SCHEMAS)
        if unknown:
            raise FeishuServiceError(f"未知飞书同步资源：{sorted(unknown)[0]}")
        self._require_scopes(connection, {"base:table:read"})
        # Ensure no local write transaction remains open while the metadata
        # requests are in flight; SQLite otherwise blocks unrelated admin work.
        self.db.commit()
        remote_tables, _ = self._list_tables(token, workspace)
        remote_by_id = {
            str(item.get("table_id") or ""): item
            for item in remote_tables
            if str(item.get("table_id") or "")
        }
        remote_by_name: dict[str, list[dict[str, Any]]] = {}
        for item in remote_tables:
            name = str(item.get("name") or "").strip()
            if name:
                remote_by_name.setdefault(name, []).append(item)

        bindings = list(
            self.db.scalars(
                select(FeishuTableBinding).where(FeishuTableBinding.workspace_id == workspace.id)
            )
        )
        bindings_by_resource = {item.resource: item for item in bindings}
        binding_by_table_id = {item.table_id: item for item in bindings}
        prepared: dict[str, FeishuTableBinding] = {}

        for resource in unique_resources:
            table_name, expected_fields = TABLE_SCHEMAS[resource]
            binding = bindings_by_resource.get(resource)
            table_id = binding.table_id if binding and binding.table_id in remote_by_id else ""
            created_new_table = False

            if not table_id:
                # Prefer an existing matching table that is not already bound
                # to another resource.  Never create a duplicate just because
                # the local binding cache was absent or stale.
                candidate = next(
                    (
                        item
                        for item in remote_by_name.get(table_name, [])
                        if str(item.get("table_id") or "") not in binding_by_table_id
                        or binding_by_table_id[str(item.get("table_id") or "")].resource == resource
                    ),
                    None,
                )
                if candidate is not None:
                    table_id = str(candidate.get("table_id") or "")
                else:
                    self._require_scopes(connection, {"base:table:create"})
                    table_id = self._create_table(token, workspace, table_name, expected_fields)
                    remote_by_id[table_id] = {"table_id": table_id, "name": table_name}
                    created_new_table = True

                if binding is None:
                    binding = FeishuTableBinding(
                        workspace_id=workspace.id,
                        resource=resource,
                        table_name=table_name,
                        table_id=table_id,
                    )
                    self.db.add(binding)
                    bindings_by_resource[resource] = binding
                else:
                    # Changing a table target invalidates all record IDs held
                    # by the old table's binding ledger.
                    self.db.execute(
                        delete(FeishuRecordBinding).where(
                            FeishuRecordBinding.table_binding_id == binding.id
                        )
                    )
                    binding.table_id = table_id
                    binding.table_name = table_name
                self.db.commit()
                binding_by_table_id[table_id] = binding
            elif binding is not None:
                # Keep the locally displayed name aligned with this product's
                # known resource name, without renaming a user's real table.
                if binding.table_name != table_name:
                    binding.table_name = table_name
                    self.db.commit()

            if binding is None:
                raise FeishuServiceError(f"飞书数据表“{table_name}”绑定未建立")

            if created_new_table:
                # The creation payload above already supplied every expected
                # field, so an immediate field-list round trip would be pure
                # latency.  Future preflights still inspect it normally.
                prepared[resource] = binding
                continue

            # A table that already existed may be from an earlier app version.
            # Inspect its columns and append only schema fields that are absent.
            remote_fields, _ = self._list_fields(token, workspace, table_id)
            fields_by_name = {
                str(item.get("field_name") or "").strip(): item for item in remote_fields
            }
            missing_fields = [
                (field_name, field_type)
                for field_name, field_type in expected_fields
                if field_name not in fields_by_name
            ]
            if missing_fields:
                self._require_scopes(connection, {"base:table:update"})
                for field_name, field_type in missing_fields:
                    self._create_field(token, workspace, table_id, field_name, field_type)
            prepared[resource] = binding

        self.db.commit()
        return prepared

    def prepare_sync_resources(
        self,
        user_id: str,
        resources: tuple[str, ...] | list[str],
        workspace_id: str | None = None,
        schedule_set_id: str = "default",
    ) -> dict[str, FeishuTableBinding]:
        """Run one preflight for a sync batch and cache ready table bindings."""

        context = self._resolve_sync_context(user_id, workspace_id, schedule_set_id)
        workspace, connection, token = context
        prepared = self._reconcile_workspace_resources(
            connection,
            token,
            workspace,
            resources,
        )
        for resource, table in prepared.items():
            self._ready_table_ids[(workspace.id, resource)] = table.id
        return prepared

    def _resolve_sync_context(
        self,
        user_id: str,
        workspace_id: str | None,
        schedule_set_id: str,
    ) -> tuple[FeishuWorkspace, FeishuConnection, str]:
        key = (user_id, schedule_set_id, workspace_id)
        cached = self._sync_contexts.get(key)
        if cached is not None:
            return cached
        self._app_configuration()
        workspace = self._active_workspace(
            workspace_id,
            schedule_set_id,
            owner_user_id=user_id,
        )
        workspace_connection = self.db.get(FeishuConnection, workspace.connection_id)
        if workspace_connection is None:
            raise FeishuServiceError("当前多维表格缺少飞书授权连接")
        connection, token = self.access_token(workspace_connection.user_id)
        self._require_scopes(
            connection,
            {"base:record:create", "base:record:retrieve", "base:record:update"},
        )
        # The next action is a network call.  Close any read transaction opened
        # while resolving the workspace so it never blocks SQLite writers.
        self.db.commit()
        context = (workspace, connection, token)
        self._sync_contexts[key] = context
        return context

    def _list_records(
        self,
        token: str,
        workspace: FeishuWorkspace,
        table_id: str,
        field_names: list[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        records: list[dict[str, Any]] = []
        log_ids: list[str] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": SYNC_RECORD_PAGE_SIZE}
            if page_token:
                params["page_token"] = page_token
            data, log_id = self._request(
                "POST",
                f"{OPEN_API_URL}/bitable/v1/apps/{workspace.app_token}/tables/"
                f"{table_id}/records/search",
                token=token,
                params=params,
                json_body={"field_names": field_names},
                timeout=SYNC_REQUEST_TIMEOUT_SECONDS,
                retry_attempts=SYNC_RETRY_ATTEMPTS,
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
        for start in range(0, len(rows), SYNC_BATCH_SIZE):
            batch = rows[start : start + SYNC_BATCH_SIZE]
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
        for start in range(0, len(rows), SYNC_BATCH_SIZE):
            batch = rows[start : start + SYNC_BATCH_SIZE]
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
                timeout=SYNC_REQUEST_TIMEOUT_SECONDS,
                retry_attempts=SYNC_RETRY_ATTEMPTS,
            )
            if log_id:
                log_ids.append(log_id)
        return log_ids

    def _batch_delete(
        self,
        token: str,
        workspace: FeishuWorkspace,
        table_id: str,
        record_ids: list[str],
    ) -> tuple[set[str], set[str], list[str], str | None]:
        """Best-effort removal for records owned by our binding ledger only.

        Deletion is intentionally separate from the core upsert path.  A
        legacy OAuth grant may not yet contain ``base:record:delete`` and a
        table can have extra sharing rules, neither of which should turn a
        successful export into a failed timetable publication.
        """

        deleted: set[str] = set()
        failed: set[str] = set()
        log_ids: list[str] = []
        error: str | None = None
        for start in range(0, len(record_ids), SYNC_BATCH_SIZE):
            batch = record_ids[start : start + SYNC_BATCH_SIZE]
            try:
                data, log_id = self._request(
                    "POST",
                    f"{OPEN_API_URL}/bitable/v1/apps/{workspace.app_token}/tables/"
                    f"{table_id}/records/batch_delete",
                    token=token,
                    json_body={"records": batch},
                    params={"ignore_consistency_check": True},
                    timeout=SYNC_REQUEST_TIMEOUT_SECONDS,
                )
            except (FeishuServiceError, httpx.HTTPError) as exc:
                failed.update(record_ids[start:])
                error = str(exc)
                break
            if log_id:
                log_ids.append(log_id)
            records = data.get("records", data.get("items", []))
            if not isinstance(records, list):
                failed.update(batch)
                error = "飞书删除记录响应缺少逐条删除状态"
                continue
            status_by_record_id = {
                str(item.get("record_id") or ""): item
                for item in records
                if isinstance(item, dict) and item.get("record_id")
            }
            for record_id in batch:
                item = status_by_record_id.get(record_id)
                if item and item.get("deleted") is True:
                    deleted.add(record_id)
                else:
                    failed.add(record_id)
                    if error is None:
                        error = "飞书未确认全部重复记录已删除"
        return deleted, failed, log_ids, error

    @staticmethod
    def _remote_record_index(
        records: list[dict[str, Any]],
    ) -> tuple[dict[str, list[str]], set[str]]:
        """Index remote records without collapsing duplicate business keys."""

        by_key: dict[str, list[str]] = {}
        record_ids: set[str] = set()
        for record in records:
            record_id = str(record.get("record_id") or "").strip()
            if not record_id:
                continue
            record_ids.add(record_id)
            fields = record.get("fields")
            if not isinstance(fields, dict):
                continue
            key = normalize_business_key(fields.get(BUSINESS_KEY_FIELD))
            if not key:
                continue
            candidates = by_key.setdefault(key, [])
            if record_id not in candidates:
                candidates.append(record_id)
        return by_key, record_ids

    @staticmethod
    def _plain_text(value: object) -> str:
        """Normalize Bitable's text/rich-text shapes for a lossless comparison."""

        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                    elif text is not None:
                        parts.append(str(text))
                elif item is not None:
                    parts.append(str(item))
            return "".join(parts)
        if isinstance(value, dict):
            text = value.get("text")
            if isinstance(text, str):
                return text
            if text is not None:
                return str(text)
        return str(value)

    @classmethod
    def _comparable_field_value(cls, field_type: int, value: object) -> str:
        if field_type != 2:
            return cls._plain_text(value)
        text = cls._plain_text(value).strip()
        if not text:
            return ""
        try:
            number = Decimal(text)
        except (InvalidOperation, ValueError):
            return text
        normalized = format(number.normalize(), "f")
        return "0" if normalized in {"", "-0"} else normalized

    @classmethod
    def _fields_match(
        cls,
        local_fields: dict[str, Any],
        remote_fields: dict[str, Any],
        field_types: dict[str, int],
    ) -> bool:
        # We own the schema fields but deliberately ignore extra remote columns
        # so a user can add a local display/formula column without every sync
        # trying to erase it.
        for field_name, local_value in local_fields.items():
            field_type = field_types.get(field_name, 1)
            if cls._comparable_field_value(field_type, local_value) != cls._comparable_field_value(
                field_type,
                remote_fields.get(field_name),
            ):
                return False
        return True

    def sync_rows(
        self,
        user_id: str,
        resource: str,
        rows: list[dict[str, Any]],
        workspace_id: str | None = None,
        schedule_set_id: str = "default",
    ) -> dict[str, Any]:
        workspace, connection, token = self._resolve_sync_context(
            user_id,
            workspace_id,
            schedule_set_id,
        )
        if (workspace.id, resource) not in self._ready_table_ids:
            self.prepare_sync_resources(
                user_id,
                [resource],
                workspace_id,
                schedule_set_id,
            )
        table = self.db.scalar(
            select(FeishuTableBinding).where(
                FeishuTableBinding.workspace_id == workspace.id,
                FeishuTableBinding.resource == resource,
            )
        )
        if table is None:
            raise FeishuServiceError("当前多维表格缺少对应业务数据表")
        field_types = dict(TABLE_SCHEMAS[resource][1])
        field_names = list(field_types)

        normalized: list[dict[str, Any]] = []
        local_keys: set[str] = set()
        for row in rows:
            fields = {key: value for key, value in row.items() if value is not None}
            business_key = normalize_business_key(fields.get(BUSINESS_KEY_FIELD))
            if not business_key:
                raise FeishuServiceError("同步数据缺少“业务标识”")
            if business_key in local_keys:
                raise FeishuServiceError(f"本地同步数据存在重复业务标识：{business_key}")
            local_keys.add(business_key)
            # Always write a plain string back.  It is important for both new
            # Bitable records and records that were previously rich-text.
            fields[BUSINESS_KEY_FIELD] = business_key
            normalized.append(fields)

        # Do not keep a SQLite transaction open while the remote records are
        # read.  The rows above are plain Python data now, so committing cannot
        # affect the projection being synchronized.
        self.db.commit()
        remote_records, log_ids = self._list_records(
            token,
            workspace,
            table.table_id,
            field_names,
        )
        remote_by_key, remote_record_ids = self._remote_record_index(remote_records)
        remote_fields_by_record_id = {
            str(item.get("record_id") or ""): item["fields"]
            for item in remote_records
            if str(item.get("record_id") or "") and isinstance(item.get("fields"), dict)
        }
        bindings = list(
            self.db.scalars(
                select(FeishuRecordBinding).where(FeishuRecordBinding.table_binding_id == table.id)
            )
        )
        # A search response can be restricted by Bitable sharing or return an
        # incomplete page after a transient failure.  Keep every local binding
        # as the ownership ledger; only use it as a remote update target when
        # its record is actually present in this response.
        bindings_by_key = {item.business_key: item for item in bindings}
        bindings_by_record_id = {
            item.record_id: item for item in bindings if item.record_id in remote_record_ids
        }
        canonical_by_key: dict[str, str] = {}
        canonical_record_ids: set[str] = set()

        def canonical_record(key: str) -> str | None:
            binding = bindings_by_key.get(key)
            if (
                binding
                and binding.record_id in remote_record_ids
                and binding.record_id not in canonical_record_ids
            ):
                canonical_record_ids.add(binding.record_id)
                return binding.record_id
            for record_id in remote_by_key.get(key, []):
                owner = bindings_by_record_id.get(record_id)
                if record_id in canonical_record_ids:
                    continue
                # Do not silently take a record owned by a different current
                # business key when someone edits its Bitable cell by hand.
                if owner is not None and owner.business_key != key:
                    continue
                canonical_record_ids.add(record_id)
                return record_id
            return None

        for row in normalized:
            key = str(row[BUSINESS_KEY_FIELD])
            record_id = canonical_record(key)
            if record_id:
                canonical_by_key[key] = record_id

        to_create = [
            row for row in normalized if str(row[BUSINESS_KEY_FIELD]) not in canonical_by_key
        ]
        to_update = [
            (canonical_by_key[str(row[BUSINESS_KEY_FIELD])], row)
            for row in normalized
            if str(row[BUSINESS_KEY_FIELD]) in canonical_by_key
            and not self._fields_match(
                row,
                remote_fields_by_record_id.get(
                    canonical_by_key[str(row[BUSINESS_KEY_FIELD])], {}
                ),
                field_types,
            )
        ]
        records_skipped = len(normalized) - len(to_create) - len(to_update)
        # Bindings were just read.  Close that read transaction before sending
        # any potentially slow batch request so unrelated account edits are not
        # blocked by this sync.
        self.db.commit()
        created_records, create_logs = self._batch_create(
            token, workspace, table.table_id, to_create
        )
        log_ids.extend(create_logs)
        if to_create:
            if len(created_records) == len(to_create):
                for row, record in zip(to_create, created_records, strict=True):
                    record_id = str(record.get("record_id") or "")
                    if record_id:
                        key = str(row[BUSINESS_KEY_FIELD])
                        canonical_by_key[key] = record_id
                        canonical_record_ids.add(record_id)
                        remote_record_ids.add(record_id)
                        remote_by_key.setdefault(key, []).append(record_id)
            if any(str(row[BUSINESS_KEY_FIELD]) not in canonical_by_key for row in to_create):
                refreshed, refresh_logs = self._list_records(
                    token,
                    workspace,
                    table.table_id,
                    field_names,
                )
                log_ids.extend(refresh_logs)
                remote_by_key, remote_record_ids = self._remote_record_index(refreshed)
                for row in to_create:
                    key = str(row[BUSINESS_KEY_FIELD])
                    if key not in canonical_by_key:
                        record_id = canonical_record(key)
                        if record_id:
                            canonical_by_key[key] = record_id
        missing_bindings = [
            str(row[BUSINESS_KEY_FIELD])
            for row in normalized
            if str(row[BUSINESS_KEY_FIELD]) not in canonical_by_key
        ]
        if missing_bindings:
            raise FeishuServiceError(
                f"飞书写入后未找到记录绑定：{missing_bindings[0]}"
            )
        log_ids.extend(self._batch_update(token, workspace, table.table_id, to_update))

        # Public projection tables are exclusively generated by this service.
        # Their old rich-text bug created an unbound rec1 and then moved the
        # sole binding to rec2, so a binding-only policy could never repair the
        # exact visible duplicate.  Internal master-data tables stay
        # conservative because users may keep hand-authored records there.
        system_owned_public_table = resource in SYSTEM_OWNED_PUBLIC_RESOURCES
        cleanup_candidate_ids: set[str] = set()
        cleanup_bindings_by_record_id: dict[str, FeishuRecordBinding] = {}
        duplicate_candidates = 0
        stale_candidates = 0
        unmanaged_duplicates = 0
        unmanaged_stale_records = 0
        removed_stale_bindings = 0

        def add_cleanup_candidate(record_id: str, *, stale: bool = False) -> None:
            nonlocal duplicate_candidates, stale_candidates
            if record_id in cleanup_candidate_ids:
                return
            cleanup_candidate_ids.add(record_id)
            if stale:
                stale_candidates += 1
            else:
                duplicate_candidates += 1
            bound_record = bindings_by_record_id.get(record_id)
            if bound_record is not None:
                cleanup_bindings_by_record_id[record_id] = bound_record

        if system_owned_public_table:
            # Public projection tables are fully generated resources.  Delete
            # every remote record that is not a canonical current projection:
            # this repairs unbound historical rich-text duplicates, stale
            # months, removed course rows, and malformed no-key remnants.
            local_remote_ids = {
                record_id
                for key in local_keys
                for record_id in remote_by_key.get(key, [])
            }
            for record_id in sorted(remote_record_ids - canonical_record_ids):
                add_cleanup_candidate(record_id, stale=record_id not in local_remote_ids)
            # If a record was already removed outside the app, its local ledger
            # entry is stale too.  It must not survive forever and influence a
            # later projection reconciliation.
            for binding in bindings:
                if (
                    binding.business_key not in local_keys
                    and binding.record_id not in remote_record_ids
                ):
                    self.db.delete(binding)
                    removed_stale_bindings += 1
        else:
            for key in local_keys:
                canonical_id = canonical_by_key[key]
                for record_id in remote_by_key.get(key, []):
                    # A malformed remote key can overlap another local key's
                    # bound canonical record. Never delete any such record.
                    if record_id == canonical_id or record_id in canonical_record_ids:
                        continue
                    candidate_binding = bindings_by_record_id.get(record_id)
                    if (
                        candidate_binding is not None
                        and candidate_binding.business_key not in local_keys
                    ):
                        add_cleanup_candidate(record_id)
                    else:
                        unmanaged_duplicates += 1

        for row in normalized:
            key = str(row[BUSINESS_KEY_FIELD])
            bound_record_id = canonical_by_key[key]
            record_binding = bindings_by_key.get(key)
            if record_binding is None:
                record_binding = FeishuRecordBinding(
                    table_binding_id=table.id,
                    business_key=key,
                    record_id=bound_record_id,
                )
                self.db.add(record_binding)
                bindings_by_key[key] = record_binding
                bindings_by_record_id[bound_record_id] = record_binding
            else:
                if record_binding.record_id != bound_record_id:
                    bindings_by_record_id.pop(record_binding.record_id, None)
                record_binding.record_id = bound_record_id
                bindings_by_record_id[bound_record_id] = record_binding

        duplicate_cleanup: dict[str, Any] = {
            "status": "not_needed",
            "system_owned_public_table": system_owned_public_table,
            "managed_candidates": len(cleanup_candidate_ids),
            "duplicate_candidates": duplicate_candidates,
            "stale_candidates": stale_candidates,
            "deleted": 0,
            "failed": 0,
            "skipped_missing_delete_scope": 0,
            "unmanaged_duplicates": unmanaged_duplicates,
            "unmanaged_stale_records": unmanaged_stale_records,
            "removed_stale_bindings": removed_stale_bindings,
            "error": None,
        }
        if cleanup_candidate_ids:
            if "base:record:delete" not in connection.scopes:
                duplicate_cleanup.update(
                    {
                        "status": "skipped_missing_delete_scope",
                        "skipped_missing_delete_scope": len(cleanup_candidate_ids),
                        "error": "飞书授权缺少权限：base:record:delete",
                    }
                )
            else:
                deleted_ids, failed_ids, delete_logs, cleanup_error = self._batch_delete(
                    token,
                    workspace,
                    table.table_id,
                    sorted(cleanup_candidate_ids),
                )
                log_ids.extend(delete_logs)
                duplicate_cleanup.update(
                    {
                        "status": "completed" if not failed_ids else "failed",
                        "deleted": len(deleted_ids),
                        "failed": len(failed_ids),
                        "error": cleanup_error,
                    }
                )
                for record_id, binding in cleanup_bindings_by_record_id.items():
                    if record_id in deleted_ids:
                        self.db.delete(binding)
        elif unmanaged_duplicates or unmanaged_stale_records:
            duplicate_cleanup["status"] = "unmanaged_duplicates"
        self.db.commit()
        return {
            "workspace_id": workspace.id,
            "workspace_url": workspace.url,
            "table_id": table.table_id,
            "records_read": len(remote_records),
            "records_created": len(to_create),
            "records_updated": len(to_update),
            "records_skipped": records_skipped,
            "records_deleted": int(duplicate_cleanup["deleted"]),
            "records_written": len(to_create) + len(to_update),
            "duplicate_cleanup": duplicate_cleanup,
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
