# Development

**English** · [Українська](../uk/development.md) · [Русский](../ru/development.md) · [Polski](../pl/development.md)

Use Python only through `.venv`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
cd frontend && npm ci
```

The default development database URL points to SQLite when running the backend
directly. Docker Compose injects PostgreSQL and Valkey URLs.

Run checks before committing:

```bash
make lint
make test
cd frontend && npm run typecheck && npm run build
docker compose config --quiet
```

Create schema changes by importing every model in `backend/app/models` and
running:

```bash
cd backend
../.venv/bin/alembic revision --autogenerate -m "describe change"
../.venv/bin/alembic upgrade head
```

Review generated migrations. Never edit an applied migration.
