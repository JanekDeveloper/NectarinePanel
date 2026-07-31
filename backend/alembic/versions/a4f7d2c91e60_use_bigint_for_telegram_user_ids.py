"""Use 64-bit storage for Telegram user IDs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4f7d2c91e60"
down_revision: str | None = "e7a9c14d2b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow the full Telegram user ID range."""
    with op.batch_alter_table("telegram_accounts") as batch_op:
        batch_op.alter_column(
            "telegram_user_id",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )


def downgrade() -> None:
    """Restore 32-bit Telegram user ID storage."""
    with op.batch_alter_table("telegram_accounts") as batch_op:
        batch_op.alter_column(
            "telegram_user_id",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
