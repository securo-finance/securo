#!/usr/bin/env bash
# All-in-one entrypoint: run the migrations, then the API and a Celery worker
# with embedded beat, in this one container.
#
# Two processes under bash rather than a supervisor. For a single-replica
# deployment "either process dies, the container dies, the restart policy
# brings it back" is the behaviour you want, and it costs five lines here
# instead of a dependency that is not in uv.lock — which would break the
# --require-hashes guarantee the backend image is built around.
set -euo pipefail

# Railway's private network is IPv6-only; it sets UVICORN_HOST to '::'.
APP_HOST="${UVICORN_HOST:-0.0.0.0}"
APP_PORT="${PORT:-${UVICORN_PORT:-8000}}"
CELERY_CONCURRENCY="${CELERY_CONCURRENCY:-2}"
CELERY_LOGLEVEL="${CELERY_LOGLEVEL:-info}"

# Beat records when each schedule last ran in a shelve file. The default is
# the working directory, which is an image layer — it would be lost on every
# restart and re-fire all seven entries at once. Keep it on the volume.
BEAT_SCHEDULE="${CELERY_BEAT_SCHEDULE_FILE:-/app/data/celerybeat-schedule}"

mkdir -p \
  "$(dirname "$BEAT_SCHEDULE")" \
  "${STORAGE_LOCAL_PATH:-/app/data/attachments}" \
  "${AGENTS_KNOWLEDGE_STORAGE_PATH:-/app/data/agent_knowledge}"

# With no nginx in front, uvicorn is what a reverse proxy talks to. Without
# this every request appears to come from the proxy and X-Forwarded-Proto is
# ignored, which breaks scheme-aware redirects and per-IP rate limiting.
proxy_args=()
if [ -n "${UVICORN_FORWARDED_ALLOW_IPS:-}" ]; then
  proxy_args=(--proxy-headers --forwarded-allow-ips "${UVICORN_FORWARDED_ALLOW_IPS}")
fi

echo "[aio] alembic upgrade head"
alembic upgrade head

pids=()

shutdown() {
  # Detach the trap first so a second signal cannot re-enter this.
  trap - TERM INT
  echo "[aio] forwarding SIGTERM to children"
  for pid in ${pids[@]+"${pids[@]}"}; do
    kill -TERM "$pid" 2>/dev/null || true
  done
}
trap shutdown TERM INT

echo "[aio] uvicorn on ${APP_HOST}:${APP_PORT}"
uvicorn app.main:app --host "$APP_HOST" --port "$APP_PORT" ${proxy_args[@]+"${proxy_args[@]}"} &
pids+=("$!")

# -B embeds beat in the worker. Never run this container with more than one
# replica: two beats means every periodic task fires twice.
echo "[aio] celery worker with embedded beat (schedule: ${BEAT_SCHEDULE})"
celery -A app.worker worker -B \
  --loglevel="$CELERY_LOGLEVEL" \
  --concurrency="$CELERY_CONCURRENCY" \
  --schedule="$BEAT_SCHEDULE" &
pids+=("$!")

# `wait -n` returns as soon as *either* child exits. set -e is fenced off so a
# non-zero status reaches the cleanup below instead of aborting the script.
set +e
wait -n
status=$?
set -e

echo "[aio] a child exited (status ${status}); stopping the container"
shutdown
# Reap both so the container does not exit while Celery is still draining.
wait || true
exit "$status"
