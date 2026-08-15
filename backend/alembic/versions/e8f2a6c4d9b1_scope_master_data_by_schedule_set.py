"""scope all master data to named schedule sets

Revision ID: e8f2a6c4d9b1
Revises: c7d5e9a1b2f4
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e8f2a6c4d9b1"
down_revision: str | None = "c7d5e9a1b2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEDULE_SET_ID = "default"
_MASTER_TABLES = ("campuses", "teachers", "class_groups", "rooms", "time_slots", "course_sessions")


def _scope_master_table(
    table: str, unique_columns: list[str], *, business_id_column: object
) -> None:
    """Add scope and replace the old global identity constraint.

    The old constraints were unnamed SQLite UNIQUE auto-indexes.  The naming
    convention assigns stable temporary names during batch reflection so each
    can be removed as the table is rebuilt.
    """
    with op.batch_alter_table(
        table,
        recreate="always",
        naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
        reflect_args=[sa.Column("business_id", business_id_column, nullable=False)],
    ) as batch_op:
        if table != "campuses":
            batch_op.drop_constraint(f"uq_{table}_campus_id", type_="unique")
        batch_op.add_column(
            sa.Column(
                "schedule_set_id",
                sa.String(length=36),
                nullable=False,
                server_default=SCHEDULE_SET_ID,
            )
        )
        batch_op.create_foreign_key(
            f"fk_{table}_schedule_set_id",
            "schedule_sets",
            ["schedule_set_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index(f"ix_{table}_schedule_set_id", ["schedule_set_id"], unique=False)
        batch_op.create_unique_constraint(f"uq_{table}_schedule_scope_business", unique_columns)


def upgrade() -> None:
    _scope_master_table(
        "campuses", ["schedule_set_id", "business_id"], business_id_column=sa.String(length=40)
    )
    for table in ("teachers", "class_groups", "rooms", "time_slots", "course_sessions"):
        _scope_master_table(
            table,
            ["schedule_set_id", "campus_id", "business_id"],
            business_id_column=sa.String(length=50 if table == "course_sessions" else 40),
        )


def downgrade() -> None:
    # Global keys cannot represent two independent master-data sets.  Refuse a
    # destructive collapse once non-default data exists.
    bind = op.get_bind()
    for table in _MASTER_TABLES:
        if bind.execute(
            sa.text(f"SELECT COUNT(*) FROM {table} WHERE schedule_set_id <> :default_id"),
            {"default_id": SCHEDULE_SET_ID},
        ).scalar_one():
            raise RuntimeError("Cannot downgrade master-data scoping while non-default data exists")
    for table in _MASTER_TABLES:
        with op.batch_alter_table(table, recreate="always") as batch_op:
            batch_op.drop_index(f"ix_{table}_schedule_set_id")
            batch_op.drop_constraint(f"uq_{table}_schedule_scope_business", type_="unique")
            batch_op.drop_constraint(f"fk_{table}_schedule_set_id", type_="foreignkey")
            batch_op.drop_column("schedule_set_id")
            if table == "campuses":
                batch_op.create_unique_constraint("uq_campuses_business_id", ["business_id"])
            else:
                batch_op.create_unique_constraint(
                    f"uq_{table}_campus_business", ["campus_id", "business_id"]
                )
