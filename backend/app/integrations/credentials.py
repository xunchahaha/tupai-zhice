"""集成凭据存取：config 明文 JSON + secrets Fernet 加密 JSON（复用现有密钥机制）。

钉钉/企业微信 v1 凭据走设置页按 manifest config schema 直填（无管理端 OAuth
安装流，roadmap §2.3 的 integration_installations 多实例表留二期）。每个集成
一行，``integration_type`` 唯一；密钥字段集合由 ``SECRET_CONFIG_KEYS`` 声明，
保存时拆分——非密钥字段进 config JSON，密钥字段整体加密为一个 JSON 密文。

加密主密钥与飞书/AI 配置同一套约定（settings 显式 key 或自动生成的 key 文件）；
``_cipher`` 的解析逻辑与 services/feishu.py、services/ai.py 保持逐字同构——
strangler 迁移下先容忍第三份拷贝，收敛到公共模块留待二期。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cryptography.fernet import Fernet, InvalidToken

from ..models import IntegrationCredential

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..config import Settings

# 各集成声明为密钥（加密落库、GET 永不明文回传）的 config 字段。
SECRET_CONFIG_KEYS: Mapping[str, frozenset[str]] = {
    "dingtalk": frozenset({"app_secret"}),
    "wecom": frozenset({"corp_secret"}),
}


def secret_fields(integration_id: str) -> frozenset[str]:
    """该集成的密钥字段集合；未登记的集成没有密钥字段。"""

    return SECRET_CONFIG_KEYS.get(integration_id, frozenset())


class IntegrationCredentialError(RuntimeError):
    """凭据存取失败（主密钥格式不正确或密文校验失败）。"""


class IntegrationSecretCipher:
    def __init__(self, key: str) -> None:
        try:
            self.fernet = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise IntegrationCredentialError("集成凭据加密主密钥格式不正确") from exc

    def encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self.fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise IntegrationCredentialError("集成凭据密文校验失败，请重新配置") from exc


@dataclass(frozen=True)
class StoredIntegrationConfig:
    """解密后的完整配置：config 为明文 JSON，secrets 为解密后的密钥字段。"""

    config: dict[str, Any]
    secrets: dict[str, str]

    def merged(self) -> dict[str, Any]:
        return {**self.config, **self.secrets}


class CredentialStore:
    """按集成 id 读写凭据行；不负责 commit（由调用方与审计日志一并提交）。

    保存语义为合并：非密钥字段按传入值覆盖、传 ``None`` 表示清除该字段、
    未传保持不变；密钥字段传非空值才覆盖（表单留空 = 保持已存密钥）。
    """

    def __init__(self, settings: Settings, db: Session) -> None:
        self.settings = settings
        self.db = db

    def _row(self, integration_id: str) -> IntegrationCredential | None:
        return self.db.get(IntegrationCredential, integration_id)

    def _cipher(self) -> IntegrationSecretCipher:
        key = self.settings.integration_token_encryption_key.strip()
        if not key:
            path = self.settings.integration_token_key_file
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
        return IntegrationSecretCipher(key)

    @staticmethod
    def _decrypt_secrets(cipher: IntegrationSecretCipher, encrypted: str) -> dict[str, str]:
        if not encrypted:
            return {}
        payload: Any = json.loads(cipher.decrypt(encrypted))
        if not isinstance(payload, dict):
            raise IntegrationCredentialError("集成凭据密文内容异常，请重新配置")
        return {str(name): str(value) for name, value in payload.items()}

    def load(self, integration_id: str) -> StoredIntegrationConfig | None:
        row = self._row(integration_id)
        if row is None:
            return None
        secrets = self._decrypt_secrets(self._cipher(), row.secrets_encrypted)
        return StoredIntegrationConfig(config=dict(row.config or {}), secrets=secrets)

    def save(
        self,
        integration_id: str,
        config: Mapping[str, Any],
        *,
        configured_by: str | None = None,
    ) -> IntegrationCredential:
        cipher = self._cipher()
        row = self._row(integration_id)
        merged_config: dict[str, Any] = dict(row.config or {}) if row is not None else {}
        merged_secrets = self._decrypt_secrets(
            cipher, row.secrets_encrypted if row is not None else ""
        )
        for name, value in config.items():
            if name in secret_fields(integration_id):
                text = str(value).strip() if value is not None else ""
                if text:
                    merged_secrets[name] = text
            elif value is None:
                merged_config.pop(name, None)
            else:
                merged_config[name] = value
        if row is None:
            row = IntegrationCredential(
                integration_type=integration_id,
                config=merged_config,
                secrets_encrypted=cipher.encrypt(json.dumps(merged_secrets, ensure_ascii=False)),
                configured_by=configured_by,
            )
            self.db.add(row)
        else:
            row.config = merged_config
            row.secrets_encrypted = cipher.encrypt(
                json.dumps(merged_secrets, ensure_ascii=False)
            )
            if configured_by:
                row.configured_by = configured_by
        self.db.flush()
        return row

    def view(self, integration_id: str) -> dict[str, Any]:
        """脱敏视图：非密钥字段原样，密钥字段只给「是否已配置」布尔。"""

        keys = sorted(secret_fields(integration_id))
        row = self._row(integration_id)
        if row is None:
            return {
                "config": {},
                "secrets_configured": {key: False for key in keys},
                "updated_at": None,
            }
        secrets = self._decrypt_secrets(self._cipher(), row.secrets_encrypted)
        return {
            "config": dict(row.config or {}),
            "secrets_configured": {key: bool(secrets.get(key)) for key in keys},
            "updated_at": row.updated_at,
        }
