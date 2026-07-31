FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/telegram-bot
WORKDIR /app
COPY telegram-bot/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && addgroup --system --gid 10001 panel-bot \
    && adduser --system --uid 10001 --ingroup panel-bot panel-bot
COPY telegram-bot /app/telegram-bot
USER panel-bot
CMD ["python", "-m", "nectarine_bot.main"]
