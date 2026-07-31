FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend
WORKDIR /app

RUN addgroup --system --gid 10001 panel \
    && adduser --system --uid 10001 --ingroup panel panel \
    && install -d -o panel -g panel /srv/vps-panel
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY backend /app/backend
RUN chown -R panel:panel /app
USER panel
EXPOSE 8000
CMD ["sh", "-c", "cd /app/backend && alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
