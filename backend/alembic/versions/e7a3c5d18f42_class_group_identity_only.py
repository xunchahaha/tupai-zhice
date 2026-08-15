"""class groups keep identity only; index the batch filter columns

走班制下班级的班型/业务线/教师是课次属性的投影，不是班级自己的属性。旧的三个单值列
是导入时用众数猜出来的，猜出来的组合彼此不保证来自同一批课次，删掉它们，改为从
course_sessions 实时聚合。

顺带补上「按筛选条件批量」和删除前引用检查要走的索引。SQLite 不支持 DROP COLUMN，
必须用 batch_alter_table 重建表。

Revision ID: e7a3c5d18f42
Revises: d3a8f1e6b527
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7a3c5d18f42"
down_revision: str | None = "d3a8f1e6b527"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("class_groups") as batch_op:
        batch_op.drop_column("grade")
        batch_op.drop_column("subject")
        batch_op.drop_column("teacher_business_id")

    with op.batch_alter_table("course_sessions") as batch_op:
        batch_op.create_index(
            op.f("ix_course_sessions_class_business_id"), ["class_business_id"], unique=False
        )
        batch_op.create_index(
            op.f("ix_course_sessions_teacher_business_id"), ["teacher_business_id"], unique=False
        )
        batch_op.create_index(
            op.f("ix_course_sessions_lesson_date"), ["lesson_date"], unique=False
        )

    with op.batch_alter_table("schedule_assignments") as batch_op:
        batch_op.create_index(
            op.f("ix_schedule_assignments_course_session_id"),
            ["course_session_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("schedule_assignments") as batch_op:
        batch_op.drop_index(op.f("ix_schedule_assignments_course_session_id"))

    with op.batch_alter_table("course_sessions") as batch_op:
        batch_op.drop_index(op.f("ix_course_sessions_lesson_date"))
        batch_op.drop_index(op.f("ix_course_sessions_teacher_business_id"))
        batch_op.drop_index(op.f("ix_course_sessions_class_business_id"))

    # 回滚只能把列加回来，猜出来的众数值无法还原，一律留空。
    with op.batch_alter_table("class_groups") as batch_op:
        batch_op.add_column(
            sa.Column("grade", sa.String(length=50), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("subject", sa.String(length=80), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column(
                "teacher_business_id", sa.String(length=40), nullable=False, server_default=""
            )
        )
