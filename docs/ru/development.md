# Разработка

[English](../en/development.md) · [Українська](../uk/development.md) · **Русский** · [Polski](../pl/development.md)

Используйте виртуальное окружение Python и отдельную установку зависимостей frontend.

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm ci
```

Перед изменением схемы создавайте миграцию Alembic. Перед отправкой изменений запускайте целевые тесты, `ruff check`, `mypy`, `npm run typecheck` и `npm run test`.

Не помещайте `.env`, токены, базы данных, сборки и резервные копии в репозиторий.

