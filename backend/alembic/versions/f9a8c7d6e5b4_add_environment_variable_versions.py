"""Add environment variable version history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9a8c7d6e5b4"
down_revision: str | None = "a4f7d2c91e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create immutable project environment history."""
    op.create_table(
        "environment_variable_versions",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=True),
        sa.Column("is_secret", sa.Boolean(), nullable=False),
        sa.Column("value_sha256", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_environment_variable_versions_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_environment_variable_versions_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_environment_variable_versions")),
    )
    op.create_index(
        op.f("ix_environment_variable_versions_actor_id"),
        "environment_variable_versions",
        ["actor_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_environment_variable_versions_created_at"),
        "environment_variable_versions",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_environment_versions_project_key",
        "environment_variable_versions",
        ["project_id", "key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_environment_variable_versions_project_id"),
        "environment_variable_versions",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop immutable project environment history."""
    op.drop_index(
        op.f("ix_environment_variable_versions_project_id"),
        table_name="environment_variable_versions",
    )
    op.drop_index(
        "ix_environment_versions_project_key",
        table_name="environment_variable_versions",
    )
    op.drop_index(
        op.f("ix_environment_variable_versions_created_at"),
        table_name="environment_variable_versions",
    )
    op.drop_index(
        op.f("ix_environment_variable_versions_actor_id"),
        table_name="environment_variable_versions",
    )
    op.drop_table("environment_variable_versions")
