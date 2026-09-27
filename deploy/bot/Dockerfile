FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN useradd --create-home --uid 10001 osai \
    && mkdir -p /var/lib/osai /etc/osai \
    && chown -R osai:osai /var/lib/osai

WORKDIR /app
COPY requirements-bot.txt requirements-voice.txt /app/
RUN pip install --no-cache-dir -r /app/requirements-bot.txt -r /app/requirements-voice.txt
COPY osai /app/osai

USER osai
VOLUME ["/var/lib/osai"]
ENTRYPOINT ["python", "-m", "osai.bot"]
CMD ["run"]
