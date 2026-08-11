"""add administrator-managed Feishu app configuration

Revision ID: a9e7c4f20d61
Revises: f2a6c9d14b30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9e7c4f20d61"
down_revision: str | None = "f2a6c9d14b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feishu_app_configurations",
        sa.Column("id", sa.String(length=30), nullable=False),
        sa.Column("app_id", sa.String(length=100), nullable=False),
        sa.Column("app_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("oauth_redirect_uri", sa.String(length=500), nullable=False),
        sa.Column("frontend_url", sa.String(length=500), nullable=False),
        sa.Column("configured_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["configured_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("feishu_app_configurations")
