from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_COMMAND = "uv run alembic upgrade head"


class DatabaseMigrationRequiredError(RuntimeError):
    """The application database exists but is not at the Alembic head."""


class Base(DeclarativeBase):
    pass


settings = get_settings()
if settings.database_url.startswith("sqlite:///"):
    database_path = settings.database_url.removeprefix("sqlite:///")
    Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


@lru_cache(maxsize=1)
def expected_migration_heads() -> frozenset[str]:
    """Read the repository's Alembic heads without mutating the application DB."""
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return frozenset(ScriptDirectory.from_config(config).get_heads())


def require_current_database_schema(database_engine: Engine = engine) -> None:
    """Fail before bootstrap writes when a database has not been migrated.

    ``Base.metadata.create_all`` is intentionally not a runtime fallback: it can
    create only part of a newer schema in an older SQLite database and then make
    the next Alembic upgrade impossible.  Schema creation and upgrades belong to
    the explicit Alembic command instead.
    """
    try:
        with database_engine.connect() as connection:
            if "alembic_version" not in set(inspect(connection).get_table_names()):
                raise DatabaseMigrationRequiredError(
                    "数据库架构未初始化或未通过 Alembic 迁移；"
                    f"请先执行 `{MIGRATION_COMMAND}` 后再启动服务。"
                )
            installed_heads = frozenset(MigrationContext.configure(connection).get_current_heads())
    except SQLAlchemyError as exc:
        raise DatabaseMigrationRequiredError(
            "无法读取数据库迁移状态；"
            f"请先执行 `{MIGRATION_COMMAND}` 后再启动服务。"
        ) from exc

    expected_heads = expected_migration_heads()
    if installed_heads != expected_heads:
        installed = ", ".join(sorted(installed_heads)) or "无"
        expected = ", ".join(sorted(expected_heads)) or "无"
        raise DatabaseMigrationRequiredError(
            "数据库迁移版本不是当前版本"
            f"（已安装: {installed}；期望: {expected}）；"
            f"请先执行 `{MIGRATION_COMMAND}` 后再启动服务。"
        )
