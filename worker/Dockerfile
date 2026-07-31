FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend:/app/worker
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git ca-certificates zip unzip postgresql-client default-mysql-client \
    && rm -rf /var/lib/apt/lists/*
RUN addgroup --system --gid 10001 panel-worker \
    && adduser --system --uid 10001 --ingroup panel-worker panel-worker \
    && install -d -o panel-worker -g panel-worker /srv/vps-panel
COPY backend/requirements.txt /tmp/backend-requirements.txt
COPY worker/requirements.txt /tmp/worker-requirements.txt
RUN sed -i 's#-r ../backend/requirements.txt#-r /tmp/backend-requirements.txt#' /tmp/worker-requirements.txt \
    && pip install --no-cache-dir -r /tmp/worker-requirements.txt
COPY backend /app/backend
COPY worker /app/worker
RUN chown -R panel-worker:panel-worker /app
USER panel-worker
CMD ["celery", "-A", "nectarine_worker.celery_app", "worker", "--loglevel=INFO", "--concurrency=2"]
