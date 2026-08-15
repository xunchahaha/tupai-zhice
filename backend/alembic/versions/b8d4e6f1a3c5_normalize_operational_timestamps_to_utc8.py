"""normalize operational SQLite timestamps to Asia/Shanghai

Revision ID: b8d4e6f1a3c5
Revises: f4b8c2d6e1a9
Create Date: 2026-08-15

SQLite silently drops ``tzinfo`` for ``DateTime(timezone=True)``.  Every
existing value in those columns was therefore a UTC wall-clock string.  The
application now persists operational timestamps as Asia/Shanghai wall-clock
time, so existing SQLite rows must be shifted once rather than mixed with new
UTC+8 values.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from alembic import op

revision: str = "b8d4e6f1a3c5"
down_revision: str | None = "f4b8c2d6e1a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

TIMESTAMP_COLUMNS: dict[str, tuple[str, ...]] = {
    "users": (
        "last_login_at",
        "password_changed_at",
        "locked_until",
        "created_at",
        "updated_at",
    ),
    "ai_provider_configurations": ("created_at", "updated_at"),
    "audit_logs": ("created_at",),
    "feishu_app_configurations": ("created_at", "updated_at"),
    "feishu_connections": (
        "access_expires_at",
        "refresh_expires_at",
        "created_at",
        "updated_at",
    ),
    "feishu_oauth_states": ("expires_at", "used_at", "created_at", "updated_at"),
    "schedule_sets": ("created_at", "updated_at"),
    "campuses": ("created_at", "updated_at"),
    "data_snapshots": ("created_at", "updated_at"),
    "feishu_workspaces": ("created_at", "updated_at"),
    "integration_syncs": ("created_at", "updated_at"),
    "rules": ("created_at", "updated_at"),
    "schedule_set_members": ("created_at", "updated_at"),
    "class_groups": ("created_at", "updated_at"),
    "course_sessions": ("created_at", "updated_at"),
    "feishu_table_bindings": ("created_at", "updated_at"),
    "rooms": ("created_at", "updated_at"),
    "solver_runs": ("created_at", "updated_at"),
    "teachers": ("created_at", "updated_at"),
    "time_slots": ("created_at", "updated_at"),
    "feishu_record_bindings": ("created_at", "updated_at"),
    "schedule_versions": ("published_at", "created_at", "updated_at"),
    "calendar_event_bindings": ("created_at", "updated_at"),
    "reschedule_events": ("created_at", "updated_at"),
    "schedule_assignments": ("created_at", "updated_at"),
}


def _parse_timestamp(value: object, assumed_timezone: tzinfo) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=assumed_timezone)
    return parsed.astimezone(assumed_timezone)


def _format_sqlite_timestamp(value: datetime) -> str:
    """Persist a timezone-free SQLite wall-clock string without losing microseconds."""
    return value.replace(tzinfo=None).isoformat(sep=" ")


def _rewrite_sqlite_timestamps(*, source_timezone: tzinfo, target_timezone: tzinfo) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        # Timezone-aware production databases preserve instants themselves.
        return
    inspector = sa.inspect(bind)
    known_tables = set(inspector.get_table_names())
    for table, columns in TIMESTAMP_COLUMNS.items():
        if table not in known_tables:
            continue
        known_columns = {item["name"] for item in inspector.get_columns(table)}
        for column in columns:
            if column not in known_columns:
                continue
            rows = bind.execute(
                sa.text(
                    f'SELECT id, "{column}" AS timestamp_value FROM "{table}" '
                    f'WHERE "{column}" IS NOT NULL'
                )
            ).mappings()
            updates = [
                {
                    "id": row["id"],
                    "timestamp_value": _format_sqlite_timestamp(
                        _parse_timestamp(row["timestamp_value"], source_timezone).astimezone(
                            target_timezone
                        )
                    ),
                }
                for row in rows
            ]
            if updates:
                bind.execute(
                    sa.text(f'UPDATE "{table}" SET "{column}" = :timestamp_value WHERE id = :id'),
                    updates,
                )


def upgrade() -> None:
    _rewrite_sqlite_timestamps(source_timezone=UTC, target_timezone=SHANGHAI_TZ)


def downgrade() -> None:
    _rewrite_sqlite_timestamps(source_timezone=SHANGHAI_TZ, target_timezone=UTC)
