"""goals MEM-D2: acceptance status on solve goals

Revision ID: e5c9a1d3f7b2
Revises: b8a4d2e6f9c1
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5c9a1d3f7b2"
down_revision: str | None = "b8a4d2e6f9c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 验收状态（MEM-D2/D6）：run completed 先置 pending，验收成功 → completed、
    # 异常 → failed（原因落 acceptance_detail，不再只打日志）。存量目标按
    # server_default 置 pending——没有报告就是「还没验收完」，语义自洽。
    with op.batch_alter_table("solve_goals") as batch_op:
        batch_op.add_column(
            sa.Column(
                "acceptance_status",
                sa.String(length=20),
                nullable=False,
                server_default="pending",
            )
        )
        batch_op.add_column(sa.Column("acceptance_detail", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("solve_goals") as batch_op:
        batch_op.drop_column("acceptance_detail")
        batch_op.drop_column("acceptance_status")
