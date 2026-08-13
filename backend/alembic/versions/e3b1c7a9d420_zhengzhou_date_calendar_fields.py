"""add Zhengzhou date-aware scheduling and calendar mapping fields

Revision ID: e3b1c7a9d420
Revises: c0d7e2f4a1b6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e3b1c7a9d420"
down_revision: str | None = "c0d7e2f4a1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("teachers") as batch_op:
        batch_op.add_column(sa.Column("calendar_user_id", sa.String(length=120), nullable=True))

    with op.batch_alter_table("course_sessions") as batch_op:
        batch_op.add_column(
            sa.Column("source_row_id", sa.String(length=64), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("business_line", sa.String(length=40), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("product_type", sa.String(length=120), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("calendar_user_id", sa.String(length=120), nullable=True))
        batch_op.add_column(
            sa.Column("fixed_start_time", sa.String(length=10), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("fixed_end_time", sa.String(length=10), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("original_room_business_id", sa.String(length=40), nullable=True)
        )
        batch_op.create_index("ix_course_sessions_source_row_id", ["source_row_id"])
        batch_op.create_index("ix_course_sessions_business_line", ["business_line"])
        batch_op.create_index("ix_course_sessions_product_type", ["product_type"])

    with op.batch_alter_table("schedule_assignments") as batch_op:
        batch_op.add_column(sa.Column("lesson_date", sa.Date(), nullable=True))

    # Existing installations already keep the authoritative lesson date on the
    # course row. Preserve it when adding the date-aware assignment column so a
    # normal Alembic upgrade does not require an immediate workbook re-import.
    op.execute(
        sa.text(
            """
            UPDATE schedule_assignments
            SET lesson_date = (
                SELECT course_sessions.lesson_date
                FROM course_sessions
                WHERE course_sessions.id = schedule_assignments.course_session_id
            )
            WHERE lesson_date IS NULL
            """
        )
    )

    op.create_table(
        "calendar_event_bindings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("schedule_version_id", sa.String(length=36), nullable=False),
        sa.Column("course_session_id", sa.String(length=36), nullable=False),
        sa.Column("calendar_id", sa.String(length=120), nullable=False),
        sa.Column("event_id", sa.String(length=120), nullable=False),
        sa.Column("calendar_user_id", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["course_session_id"], ["course_sessions.id"]),
        sa.ForeignKeyConstraint(["schedule_version_id"], ["schedule_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("schedule_version_id", "course_session_id"),
    )
    op.create_index(
        "ix_calendar_event_bindings_schedule_version_id",
        "calendar_event_bindings",
        ["schedule_version_id"],
    )
    op.create_index(
        "ix_calendar_event_bindings_course_session_id",
        "calendar_event_bindings",
        ["course_session_id"],
    )
    op.create_index(
        "ix_calendar_event_bindings_calendar_user_id",
        "calendar_event_bindings",
        ["calendar_user_id"],
    )
    op.create_index(
        "ix_calendar_event_bindings_idempotency_key",
        "calendar_event_bindings",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_calendar_event_bindings_status", "calendar_event_bindings", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_calendar_event_bindings_status", table_name="calendar_event_bindings")
    op.drop_index(
        "ix_calendar_event_bindings_idempotency_key", table_name="calendar_event_bindings"
    )
    op.drop_index(
        "ix_calendar_event_bindings_calendar_user_id", table_name="calendar_event_bindings"
    )
    op.drop_index(
        "ix_calendar_event_bindings_course_session_id", table_name="calendar_event_bindings"
    )
    op.drop_index(
        "ix_calendar_event_bindings_schedule_version_id", table_name="calendar_event_bindings"
    )
    op.drop_table("calendar_event_bindings")
    with op.batch_alter_table("schedule_assignments") as batch_op:
        batch_op.drop_column("lesson_date")
    with op.batch_alter_table("course_sessions") as batch_op:
        batch_op.drop_index("ix_course_sessions_product_type")
        batch_op.drop_index("ix_course_sessions_business_line")
        batch_op.drop_index("ix_course_sessions_source_row_id")
        batch_op.drop_column("original_room_business_id")
        batch_op.drop_column("fixed_end_time")
        batch_op.drop_column("fixed_start_time")
        batch_op.drop_column("calendar_user_id")
        batch_op.drop_column("product_type")
        batch_op.drop_column("business_line")
        batch_op.drop_column("source_row_id")
    with op.batch_alter_table("teachers") as batch_op:
        batch_op.drop_column("calendar_user_id")
