# Rozwój

[English](../en/development.md) · [Українська](../uk/development.md) · [Русский](../ru/development.md) · **Polski**

Używaj izolowanego środowiska wirtualnego Pythona i osobnej instalacji zależności frontend.

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm ci
```

Dla zmian schematu twórz migrację Alembic. Przed wysłaniem zmian uruchom testy docelowe, `ruff check`, `mypy`, `npm run typecheck` i `npm run test`. Nie dodawaj `.env`, tokenów, baz, buildów ani kopii zapasowych do repozytorium.

