#!/usr/bin/env bash
# Regenerate uv.lock, the source of truth for backend dependencies.
#
# CI, Docker and local development install it with `uv sync --locked`.
# Add `--group dev` for developer tools or `--no-dev` for runtime only.
#
# Run this after any change to [project.dependencies] or [dependency-groups] in
# pyproject.toml and commit the updated uv.lock — CI fails if it drifts.
# Extra arguments are passed through, e.g.:  ./scripts/lock.sh --upgrade
#
# The uv version is pinned so resolution is reproducible; Renovate bumps it.
set -euo pipefail
cd "$(dirname "$0")/.."

# renovate: datasource=pypi depName=uv
uvx uv@0.12.10 lock "$@"
