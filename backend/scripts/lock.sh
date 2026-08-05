#!/usr/bin/env bash
# Regenerate the backend dependency lock and its pip-consumable exports.
#
# uv.lock is the source of truth; requirements.txt (runtime, used by the
# Docker image) and requirements-dev.txt (runtime + dev extra, used by CI and
# local dev) are exported from it so plain pip keeps working everywhere.
#
# Run this after any change to [project.dependencies] or the dev extra in
# pyproject.toml, and commit all three generated files. CI re-runs this script
# and fails if the committed files don't match.
#
# The uv version is pinned so the export output is byte-for-byte reproducible;
# bump it here deliberately.
set -euo pipefail
cd "$(dirname "$0")/.."

UV=uv@0.12.1

uvx "$UV" lock
uvx "$UV" export --no-emit-project -o requirements.txt
uvx "$UV" export --all-extras --no-emit-project -o requirements-dev.txt
