#!/usr/bin/env bash
# Regenerate uv.lock, the source of truth for backend dependencies.
#
# Nothing else is committed: CI and the Docker image export the lock at
# install time (uv export --frozen) and feed it to pip with hash checking,
# and `uv sync --all-extras` builds a dev venv from it directly.
#
# Run this after any change to [project.dependencies] or the dev extra in
# pyproject.toml and commit the updated uv.lock — CI fails if it drifts.
# Extra arguments are passed through, e.g.:  ./scripts/lock.sh --upgrade
#
# The uv version is pinned so resolution is reproducible; Renovate bumps it.
set -euo pipefail
cd "$(dirname "$0")/.."

# renovate: datasource=pypi depName=uv
uvx uv@0.12.5 lock "$@"
