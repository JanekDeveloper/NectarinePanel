FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/agent
WORKDIR /app
COPY agent/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && addgroup --system --gid 10001 panel-agent \
    && adduser --system --uid 10001 --ingroup panel-agent panel-agent \
    && install -d -o panel-agent -g panel-agent /srv/vps-panel
COPY agent /app/agent
RUN chown -R panel-agent:panel-agent /app
USER panel-agent
EXPOSE 8090
CMD ["uvicorn", "system_agent.main:app", "--host", "0.0.0.0", "--port", "8090"]
