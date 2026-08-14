"""mark teachers that represent a teaching group rather than one person

Revision ID: a4f7b2c9e150
Revises: 2c9e7a4d1b63
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a4f7b2c9e150"
down_revision: str | None = "2c9e7a4d1b63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("teachers") as batch_op:
        batch_op.add_column(
            sa.Column("is_group", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("teachers") as batch_op:
        batch_op.drop_column("is_group")
