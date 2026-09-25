"""goals TC-4: persistent task context (goal.context JSON)

Revision ID: a7c9e1f3b5d7
Revises: e2b7f4a9c3d6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c9e1f3b5d7"
down_revision: str | None = "e2b7f4a9c3d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 任务上下文（TC-4，docs/roadmap/07-task-context.md §4.1）：solve_goals 新增
    # context JSON 列——当前工作状态（scope / soft_task_constraints /
    # work_draft_schedule_id）的唯一持久归宿。存量目标保持 NULL =「无上下文」，
    # 续办时按惰性初始化回填；硬约束的单一事实源仍是 goal.checklist，本列不承载
    # 验收口径（不参与验收乐观锁）。
    with op.batch_alter_table("solve_goals") as batch_op:
        batch_op.add_column(sa.Column("context", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("solve_goals") as batch_op:
        batch_op.drop_column("context")
