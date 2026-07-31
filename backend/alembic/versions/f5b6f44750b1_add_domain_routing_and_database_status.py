"""add domain routing and database status."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f5b6f44750b1"
down_revision: Union[str, None] = "4581f4d85cea"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply this migration."""
    op.add_column(
        "database_instances",
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="queued",
        ),
    )
    op.create_index(
        "uq_database_instance_server_name",
        "database_instances",
        ["server_id", "name"],
        unique=True,
    )
    op.add_column(
        "domains",
        sa.Column(
            "upstream_port",
            sa.Integer(),
            nullable=False,
            server_default="8080",
        ),
    )


def downgrade() -> None:
    """Revert this migration."""
    op.drop_column("domains", "upstream_port")
    op.drop_index(
        "uq_database_instance_server_name",
        table_name="database_instances",
    )
    op.drop_column("database_instances", "status")
