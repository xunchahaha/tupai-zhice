"""repair early master-data scope migration unique constraints

Revision ID: e9a3b7d5f1c2
Revises: e8f2a6c4d9b1
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
revision: str = "e9a3b7d5f1c2"
down_revision: str | None = "e8f2a6c4d9b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in ("teachers", "class_groups", "rooms", "time_slots", "course_sessions"):
        legacy = f"uq_{table}_campus_id"
        if legacy not in {item["name"] for item in inspector.get_unique_constraints(table)}:
            continue
        with op.batch_alter_table(table, recreate="always", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}) as batch_op:
            batch_op.drop_constraint(legacy, type_="unique")

def downgrade() -> None:
    pass
