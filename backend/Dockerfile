FROM python:3.14-slim AS builder

# uv comes from its official image (https://docs.astral.sh/uv/guides/integration/docker/)
# and stays in this stage: it is a build tool, not a runtime dependency.
# Renovate bumps the tag from PyPI, together with the other uv pins.
# renovate: datasource=pypi depName=uv
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /bin/uv

# The environment lives outside /app so the development bind mount of
# ./backend (docker-compose.yml) can't hide it, nor a host .venv leak in.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Dependencies come from uv.lock (the source of truth — see scripts/lock.sh).
# --locked fails the build if the lock is out of date with pyproject.toml, and
# uv checks every download against the hashes recorded in the lock, so the
# image only ever contains the exact audited versions. Extras (dev) are left
# out. This layer is cached until pyproject.toml or uv.lock change; the cache
# mount keeps downloads across builds even then.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

# The runtime image: the environment and the code, no compiler, no uv.
FROM python:3.14-slim

COPY --from=builder /opt/venv /opt/venv
# The project isn't installed as a package (there is no build backend);
# PYTHONPATH makes `app`, `mcp_server` and `plugin_runner` importable from
# any working directory.
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app

WORKDIR /app
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
