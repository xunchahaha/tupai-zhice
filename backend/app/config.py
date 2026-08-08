from __future__ import annotations

import json
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
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )
    solver_workers: int = 1
    solver_time_limit_seconds: float = 30.0
    solver_random_seed: int = 2026

    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_bitable_app_token: str = ""
    feishu_table_map: dict[str, str] = Field(default_factory=dict)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("feishu_table_map", mode="before")
    @classmethod
    def parse_table_map(cls, value: object) -> object:
        if isinstance(value, str):
            if not value.strip():
                return {}
            return json.loads(value)
        return value

    @property
    def feishu_credentials_configured(self) -> bool:
        return bool(self.feishu_app_id and self.feishu_app_secret and self.feishu_bitable_app_token)

    @property
    def feishu_missing_fields(self) -> list[str]:
        fields: list[str] = []
        if not self.feishu_app_id:
            fields.append("FEISHU_APP_ID")
        if not self.feishu_app_secret:
            fields.append("FEISHU_APP_SECRET")
        if not self.feishu_bitable_app_token:
            fields.append("FEISHU_BITABLE_APP_TOKEN")
        return fields

    @property
    def feishu_missing_resources(self) -> list[str]:
        return [
            resource for resource in FEISHU_RESOURCES if not self.feishu_table_map.get(resource)
        ]

    @property
    def feishu_table_mapping_configured(self) -> bool:
        return not self.feishu_missing_resources

    @property
    def feishu_configured(self) -> bool:
        return self.feishu_credentials_configured and self.feishu_table_mapping_configured


@lru_cache
def get_settings() -> Settings:
    return Settings()
