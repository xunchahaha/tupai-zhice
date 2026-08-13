"""store Feishu Aily application and skill identifiers

Revision ID: b7f4a2d91c63
Revises: e3b1c7a9d420
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7f4a2d91c63"
down_revision: str | None = "e3b1c7a9d420"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("feishu_app_configurations") as batch_op:
        batch_op.add_column(
            sa.Column("aily_app_id", sa.String(length=100), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("aily_skill_id", sa.String(length=100), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table("feishu_app_configurations") as batch_op:
        batch_op.drop_column("aily_skill_id")
        batch_op.drop_column("aily_app_id")
