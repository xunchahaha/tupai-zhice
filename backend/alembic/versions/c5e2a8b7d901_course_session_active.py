"""Separate current scheduling demands from retained historical courses."""

import sqlalchemy as sa

from alembic import op

revision = "c5e2a8b7d901"
down_revision = "b8d4e6f1a3c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "course_sessions",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("course_sessions", "is_active")
