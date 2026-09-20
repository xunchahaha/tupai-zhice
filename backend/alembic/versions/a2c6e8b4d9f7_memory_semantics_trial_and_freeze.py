"""memory semantics MEM-C1: trial authorization and snapshot freezing

Revision ID: a2c6e8b4d9f7
Revises: b7e4f9a2c6d1
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a2c6e8b4d9f7"
down_revision: str | None = "b7e4f9a2c6d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("preference_entries") as batch_op:
        batch_op.add_column(
            sa.Column("trial_authorized", sa.Boolean(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("trial_until", sa.Date(), nullable=True))
    # 偏好记忆使用情况冻结进求解任务（MEM-C1 修正 6）。
    with op.batch_alter_table("solver_runs") as batch_op:
        batch_op.add_column(sa.Column("memory_usage", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("solver_runs") as batch_op:
        batch_op.drop_column("memory_usage")
    with op.batch_alter_table("preference_entries") as batch_op:
        batch_op.drop_column("trial_until")
        batch_op.drop_column("trial_authorized")
