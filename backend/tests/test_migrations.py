"""Database migration regression tests."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
ALEMBIC_HEAD = "c1b2d3e4f5a6"
ALEMBIC_PREVIOUS_HEAD = "9d7e3c1b2a44"


def test_clean_sqlite_database_upgrades_to_head(tmp_path: Path) -> None:
    """Apply the complete migration chain to a fresh SQLite database."""
    database_path = tmp_path / "migration.db"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path}"

    subprocess.run(  # noqa: S603 - fixed Python module and Alembic arguments
        [sys.executable, "-m", "alembic", "upgrade", ALEMBIC_PREVIOUS_HEAD],
        cwd=BACKEND_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO projects (
                id, name, slug, project_type, runtime_type, status, branch,
                runtime_config, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "11111111-1111-1111-1111-111111111111",
                "Existing bot",
                "existing-bot",
                "bot",
                "docker",
                "running",
                "main",
                "{}",
                "2026-01-01 00:00:00",
                "2026-01-01 00:00:00",
            ),
        )

    subprocess.run(  # noqa: S603 - fixed Python module and Alembic arguments
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(database_path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        user_columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        telegram_columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(telegram_accounts)").fetchall()
        }
        bot_runtime_config = connection.execute(
            "SELECT runtime_config FROM projects WHERE slug = 'existing-bot'"
        ).fetchone()

    assert revision == (ALEMBIC_HEAD,)
    assert user_columns["role"][4] is None
    assert telegram_columns["telegram_user_id"][2].upper() == "BIGINT"
    assert bot_runtime_config is not None
    assert json.loads(bot_runtime_config[0])["persistent_mounts"] == [
        {"source": "data", "target": "/app/data", "read_only": False}
    ]
