"""add preference memory entries and reschedule attribution

Revision ID: b3e7f9a1c5d8
Revises: c5e2a8b7d901
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3e7f9a1c5d8"
down_revision: str | None = "c5e2a8b7d901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "preference_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("schedule_set_id", sa.String(length=36), nullable=False),
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.String(length=50), nullable=False),
        sa.Column("predicate", sa.String(length=40), nullable=False),
        sa.Column("constraint", sa.JSON(), nullable=False),
        sa.Column("modality", sa.String(length=10), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["schedule_set_id"], ["schedule_sets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_preference_entries_schedule_set_id"),
        "preference_entries",
        ["schedule_set_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_preference_entries_status"),
        "preference_entries",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_preference_entries_subject_id"),
        "preference_entries",
        ["subject_id"],
        unique=False,
    )
    op.create_index(
        "ix_preference_entries_scope_status",
        "preference_entries",
        ["schedule_set_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_preference_entries_subject",
        "preference_entries",
        ["schedule_set_id", "subject_type", "subject_id"],
        unique=False,
    )
    with op.batch_alter_table("reschedule_events") as batch_op:
        batch_op.add_column(sa.Column("declared_reason", sa.String(length=80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("reschedule_events") as batch_op:
        batch_op.drop_column("declared_reason")
    op.drop_index("ix_preference_entries_subject", table_name="preference_entries")
    op.drop_index("ix_preference_entries_scope_status", table_name="preference_entries")
    op.drop_index(
        op.f("ix_preference_entries_subject_id"), table_name="preference_entries"
    )
    op.drop_index(op.f("ix_preference_entries_status"), table_name="preference_entries")
    op.drop_index(
        op.f("ix_preference_entries_schedule_set_id"), table_name="preference_entries"
    )
    op.drop_table("preference_entries")
