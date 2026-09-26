FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN useradd --create-home --uid 10001 osai \
    && mkdir -p /var/lib/osai \
    && chown -R osai:osai /var/lib/osai

WORKDIR /app
COPY osai /app/osai

USER osai
VOLUME ["/var/lib/osai"]
EXPOSE 8765

ENTRYPOINT ["python", "-m", "osai.runtime"]
CMD ["check"]
