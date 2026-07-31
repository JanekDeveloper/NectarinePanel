"""Add users RBAC metadata and project memberships."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6c2d1f0a9b8e"
down_revision: str | None = "f9a8c7d6e5b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add global roles and project-scoped memberships."""
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=32), server_default="owner", nullable=False),
    )
    op.add_column("users", sa.Column("display_name", sa.String(length=120), nullable=True))
    op.add_column("users", sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_users_role"), "users", ["role"], unique=False)
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "role",
            existing_type=sa.String(length=32),
            existing_nullable=False,
            server_default=None,
        )

    op.create_table(
        "project_memberships",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_project_memberships_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_project_memberships_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_memberships")),
    )
    op.create_index(
        op.f("ix_project_memberships_project_id"),
        "project_memberships",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_memberships_user_id"),
        "project_memberships",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "uq_project_membership_project_user",
        "project_memberships",
        ["project_id", "user_id"],
        unique=True,
    )


def downgrade() -> None:
    """Remove global roles and project-scoped memberships."""
    op.drop_index("uq_project_membership_project_user", table_name="project_memberships")
    op.drop_index(op.f("ix_project_memberships_user_id"), table_name="project_memberships")
    op.drop_index(op.f("ix_project_memberships_project_id"), table_name="project_memberships")
    op.drop_table("project_memberships")
    op.drop_index(op.f("ix_users_role"), table_name="users")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("disabled_at")
        batch_op.drop_column("display_name")
        batch_op.drop_column("role")
