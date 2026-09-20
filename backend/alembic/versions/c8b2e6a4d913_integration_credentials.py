"""add integration_credentials for dingtalk/wecom adapters

Revision ID: c8b2e6a4d913
Revises: a1c3e5d7b9f2
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8b2e6a4d913"
down_revision: str | None = "a1c3e5d7b9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_credentials",
        sa.Column("integration_type", sa.String(length=30), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("secrets_encrypted", sa.Text(), nullable=False),
        sa.Column("configured_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["configured_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("integration_type"),
    )


def downgrade() -> None:
    op.drop_table("integration_credentials")
