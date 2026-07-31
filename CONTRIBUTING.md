# Contributing

## Development setup

Requirements: Python 3.12+, Node.js 22.22.2 LTS, npm 10+, and Docker Compose v2.

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
cd frontend && npm ci
```

Use a dedicated `.env` copied from `.env.example`. Never commit credentials or
production data.

## Before opening a pull request

```bash
make lint
make test
make build-frontend
make audit-frontend
make compose-check
```

Keep changes focused. Add unit tests for new logic, preserve public API
compatibility unless the change is explicitly breaking, and document database
migrations and operational impact. Python functions, classes, and modules must
have concise English docstrings.

Security vulnerabilities must follow [SECURITY.md](SECURITY.md), not the public
issue tracker.
