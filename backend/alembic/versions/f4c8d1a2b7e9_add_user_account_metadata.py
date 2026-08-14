"""add user account management metadata

Revision ID: f4c8d1a2b7e9
Revises: b7f4a2d91c63
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4c8d1a2b7e9"
down_revision: str | None = "b7f4a2d91c63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("created_by", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_users_created_by_users",
            "users",
            ["created_by"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("fk_users_created_by_users", type_="foreignkey")
        batch_op.drop_column("created_by")
        batch_op.drop_column("last_login_at")
