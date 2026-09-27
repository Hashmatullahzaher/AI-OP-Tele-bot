FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN useradd --create-home --uid 10001 osai \
    && mkdir -p /var/lib/osai /etc/osai \
    && chown -R osai:osai /var/lib/osai

WORKDIR /app
COPY requirements-bot.txt /app/requirements-bot.txt
RUN pip install --no-cache-dir -r /app/requirements-bot.txt
COPY osai /app/osai

USER osai
VOLUME ["/var/lib/osai"]
ENTRYPOINT ["python", "-m", "osai.bot"]
CMD ["run"]
