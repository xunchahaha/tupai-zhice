"""add import_mapping_history for header-fingerprint mapping recall

Revision ID: b7e4f9a2c6d1
Revises: c8b2e6a4d913
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e4f9a2c6d1"
down_revision: str | None = "c8b2e6a4d913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_mapping_history",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("schedule_set_id", sa.String(length=36), nullable=False),
        sa.Column("header_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("mapping", sa.JSON(), nullable=False),
        sa.Column("sheet_name", sa.String(length=255), nullable=False),
        sa.Column("used_count", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["schedule_set_id"], ["schedule_sets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("schedule_set_id", "header_fingerprint"),
    )
    op.create_index(
        op.f("ix_import_mapping_history_header_fingerprint"),
        "import_mapping_history",
        ["header_fingerprint"],
        unique=False,
    )
    op.create_index(
        op.f("ix_import_mapping_history_schedule_set_id"),
        "import_mapping_history",
        ["schedule_set_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_import_mapping_history_schedule_set_id"),
        table_name="import_mapping_history",
    )
    op.drop_index(
        op.f("ix_import_mapping_history_header_fingerprint"),
        table_name="import_mapping_history",
    )
    op.drop_table("import_mapping_history")
