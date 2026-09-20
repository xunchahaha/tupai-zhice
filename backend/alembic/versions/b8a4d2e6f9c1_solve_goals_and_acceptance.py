"""goals MEM-C3: persistent solve goals and acceptance reports

Revision ID: b8a4d2e6f9c1
Revises: d5e9f2a7c4b1
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8a4d2e6f9c1"
down_revision: str | None = "d5e9f2a7c4b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 持久目标（MEM-C3）：一句话目标拆成可逐项验收的 checklist，求解完成后
    # 由代码验收器出报告。latest_run_id 为展示用快捷指针，不设外键以避免与
    # solver_runs.goal_id 成环。
    op.create_table(
        "solve_goals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("schedule_set_id", sa.String(length=36), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("checklist", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("latest_run_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_by", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["schedule_set_id"],
            ["schedule_sets.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_solve_goals_schedule_set_id", "solve_goals", ["schedule_set_id"]
    )
    op.create_index("ix_solve_goals_status", "solve_goals", ["status"])
    op.create_index(
        "ix_solve_goals_scope_status", "solve_goals", ["schedule_set_id", "status"]
    )
    # 求解任务回链目标 + 最近一次验收报告（同一批迁移，见任务设计定稿第 3 条）。
    # SQLite 只支持 batch 模式下的约束变更，FK 必须带显式命名在 batch 内补建。
    with op.batch_alter_table("solver_runs") as batch_op:
        batch_op.add_column(sa.Column("goal_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("goal_report", sa.JSON(), nullable=True))
        batch_op.create_foreign_key(
            "fk_solver_runs_goal_id_solve_goals",
            "solve_goals",
            ["goal_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_solver_runs_goal_id", "solver_runs", ["goal_id"])


def downgrade() -> None:
    op.drop_index("ix_solver_runs_goal_id", table_name="solver_runs")
    with op.batch_alter_table("solver_runs") as batch_op:
        batch_op.drop_constraint("fk_solver_runs_goal_id_solve_goals", type_="foreignkey")
        batch_op.drop_column("goal_report")
        batch_op.drop_column("goal_id")
    op.drop_index("ix_solve_goals_scope_status", table_name="solve_goals")
    op.drop_index("ix_solve_goals_status", table_name="solve_goals")
    op.drop_index("ix_solve_goals_schedule_set_id", table_name="solve_goals")
    op.drop_table("solve_goals")
