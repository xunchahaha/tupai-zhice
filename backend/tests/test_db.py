from __future__ import annotations

import pytest

from app.db import SQLITE_BUSY_TIMEOUT_MS, engine


def test_file_backed_sqlite_uses_wal_and_bounded_busy_timeout() -> None:
    if engine.dialect.name != "sqlite":
        pytest.skip("SQLite-only connection configuration")

    with engine.connect() as connection:
        journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
        busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()

    assert str(journal_mode).lower() == "wal"
    assert busy_timeout == SQLITE_BUSY_TIMEOUT_MS
