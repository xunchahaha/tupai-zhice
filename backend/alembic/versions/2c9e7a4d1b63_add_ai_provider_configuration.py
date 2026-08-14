"""add ai provider configuration

Revision ID: 2c9e7a4d1b63
Revises: f4c8d1a2b7e9
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2c9e7a4d1b63"
down_revision: str | None = "f4c8d1a2b7e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_configurations",
        sa.Column("id", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("configured_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["configured_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("ai_provider_configurations")
