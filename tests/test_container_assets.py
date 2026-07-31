"""Container build asset regression tests."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_service_images_run_as_unprivileged_users() -> None:
    """Ensure application images do not execute services as root."""
    for relative_path in (
        "backend/Dockerfile",
        "worker/Dockerfile",
        "agent/Dockerfile",
        "telegram-bot/Dockerfile",
        "frontend/Dockerfile",
    ):
        dockerfile = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "USER " in dockerfile, relative_path


def test_docker_context_excludes_secrets_and_local_artifacts() -> None:
    """Keep credentials, databases, and local build output out of contexts."""
    root_ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    frontend_ignore = (ROOT / "frontend/.dockerignore").read_text(encoding="utf-8").splitlines()

    assert ".env" in root_ignore
    assert "**/*.db" in root_ignore
    assert ".venv" in root_ignore
    assert "frontend/node_modules" in root_ignore
    assert ".env" in frontend_ignore
    assert "node_modules" in frontend_ignore
    assert ".output" in frontend_ignore


def test_development_ports_bind_to_loopback() -> None:
    """Prevent development credentials from being exposed on public interfaces."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert '"127.0.0.1:${BACKEND_PORT:-8000}:8000"' in compose
    assert '"127.0.0.1:${FRONTEND_PORT:-3000}:3000"' in compose
