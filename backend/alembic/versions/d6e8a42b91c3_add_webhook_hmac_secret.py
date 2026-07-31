"""Add encrypted GitHub webhook HMAC secret."""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "d6e8a42b91c3"
down_revision: Union[str, None] = "b31e2f3c4a59"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    """Add encrypted webhook HMAC secret storage."""
    op.add_column("webhooks", sa.Column("encrypted_secret", sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove encrypted webhook HMAC secret storage."""
    op.drop_column("webhooks", "encrypted_secret")
