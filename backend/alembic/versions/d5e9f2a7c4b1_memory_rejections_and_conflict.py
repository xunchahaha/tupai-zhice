"""memory MEM-C2: rejection memory and conflict flag

Revision ID: d5e9f2a7c4b1
Revises: a2c6e8b4d9f7
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5e9f2a7c4b1"
down_revision: str | None = "a2c6e8b4d9f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 拒绝记忆（MEM-C2 修正 4）：拒绝候选落库，同签名同证据不再复现。
    op.create_table(
        "preference_rejections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("schedule_set_id", sa.String(length=36), nullable=False),
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.String(length=50), nullable=False),
        sa.Column("predicate", sa.String(length=40), nullable=False),
        sa.Column("constraint", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column(
            "rejected_by", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["schedule_set_id"],
            ["schedule_sets.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_preference_rejections_subject",
        "preference_rejections",
        ["schedule_set_id", "subject_type", "subject_id", "predicate"],
    )
    op.create_index(
        "ix_preference_rejections_schedule_set_id",
        "preference_rejections",
        ["schedule_set_id"],
    )
    # 矛盾消解（MEM-C2 修正 4）：同主体同谓词旧条目在窗口重叠且约束互斥时打标。
    with op.batch_alter_table("preference_entries") as batch_op:
        batch_op.add_column(
            sa.Column("conflict", sa.Boolean(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("preference_entries") as batch_op:
        batch_op.drop_column("conflict")
    op.drop_index(
        "ix_preference_rejections_schedule_set_id", table_name="preference_rejections"
    )
    op.drop_index("ix_preference_rejections_subject", table_name="preference_rejections")
    op.drop_table("preference_rejections")
