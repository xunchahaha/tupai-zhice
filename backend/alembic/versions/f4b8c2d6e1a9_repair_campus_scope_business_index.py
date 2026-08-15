"""repair campus business-id index after master-data scoping

Revision ID: f4b8c2d6e1a9
Revises: e9a3b7d5f1c2
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "f4b8c2d6e1a9"
down_revision: str | None = "e9a3b7d5f1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """The original schema created this as a *unique* index, not a constraint.

    The first schedule-set migration rebuilt campuses and introduced the
    intended ``(schedule_set_id, business_id)`` unique constraint, but SQLite
    preserved the legacy ``business_id``-only index.  Convert it to the
    non-unique lookup index declared by the ORM.
    """
    bind = op.get_bind()
    indexes = {item["name"]: item for item in sa.inspect(bind).get_indexes("campuses")}
    legacy = indexes.get("ix_campuses_business_id")
    if legacy and legacy.get("unique"):
        op.drop_index("ix_campuses_business_id", table_name="campuses")
        op.create_index("ix_campuses_business_id", "campuses", ["business_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT COUNT(*) FROM campuses WHERE schedule_set_id <> :default_id"),
        {"default_id": "default"},
    ).scalar_one():
        raise RuntimeError("Cannot restore a global campus key while non-default data exists")
    indexes = {item["name"]: item for item in sa.inspect(bind).get_indexes("campuses")}
    if "ix_campuses_business_id" in indexes:
        op.drop_index("ix_campuses_business_id", table_name="campuses")
    op.create_index("ix_campuses_business_id", "campuses", ["business_id"], unique=True)
