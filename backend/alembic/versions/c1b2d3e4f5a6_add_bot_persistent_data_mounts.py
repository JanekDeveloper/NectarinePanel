"""Add persistent data mounts to existing Docker bot projects.

Revision ID: c1b2d3e4f5a6
Revises: 9d7e3c1b2a44
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1b2d3e4f5a6"
down_revision: str | None = "9d7e3c1b2a44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_BOT_MOUNTS = [{"source": "data", "target": "/app/data", "read_only": False}]


def upgrade() -> None:
    """Configure durable `/app/data` storage for existing Docker bots."""
    projects = sa.table(
        "projects",
        sa.column("id", sa.String()),
        sa.column("project_type", sa.String()),
        sa.column("runtime_type", sa.String()),
        sa.column("runtime_config", sa.JSON()),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(projects.c.id, projects.c.runtime_config).where(
            projects.c.project_type == "bot",
            projects.c.runtime_type == "docker",
        )
    ).mappings()
    for row in rows:
        configuration = row["runtime_config"]
        if not isinstance(configuration, dict) or "persistent_mounts" in configuration:
            continue
        updated = dict(configuration)
        updated["persistent_mounts"] = DEFAULT_BOT_MOUNTS
        connection.execute(
            projects.update().where(projects.c.id == row["id"]).values(runtime_config=updated)
        )


def downgrade() -> None:
    """Remove only the default mount mapping added by this migration."""
    projects = sa.table(
        "projects",
        sa.column("id", sa.String()),
        sa.column("project_type", sa.String()),
        sa.column("runtime_type", sa.String()),
        sa.column("runtime_config", sa.JSON()),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(projects.c.id, projects.c.runtime_config).where(
            projects.c.project_type == "bot",
            projects.c.runtime_type == "docker",
        )
    ).mappings()
    for row in rows:
        configuration = row["runtime_config"]
        if not isinstance(configuration, dict):
            continue
        if configuration.get("persistent_mounts") != DEFAULT_BOT_MOUNTS:
            continue
        updated = dict(configuration)
        updated.pop("persistent_mounts", None)
        connection.execute(
            projects.update().where(projects.c.id == row["id"]).values(runtime_config=updated)
        )
