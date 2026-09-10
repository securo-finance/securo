FROM ghcr.io/astral-sh/uv:0.12.10 AS uv
FROM python:3.14-slim

WORKDIR /app

# Compose mounts source at /app; keep the Linux environment outside that mount.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"

# Cache dependencies separately from source and leave uv out of the runtime image.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY . .
# Editable installation also supports the backend, Celery and MCP source binds.
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
