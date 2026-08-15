"""add named schedule sets and per-set access control

Revision ID: c7d5e9a1b2f4
Revises: f9c2a7d41e83
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c7d5e9a1b2f4"
down_revision: str | None = "f9c2a7d41e83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCHEDULE_SET_ID = "default"


def _add_scope_column(table: str) -> None:
    with op.batch_alter_table(table) as batch_op:
        batch_op.add_column(
            sa.Column(
                "schedule_set_id",
                sa.String(length=36),
                nullable=False,
                server_default=SCHEDULE_SET_ID,
            )
        )
        batch_op.create_index(f"ix_{table}_schedule_set_id", ["schedule_set_id"], unique=False)
        batch_op.create_foreign_key(
            f"fk_{table}_schedule_set_id",
            "schedule_sets",
            ["schedule_set_id"],
            ["id"],
            ondelete="CASCADE",
        )


def upgrade() -> None:
    op.create_table(
        "schedule_sets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_schedule_sets_code", "schedule_sets", ["code"], unique=True)
    op.create_index("ix_schedule_sets_name", "schedule_sets", ["name"], unique=False)
    op.create_index("ix_schedule_sets_display_order", "schedule_sets", ["display_order"], unique=False)
    op.create_index("ix_schedule_sets_is_active", "schedule_sets", ["is_active"], unique=False)

    op.create_table(
        "schedule_set_members",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("schedule_set_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("access_role", sa.String(length=20), nullable=False, server_default="viewer"),
        sa.Column("granted_by", sa.String(length=36), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["schedule_set_id"], ["schedule_sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("schedule_set_id", "user_id"),
    )
    op.create_index("ix_schedule_set_members_schedule_set_id", "schedule_set_members", ["schedule_set_id"], unique=False)
    op.create_index("ix_schedule_set_members_user_id", "schedule_set_members", ["user_id"], unique=False)
    op.create_index("ix_schedule_set_members_is_active", "schedule_set_members", ["is_active"], unique=False)

    # Existing installations are one schedule.  Keep all historical rows in it
    # and grant the same effective per-set permission as the previous global role.
    op.execute(
        sa.text(
            "INSERT INTO schedule_sets "
            "(id, code, name, display_order, is_active, created_at, updated_at) "
            "VALUES ('default', 'SET001', '第一套课表', 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )

    for table in (
        "feishu_workspaces",
        "rules",
        "data_snapshots",
        "solver_runs",
        "schedule_versions",
        "reschedule_events",
        "integration_syncs",
    ):
        _add_scope_column(table)

    # Rule business IDs are user-facing.  They may repeat in different timetable
    # plans, but remain unique inside one plan.
    op.drop_index("ix_rules_business_id", table_name="rules")
    with op.batch_alter_table("rules", recreate="always") as batch_op:
        batch_op.create_unique_constraint(
            "uq_rules_schedule_set_business", ["schedule_set_id", "business_id"]
        )
        batch_op.create_index("ix_rules_business_id", ["business_id"], unique=False)

    # The old globally-unique version number becomes a per-schedule-set number.
    op.drop_index("ix_schedule_versions_version_no", table_name="schedule_versions")
    with op.batch_alter_table("schedule_versions", recreate="always") as batch_op:
        batch_op.create_unique_constraint(
            "uq_schedule_versions_schedule_set_version",
            ["schedule_set_id", "version_no"],
        )
        batch_op.create_index(
            "ix_schedule_versions_version_no", ["version_no"], unique=False
        )

    op.execute(
        sa.text(
            "INSERT INTO schedule_set_members "
            "(id, schedule_set_id, user_id, access_role, granted_by, is_active, created_at, updated_at) "
            "SELECT lower(hex(randomblob(16))), 'default', id, "
            "CASE role WHEN 'admin' THEN 'approver' WHEN 'approver' THEN 'approver' "
            "WHEN 'scheduler' THEN 'scheduler' ELSE 'viewer' END, id, is_active, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM users WHERE role <> 'admin'"
        )
    )


def downgrade() -> None:
    # Downgrade is intended for an empty/test database.  Historical rows cannot
    # be safely merged when two schedule sets already use the same version number.
    for table in (
        "integration_syncs",
        "reschedule_events",
        "schedule_versions",
        "solver_runs",
        "data_snapshots",
        "rules",
        "feishu_workspaces",
    ):
        with op.batch_alter_table(table, recreate="always") as batch_op:
            batch_op.drop_column("schedule_set_id")
    op.drop_index("ix_schedule_set_members_is_active", table_name="schedule_set_members")
    op.drop_index("ix_schedule_set_members_user_id", table_name="schedule_set_members")
    op.drop_index("ix_schedule_set_members_schedule_set_id", table_name="schedule_set_members")
    op.drop_table("schedule_set_members")
    op.drop_index("ix_schedule_sets_is_active", table_name="schedule_sets")
    op.drop_index("ix_schedule_sets_display_order", table_name="schedule_sets")
    op.drop_index("ix_schedule_sets_name", table_name="schedule_sets")
    op.drop_index("ix_schedule_sets_code", table_name="schedule_sets")
    op.drop_table("schedule_sets")
