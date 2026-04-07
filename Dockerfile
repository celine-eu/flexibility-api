# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

# Install system dependencies (curl kept for healthcheck probes)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv from the official image — pin the tag for reproducible builds
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency manifests (lock file required for --frozen)
COPY pyproject.toml uv.lock README.md ./

# Install production dependencies; --frozen ensures the lock file is respected
RUN uv sync --frozen --no-editable --no-dev --no-cache

# Copy application source
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
COPY policies ./policies

# Non-root user
RUN useradd -u 10001 -m appuser
USER appuser

EXPOSE 8017

# uvicorn is on PATH via UV_SYSTEM_PYTHON=1 (/usr/local/bin/uvicorn)
CMD ["uvicorn", "celine.flexibility.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8017"]
