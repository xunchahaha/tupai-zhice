"""goals MEM-D3: checklist revision history on solve goals

Revision ID: d7f2a9c4b8e1
Revises: e5c9a1d3f7b2
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7f2a9c4b8e1"
down_revision: str | None = "e5c9a1d3f7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 清单修订历史（MEM-D3）：PATCH /goals/{id}/checklist 每次保存把旧清单快照
    # 进 checklist_history，当前版本号 = len(history)+1（初始清单为 v1）。存量
    # 目标按 server_default 置空数组——从未修订过的清单没有历史，语义自洽。
    with op.batch_alter_table("solve_goals") as batch_op:
        batch_op.add_column(
            sa.Column("checklist_history", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("solve_goals") as batch_op:
        batch_op.drop_column("checklist_history")
