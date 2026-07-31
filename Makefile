SHELL := /bin/bash
PYTHON := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: setup dev down test test-python test-frontend lint lint-python lint-frontend lint-shell format migrate worker scheduler bot agent backend frontend build-frontend audit-frontend compose-check check clean

setup:
	$(PIP) install -r backend/requirements-dev.txt
	cd frontend && npm ci

dev:
	docker compose up --build

down:
	docker compose down

test: test-python test-frontend

test-python:
	$(PYTHON) -m pytest

test-frontend:
	cd frontend && npm run test

lint: lint-python lint-frontend lint-shell

lint-python:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	PYTHONPATH=backend:agent:worker:telegram-bot $(PYTHON) -m mypy \
		backend/app agent/system_agent worker/nectarine_worker telegram-bot/nectarine_bot

lint-frontend:
	cd frontend && npm run lint && npm run typecheck

lint-shell:
	find installer scripts -type f -name '*.sh' -exec bash -n {} +
	find installer/helpers -type f -exec sh -n {} +

format:
	$(PYTHON) -m ruff format backend agent worker telegram-bot tests
	cd frontend && npm run format

migrate:
	cd backend && ../$(PYTHON) -m alembic upgrade head

backend:
	PYTHONPATH=backend $(PYTHON) -m uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

build-frontend:
	cd frontend && npm run build

audit-frontend:
	cd frontend && npm audit --audit-level=high

compose-check:
	docker compose config --quiet

check: lint test build-frontend audit-frontend compose-check

worker:
	PYTHONPATH=backend:worker $(PYTHON) -m celery -A nectarine_worker.celery_app worker --loglevel=INFO

scheduler:
	PYTHONPATH=backend:worker $(PYTHON) -m celery -A nectarine_worker.celery_app beat --loglevel=INFO

bot:
	PYTHONPATH=telegram-bot $(PYTHON) -m nectarine_bot.main

agent:
	PYTHONPATH=agent $(PYTHON) -m uvicorn system_agent.main:app --host 127.0.0.1 --port 8090

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov frontend/.nuxt frontend/.output
