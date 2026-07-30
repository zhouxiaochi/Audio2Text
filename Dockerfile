FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/tmp/audio2text

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY backend ./backend
RUN python -m pip install .

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /tmp/audio2text \
    && chown -R appuser:appuser /app /tmp/audio2text

USER appuser

EXPOSE 8080

CMD ["sh", "-c", "uvicorn backend.api:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
