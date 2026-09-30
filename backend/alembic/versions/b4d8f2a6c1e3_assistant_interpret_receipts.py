"""add assistant_interpret_receipts for idempotent interpret side effects

Revision ID: b4d8f2a6c1e3
Revises: a7c9e1f3b5d7
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4d8f2a6c1e3"
down_revision: str | None = "a7c9e1f3b5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 解析接口会直接执行用户明确授权的记忆动作：同一条用户指令在流式失败后回退到
    # 同步接口重试时，必须只执行一次。回执按（方案, 请求标识）唯一，存下当时的完整
    # 响应，重试直接返回原结果，不再调用模型、不再执行动作。
    op.create_table(
        "assistant_interpret_receipts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("schedule_set_id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["schedule_set_id"], ["schedule_sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("schedule_set_id", "request_id"),
    )
    op.create_index(
        op.f("ix_assistant_interpret_receipts_schedule_set_id"),
        "assistant_interpret_receipts",
        ["schedule_set_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_assistant_interpret_receipts_schedule_set_id"),
        table_name="assistant_interpret_receipts",
    )
    op.drop_table("assistant_interpret_receipts")
