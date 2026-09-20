"""add public_link_tokens for the public showcase layer

Revision ID: a1c3e5d7b9f2
Revises: b3e7f9a1c5d8
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1c3e5d7b9f2"
down_revision: str | None = "b3e7f9a1c5d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "public_link_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("schedule_set_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_hint", sa.String(length=8), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("campus_id", sa.String(length=36), nullable=True),
        sa.Column("resource_business_id", sa.String(length=50), nullable=True),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("show_teacher_names", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_count", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["campus_id"], ["campuses.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["schedule_set_id"], ["schedule_sets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_public_link_tokens_schedule_set_id"),
        "public_link_tokens",
        ["schedule_set_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_public_link_tokens_token_hash"),
        "public_link_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_public_link_tokens_expires_at"),
        "public_link_tokens",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_public_link_tokens_expires_at"), table_name="public_link_tokens"
    )
    op.drop_index(
        op.f("ix_public_link_tokens_token_hash"), table_name="public_link_tokens"
    )
    op.drop_index(
        op.f("ix_public_link_tokens_schedule_set_id"), table_name="public_link_tokens"
    )
    op.drop_table("public_link_tokens")
