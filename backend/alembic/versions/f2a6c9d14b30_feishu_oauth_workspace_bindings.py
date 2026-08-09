"""add Feishu OAuth, workspace and record bindings

Revision ID: f2a6c9d14b30
Revises: d8e2f7a1c6b4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f2a6c9d14b30"
down_revision: str | None = "d8e2f7a1c6b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feishu_connections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_feishu_connections_access_expires_at"),
        "feishu_connections",
        ["access_expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_feishu_connections_status"),
        "feishu_connections",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_feishu_connections_user_id"),
        "feishu_connections",
        ["user_id"],
        unique=True,
    )
    op.create_table(
        "feishu_oauth_states",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("pkce_verifier_encrypted", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_feishu_oauth_states_expires_at"),
        "feishu_oauth_states",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_feishu_oauth_states_state_hash"),
        "feishu_oauth_states",
        ["state_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_feishu_oauth_states_user_id"),
        "feishu_oauth_states",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "feishu_workspaces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("connection_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("app_token", sa.String(length=100), nullable=False),
        sa.Column("default_table_id", sa.String(length=100), nullable=False),
        sa.Column("folder_token", sa.String(length=100), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["feishu_connections.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("app_token"),
    )
    op.create_index(
        op.f("ix_feishu_workspaces_connection_id"),
        "feishu_workspaces",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_feishu_workspaces_status"),
        "feishu_workspaces",
        ["status"],
        unique=False,
    )
    op.create_table(
        "feishu_table_bindings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("resource", sa.String(length=40), nullable=False),
        sa.Column("table_name", sa.String(length=80), nullable=False),
        sa.Column("table_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["feishu_workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "resource"),
        sa.UniqueConstraint("workspace_id", "table_id"),
    )
    op.create_index(
        op.f("ix_feishu_table_bindings_workspace_id"),
        "feishu_table_bindings",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "feishu_record_bindings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("table_binding_id", sa.String(length=36), nullable=False),
        sa.Column("business_key", sa.String(length=180), nullable=False),
        sa.Column("record_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["table_binding_id"], ["feishu_table_bindings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("table_binding_id", "business_key"),
        sa.UniqueConstraint("table_binding_id", "record_id"),
    )
    op.create_index(
        op.f("ix_feishu_record_bindings_table_binding_id"),
        "feishu_record_bindings",
        ["table_binding_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_feishu_record_bindings_table_binding_id"),
        table_name="feishu_record_bindings",
    )
    op.drop_table("feishu_record_bindings")
    op.drop_index(
        op.f("ix_feishu_table_bindings_workspace_id"),
        table_name="feishu_table_bindings",
    )
    op.drop_table("feishu_table_bindings")
    op.drop_index(op.f("ix_feishu_workspaces_status"), table_name="feishu_workspaces")
    op.drop_index(op.f("ix_feishu_workspaces_connection_id"), table_name="feishu_workspaces")
    op.drop_table("feishu_workspaces")
    op.drop_index(op.f("ix_feishu_oauth_states_user_id"), table_name="feishu_oauth_states")
    op.drop_index(op.f("ix_feishu_oauth_states_state_hash"), table_name="feishu_oauth_states")
    op.drop_index(op.f("ix_feishu_oauth_states_expires_at"), table_name="feishu_oauth_states")
    op.drop_table("feishu_oauth_states")
    op.drop_index(op.f("ix_feishu_connections_user_id"), table_name="feishu_connections")
    op.drop_index(op.f("ix_feishu_connections_status"), table_name="feishu_connections")
    op.drop_index(op.f("ix_feishu_connections_access_expires_at"), table_name="feishu_connections")
    op.drop_table("feishu_connections")
