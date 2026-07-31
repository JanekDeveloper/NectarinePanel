"""Add project resource policies."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9d7e3c1b2a44"
down_revision: str | None = "6c2d1f0a9b8e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create per-project resource policy storage."""
    op.create_table(
        "project_resource_policies",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("cpu_cores", sa.Float(), nullable=True),
        sa.Column("memory_mb", sa.Integer(), nullable=True),
        sa.Column("disk_mb", sa.Integer(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_project_resource_policies_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_resource_policies")),
    )
    op.create_index(
        op.f("ix_project_resource_policies_project_id"),
        "project_resource_policies",
        ["project_id"],
        unique=True,
    )


def downgrade() -> None:
    """Drop per-project resource policy storage."""
    op.drop_index(
        op.f("ix_project_resource_policies_project_id"),
        table_name="project_resource_policies",
    )
    op.drop_table("project_resource_policies")
