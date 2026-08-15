"""Application timestamp policy.

Operational records in TuPai ZhiCe are presented and, for SQLite, persisted
as Asia/Shanghai (UTC+8) wall-clock time.  External protocols such as JWT and
Feishu OAuth still use UTC instants where their specifications require it;
the conversion helpers below keep that boundary explicit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.sql.type_api import TypeEngine
from sqlalchemy.types import TypeDecorator

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def shanghai_now() -> datetime:
    """Return the current application wall-clock time with an explicit UTC+8 offset."""
    return datetime.now(SHANGHAI_TZ)


def as_shanghai(value: datetime | None) -> datetime | None:
    """Normalize a timestamp for application storage and user-facing output.

    SQLite does not retain a timezone offset for ``DateTime`` values.  After
    the UTC-to-UTC+8 migration, a timezone-naive SQLite value is therefore an
    Asia/Shanghai wall-clock value, not an implicit UTC timestamp.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)


def as_utc(value: datetime | None) -> datetime | None:
    """Convert an application timestamp to a UTC instant for protocol checks."""
    local_value = as_shanghai(value)
    return local_value.astimezone(UTC) if local_value is not None else None


class ShanghaiDateTime(TypeDecorator[datetime]):
    """DateTime column that round-trips operational values in Asia/Shanghai.

    PostgreSQL and other timezone-aware databases keep an absolute instant,
    while SQLite stores a timezone-free text value.  Returning an explicit
    Asia/Shanghai datetime in both cases keeps ORM responses, API JSON, and
    Bitable projections on the same UTC+8 policy.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[datetime]:
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        del dialect
        return as_shanghai(value)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        del dialect
        return as_shanghai(value)
