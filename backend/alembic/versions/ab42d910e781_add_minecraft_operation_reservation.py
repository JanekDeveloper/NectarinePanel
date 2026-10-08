"""Reserve exclusive Minecraft operations across API and worker processes."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ab42d910e781"
down_revision: str | None = "c1b2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a nullable active job reference without changing existing runtimes."""
    with op.batch_alter_table("project_runtimes") as batch:
        batch.add_column(sa.Column("active_job_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_runtime_active_job", "jobs", ["active_job_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_project_runtimes_active_job_id", ["active_job_id"])


def downgrade() -> None:
    """Remove operation reservations."""
    with op.batch_alter_table("project_runtimes") as batch:
        batch.drop_index("ix_project_runtimes_active_job_id")
        batch.drop_constraint("fk_runtime_active_job", type_="foreignkey")
        batch.drop_column("active_job_id")
