from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FEISHU_RESOURCES = (
    "teachers",
    "class_groups",
    "rooms",
    "time_slots",
    "course_sessions",
    "rules",
    "schedule",
    "public_summary",
)

FEISHU_REQUIRED_SCOPES = (
    "offline_access",
    "base:app:create",
    "base:app:read",
    "base:table:create",
    "base:table:read",
    "base:table:update",
    "base:record:create",
    "base:record:retrieve",
    "base:record:update",
    "calendar:calendar.event:create",
    "calendar:calendar.event:update",
    "calendar:calendar.free_busy:read",
)

AILY_OPTIONAL_SCOPES = ("aily:skill:write",)

DEFAULT_JWT_SECRET = "dev-secret-change-before-deployment"
DEFAULT_ADMIN_PASSWORD = "tupai-demo"
DEFAULT_AILY_KEY = "aily-demo-key"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "途排智策 API"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'data' / 'tupai.db').as_posix()}"
    jwt_secret: str = "dev-secret-change-before-deployment"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 480
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "tupai-demo"
    aily_skill_api_key: str = "aily-demo-key"
    aily_app_id: str = ""
    aily_skill_id: str = ""
    ai_provider: str = "openai_compatible"
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_request_timeout_seconds: float = 60.0
    ai_token_encryption_key: str = ""
    ai_token_key_file: Path = PROJECT_ROOT / "data" / "secrets" / "ai.key"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )
    solver_workers: int = 1
    solver_search_workers: int = 8
    solver_time_limit_seconds: float = 30.0
    solver_random_seed: int = 2026

    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_token_encryption_key: str = ""
    feishu_token_key_file: Path = PROJECT_ROOT / "data" / "secrets" / "feishu.key"
    feishu_oauth_redirect_uri: str = (
        "http://127.0.0.1:8000/api/v1/integrations/feishu/oauth/callback"
    )
    frontend_url: str = "http://127.0.0.1:5173"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"production", "prod"}

    @property
    def insecure_defaults(self) -> list[str]:
        """仍在使用示例值的密钥。生产环境必须为空，否则拒绝启动。"""
        issues: list[str] = []
        if self.jwt_secret == DEFAULT_JWT_SECRET:
            issues.append("JWT_SECRET 仍是示例值")
        if len(self.jwt_secret) < 32:
            issues.append("JWT_SECRET 长度不足 32 位")
        if self.bootstrap_admin_password == DEFAULT_ADMIN_PASSWORD:
            issues.append("BOOTSTRAP_ADMIN_PASSWORD 仍是示例值")
        if self.aily_skill_api_key == DEFAULT_AILY_KEY:
            issues.append("AILY_SKILL_API_KEY 仍是示例值")
        return issues

    @property
    def feishu_environment_configured(self) -> bool:
        return bool(self.feishu_app_id and self.feishu_app_secret)

    @property
    def ai_environment_configured(self) -> bool:
        return bool(self.ai_base_url and self.ai_api_key and self.ai_model)

    @property
    def feishu_environment_missing_fields(self) -> list[str]:
        fields: list[str] = []
        if not self.feishu_app_id:
            fields.append("FEISHU_APP_ID")
        if not self.feishu_app_secret:
            fields.append("FEISHU_APP_SECRET")
        if not self.feishu_oauth_redirect_uri:
            fields.append("FEISHU_OAUTH_REDIRECT_URI")
        return fields


@lru_cache
def get_settings() -> Settings:
    return Settings()
