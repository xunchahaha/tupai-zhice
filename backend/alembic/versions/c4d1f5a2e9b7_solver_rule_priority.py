"""store solver rule priority recommendations

Revision ID: c4d1f5a2e9b7
Revises: 87feb44ecf54
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4d1f5a2e9b7"
down_revision: str | None = "87feb44ecf54"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite keeps the newly-added JSON defaults in the table definition.  They
    # are harmless (and make the migration safe to retry after an interrupted
    # run), so avoid ALTER COLUMN statements that SQLite does not support.
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("solver_runs")}
    if "priority_rule_ids" not in existing:
        op.add_column(
            "solver_runs",
            sa.Column("priority_rule_ids", sa.JSON(), nullable=False, server_default="[]"),
        )
    if "priority_explanations" not in existing:
        op.add_column(
            "solver_runs",
            sa.Column("priority_explanations", sa.JSON(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    op.drop_column("solver_runs", "priority_explanations")
    op.drop_column("solver_runs", "priority_rule_ids")
