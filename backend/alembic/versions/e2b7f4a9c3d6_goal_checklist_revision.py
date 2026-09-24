"""goals MEM-F/F2: persisted checklist revision for conditional write-back

Revision ID: e2b7f4a9c3d6
Revises: d7f2a9c4b8e1
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e2b7f4a9c3d6"
down_revision: str | None = "d7f2a9c4b8e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 清单修订版本号（MEM-F/F2 收口）：把「len(checklist_history)+1」的派生
    # 版本落成持久化计数列，作为验收写回条件 UPDATE 的乐观锁。存量目标按旧
    # 口径回填（历史长度+1），保证迁移后版本计数与既有报告
    # meta.checklist_version 对齐；新目标由应用层 default=1 起步。
    with op.batch_alter_table("solve_goals") as batch_op:
        batch_op.add_column(
            sa.Column("checklist_revision", sa.Integer(), nullable=False, server_default="1")
        )
    op.execute(
        "UPDATE solve_goals SET checklist_revision = "
        "json_array_length(COALESCE(checklist_history, '[]')) + 1"
    )


def downgrade() -> None:
    with op.batch_alter_table("solve_goals") as batch_op:
        batch_op.drop_column("checklist_revision")
