"""preserve preprocessed course-session source dimensions

Revision ID: f9c2a7d41e83
Revises: e7a3c5d18f42
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f9c2a7d41e83"
down_revision: str | None = "e7a3c5d18f42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("course_sessions") as batch_op:
        batch_op.add_column(
            sa.Column("product_types", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("product_contexts", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("teacher_business_ids", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("lesson_names", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(sa.Column("stages", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(
            sa.Column("candidate_slot_ids", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column("candidate_clock_windows", sa.JSON(), nullable=False, server_default="[]")
        )
        batch_op.add_column(
            sa.Column(
                "candidate_room_business_ids", sa.JSON(), nullable=False, server_default="[]"
            )
        )
        batch_op.add_column(
            sa.Column("source_variant_count", sa.Integer(), nullable=False, server_default="1")
        )


def downgrade() -> None:
    with op.batch_alter_table("course_sessions") as batch_op:
        batch_op.drop_column("source_variant_count")
        batch_op.drop_column("candidate_room_business_ids")
        batch_op.drop_column("candidate_clock_windows")
        batch_op.drop_column("candidate_slot_ids")
        batch_op.drop_column("stages")
        batch_op.drop_column("lesson_names")
        batch_op.drop_column("teacher_business_ids")
        batch_op.drop_column("product_contexts")
        batch_op.drop_column("product_types")
