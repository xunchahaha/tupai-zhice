"""ai provider options (reasoning effort)

Revision ID: c5e1a7b3d9f2
Revises: b4d8f2a6c1e3
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5e1a7b3d9f2"
down_revision: str | None = "b4d8f2a6c1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_provider_configurations") as batch_op:
        batch_op.add_column(sa.Column("options", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ai_provider_configurations") as batch_op:
        batch_op.drop_column("options")
