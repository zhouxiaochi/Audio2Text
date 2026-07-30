FROM node:22-bookworm-slim AS frontend-dependencies

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install

FROM node:22-bookworm-slim AS frontend-builder

WORKDIR /frontend
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=frontend-dependencies /frontend/node_modules ./node_modules
COPY frontend ./
RUN npm run build

FROM python:3.12-slim-bookworm AS backend-builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY backend ./backend
RUN python -m pip install --no-cache-dir .

FROM node:22-bookworm-slim AS node-runtime

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/tmp/audio2text \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg fonts-noto-cjk libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=backend-builder /usr/local /usr/local
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY backend ./backend

COPY --from=frontend-builder /frontend/.next/standalone ./frontend
COPY --from=frontend-builder /frontend/.next/static ./frontend/.next/static
COPY deploy/start.sh ./start.sh

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /tmp/audio2text \
    && chown -R appuser:appuser /app /tmp/audio2text \
    && chmod +x /app/start.sh

USER appuser

EXPOSE 8080

CMD ["/app/start.sh"]
