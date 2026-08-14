"""align master data fields with the official schedule source

Revision ID: c0d7e2f4a1b6
Revises: f2a6c9d14b30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c0d7e2f4a1b6"
down_revision: str | None = "a9e7c4f20d61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("teachers") as batch_op:
        batch_op.drop_column("max_hours")
        batch_op.drop_column("unavailable_slot_ids")
        batch_op.drop_column("preferred_slot_ids")
        batch_op.drop_column("data_level")

    with op.batch_alter_table("class_groups") as batch_op:
        batch_op.drop_column("student_count")
        batch_op.drop_column("priority")
        batch_op.drop_column("required_devices")

    with op.batch_alter_table("rooms") as batch_op:
        batch_op.drop_column("capacity")
        batch_op.drop_column("devices")
        batch_op.drop_column("available_slot_ids")

    with op.batch_alter_table("course_sessions") as batch_op:
        batch_op.drop_column("student_count")
        batch_op.drop_column("required_devices")
        batch_op.add_column(
            sa.Column("lesson_name", sa.String(length=120), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("schedule_source", sa.String(length=80), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("stage", sa.String(length=40), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("planned_sessions", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("planned_hours", sa.Float(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("session_no", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("lesson_date", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("course_sessions") as batch_op:
        batch_op.drop_column("lesson_date")
        batch_op.drop_column("session_no")
        batch_op.drop_column("planned_hours")
        batch_op.drop_column("planned_sessions")
        batch_op.drop_column("stage")
        batch_op.drop_column("schedule_source")
        batch_op.drop_column("lesson_name")
        batch_op.add_column(sa.Column("required_devices", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("student_count", sa.Integer(), nullable=True))

    with op.batch_alter_table("rooms") as batch_op:
        batch_op.add_column(sa.Column("available_slot_ids", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("devices", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("capacity", sa.Integer(), nullable=True))

    with op.batch_alter_table("class_groups") as batch_op:
        batch_op.add_column(sa.Column("required_devices", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("priority", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("student_count", sa.Integer(), nullable=True))

    with op.batch_alter_table("teachers") as batch_op:
        batch_op.add_column(sa.Column("data_level", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("preferred_slot_ids", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("unavailable_slot_ids", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("max_hours", sa.Integer(), nullable=True))
