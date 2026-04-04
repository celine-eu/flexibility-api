# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

# Install system dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Copy dependency manifests
COPY pyproject.toml README.md ./

# Install dependencies (no dev group)
RUN uv sync --no-editable --no-dev

# Copy application source
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
COPY policies ./policies

# Non-root user
RUN useradd -u 10001 -m appuser
USER appuser

EXPOSE 8017

CMD ["uv", "run", "uvicorn", "celine.flexibility.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8017"]
