"""use live mode as the integration sync default

Revision ID: d8e2f7a1c6b4
Revises: c4d1f5a2e9b7
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d8e2f7a1c6b4"
down_revision: str | None = "c4d1f5a2e9b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("integration_syncs") as batch_op:
        batch_op.alter_column("mode", server_default="live")


def downgrade() -> None:
    with op.batch_alter_table("integration_syncs") as batch_op:
        batch_op.alter_column("mode", server_default="unconfigured")
