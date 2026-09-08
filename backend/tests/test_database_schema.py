from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from app.db import DatabaseMigrationRequiredError, require_current_database_schema


@pytest.mark.parametrize("applied_revision", [None, "not-a-current-revision"])
def test_schema_guard_explains_how_to_upgrade_unmigrated_databases(
    tmp_path, applied_revision: str | None
) -> None:
    database_engine = create_engine(f"sqlite:///{(tmp_path / 'unmigrated.db').as_posix()}")
    if applied_revision is not None:
        with database_engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO alembic_version (version_num) VALUES (?)", (applied_revision,)
            )

    with pytest.raises(DatabaseMigrationRequiredError, match=r"uv run alembic upgrade head"):
        require_current_database_schema(database_engine)


def test_integration_database_is_built_from_current_alembic_head() -> None:
    require_current_database_schema()


def test_active_course_migration_preserves_existing_rows(tmp_path, monkeypatch) -> None:
    from datetime import date, datetime
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.config import Config

    from alembic import command

    root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'existing.db').as_posix()}"
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "b8d4e6f1a3c5")
    engine = sa.create_engine(url)
    old = sa.Table("course_sessions", sa.MetaData(), autoload_with=engine)
    values = {}
    for column in old.columns:
        if column.nullable or column.server_default is not None:
            continue
        if isinstance(column.type, sa.Boolean):
            value = False
        elif isinstance(column.type, sa.Integer | sa.Float):
            value = 1
        elif isinstance(column.type, sa.DateTime):
            value = datetime(2026, 9, 7)
        elif isinstance(column.type, sa.Date):
            value = date(2026, 9, 7)
        elif isinstance(column.type, sa.JSON):
            value = []
        else:
            value = "preserved"
        values[column.name] = value
    with engine.begin() as connection:
        connection.execute(old.insert().values(**values))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT id, business_id, is_active FROM course_sessions"
        ).one()
        assert tuple(row) == ("preserved", "preserved", 1)
    engine.dispose()
