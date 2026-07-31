"""Add attached database selection to backup policies."""

import sqlalchemy as sa
from alembic import op

revision: str = "e7a9c14d2b60"
down_revision: str | None = "d6e8a42b91c3"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Add enabled-by-default attached database backups."""
    op.add_column(
        "backup_policies",
        sa.Column(
            "include_databases",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    with op.batch_alter_table("backup_policies") as batch_op:
        batch_op.alter_column(
            "include_databases",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    """Remove attached database backup selection."""
    with op.batch_alter_table("backup_policies") as batch_op:
        batch_op.drop_column("include_databases")
