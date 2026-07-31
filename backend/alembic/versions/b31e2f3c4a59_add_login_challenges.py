"""add login challenges."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b31e2f3c4a59"
down_revision: str | None = "92f7abe8d970"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this migration."""
    op.create_table(
        "login_challenges",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("request_ip", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_login_challenges_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_challenges")),
    )
    op.create_index(
        op.f("ix_login_challenges_token_digest"),
        "login_challenges",
        ["token_digest"],
        unique=True,
    )
    op.create_index(
        op.f("ix_login_challenges_user_id"),
        "login_challenges",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Revert this migration."""
    op.drop_index(op.f("ix_login_challenges_user_id"), table_name="login_challenges")
    op.drop_index(op.f("ix_login_challenges_token_digest"), table_name="login_challenges")
    op.drop_table("login_challenges")
