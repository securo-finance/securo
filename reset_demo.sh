#!/usr/bin/env bash
# Wipes and reseeds the demo database for the public demo host
# (demo.usesecuro.com). Driven by the securo-demo-reset.timer systemd timer
# (hourly). Safe to run while the app is up: seed_demo.py drops + recreates
# the seed user's data inside a transaction.
#
# This directory holds three compose files. Without pinning -p and -f, docker
# compose picks docker-compose.yml and derives the project name from the
# directory ("code"), so every service comes back as "not running". Always
# pin both.
set -euo pipefail

cd "$(dirname "$0")"

COMPOSE_PROJECT="${COMPOSE_PROJECT:-securodemo-app-hgwvvo}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.demo.yml}"
compose=(docker compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE")

DEMO_EMAIL="${DEMO_EMAIL:-demo@securo.app}"
DEMO_PASSWORD="${DEMO_PASSWORD:-DemoSecuro1!}"
DEMO_MONTHS="${DEMO_MONTHS:-6}"

echo "[$(date -Is)] demo reset start"
if ! out=$("${compose[@]}" exec -T backend python scripts/seed_demo.py \
      --email "$DEMO_EMAIL" \
      --password "$DEMO_PASSWORD" \
      --months "$DEMO_MONTHS" 2>&1); then
  printf '%s\n' "$out" | tail -20
  echo "[$(date -Is)] demo reset FAILED"
  exit 1
fi
printf '%s\n' "$out" | grep -v 'UserWarning\|warnings.warn' || true

# Clear user-uploaded attachments so disk doesn't grow between resets.
"${compose[@]}" exec -T backend sh -c 'rm -rf /app/data/attachments/* || true'

# Drop the chat history visitors left behind, so the next visitor never
# sees another one's conversations (agent_messages cascade on the FK).
# Kept here, not in seed_demo.py: the container runs the copy baked into
# the image, and the host file is not the one that executes.
"${compose[@]}" exec -T db psql -U postgres -d securo -v ON_ERROR_STOP=1 \
  -c 'DELETE FROM agent_conversations' || echo "$(date -Is) conversation cleanup skipped"
echo "[$(date -Is)] demo reset done"
