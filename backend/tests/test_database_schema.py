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
