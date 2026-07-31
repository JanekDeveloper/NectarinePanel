# Розробка

[English](../en/development.md) · **Українська** · [Русский](../ru/development.md) · [Polski](../pl/development.md)

Використовуйте ізольоване Python-віртуальне середовище та окреме встановлення залежностей frontend.

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm ci
```

Для змін схеми створюйте міграцію Alembic. Перед надсиланням запускайте цільові тести, `ruff check`, `mypy`, `npm run typecheck` і `npm run test`. Не додавайте `.env`, токени, бази, збірки або резервні копії до репозиторію.

