"""store the human-readable explanation of a solver run

Revision ID: d3a8f1e6b527
Revises: b6e1d3f7c204
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3a8f1e6b527"
down_revision: str | None = "b6e1d3f7c204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("solver_runs") as batch_op:
        batch_op.add_column(sa.Column("explanation", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("solver_runs") as batch_op:
        batch_op.drop_column("explanation")
