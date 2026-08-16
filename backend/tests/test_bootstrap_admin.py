from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, Settings
from app.db import Base
from app.models import User
from app.security import hash_password, verify_password
from app.services.seed import bootstrap_admin


def test_bootstrap_creates_default_admin_and_preserves_a_custom_password() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        admin = bootstrap_admin(db, DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
        assert admin.username == DEFAULT_ADMIN_USERNAME
        assert admin.role == "admin"
        assert admin.is_active is True
        assert verify_password(DEFAULT_ADMIN_PASSWORD, admin.password_hash)

        admin.password_hash = hash_password("custom-admin-password-2026")
        admin.role = "viewer"
        admin.is_active = False
        db.commit()

        same_admin = bootstrap_admin(db, DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
        assert same_admin.id == admin.id
        assert verify_password("custom-admin-password-2026", same_admin.password_hash)
        assert same_admin.role == "viewer"
        assert same_admin.is_active is False


def test_bootstrap_upgrades_the_legacy_demo_password_and_restores_admin_access() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        user = User(
            username=DEFAULT_ADMIN_USERNAME,
            password_hash=hash_password("tupai-demo"),
            role="viewer",
            is_active=False,
            token_version=3,
            failed_login_count=4,
        )
        db.add(user)
        db.commit()

        # 旧部署的 .env 也可能仍传旧值；服务层再次兜底迁移到新默认密码。
        admin = bootstrap_admin(db, DEFAULT_ADMIN_USERNAME, "tupai-demo")
        assert admin.role == "admin"
        assert admin.is_active is True
        assert verify_password(DEFAULT_ADMIN_PASSWORD, admin.password_hash)
        assert verify_password("tupai-demo", admin.password_hash) is False
        assert admin.token_version == 4
        assert admin.failed_login_count == 0


def test_default_admin_credentials_do_not_block_a_production_bootstrap() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="production-jwt-secret-with-at-least-32-characters",
        aily_skill_api_key="production-aily-shared-secret",
        bootstrap_admin_username=DEFAULT_ADMIN_USERNAME,
        bootstrap_admin_password=DEFAULT_ADMIN_PASSWORD,
    )

    assert settings.uses_default_admin_password is True
    assert settings.insecure_defaults == []


def test_legacy_or_blank_environment_password_uses_the_new_default() -> None:
    legacy = Settings(_env_file=None, bootstrap_admin_password="tupai-demo")
    blank = Settings(_env_file=None, bootstrap_admin_password="   ")

    assert legacy.bootstrap_admin_password == DEFAULT_ADMIN_PASSWORD
    assert blank.bootstrap_admin_password == DEFAULT_ADMIN_PASSWORD
