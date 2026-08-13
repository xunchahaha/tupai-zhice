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
    "aily:skill:write",
)


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
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )
    solver_workers: int = 1
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
    def feishu_environment_configured(self) -> bool:
        return bool(self.feishu_app_id and self.feishu_app_secret)

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
