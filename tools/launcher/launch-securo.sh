#!/bin/zsh

set -u

# Where the Securo checkout lives. build-app.sh bakes an absolute path in here
# when it assembles the .app; running this script straight from the repo falls
# back to deriving the path from the script's own location. An explicit
# PROJECT_DIR in the environment always wins.
BAKED_PROJECT_DIR="__SECURO_PROJECT_DIR__"
if [[ "$BAKED_PROJECT_DIR" == "__SECURO_PROJECT_DIR__" ]]; then
  BAKED_PROJECT_DIR="${0:A:h:h:h}"
fi
PROJECT_DIR="${PROJECT_DIR:-$BAKED_PROJECT_DIR}"
STATE_DIR="${STATE_DIR:-${TMPDIR:-/tmp}/securo-launcher}"
LOG_FILE="${LOG_FILE:-$STATE_DIR/launcher.log}"
DOCKER_BIN="${DOCKER_BIN:-/opt/homebrew/bin/docker}"
COLIMA_BIN="${COLIMA_BIN:-/opt/homebrew/bin/colima}"
CURL_BIN="${CURL_BIN:-/usr/bin/curl}"
OPEN_BIN="${OPEN_BIN:-/usr/bin/open}"
OSASCRIPT_BIN="${OSASCRIPT_BIN:-/usr/bin/osascript}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-180}"
APP_URL="${APP_URL:-http://localhost:3000}"
# The frontend is nginx serving static files: it answers 200 even when the API
# is dead, so a frontend-only probe reports a broken stack as ready. The login
# screen's "Couldn't connect to the server" is exactly that case, so readiness
# must include the API.
HEALTH_URL="${HEALTH_URL:-http://localhost:8000/api/health}"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.prod.yml"

notify() {
  "$OSASCRIPT_BIN" -e "display notification \"$1\" with title \"Securo\"" >/dev/null 2>&1 || true
}

# Writes straight to the log file rather than relying on inherited stdout:
# under the .app stub stdout is a redirected file, which zsh block-buffers, so
# a process that is terminated before it exits loses everything it logged.
# Echoed to the terminal as well, but only when there is one.
log() {
  local line="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
  [[ -t 1 ]] && print -r -- "$line"
  print -r -- "$line" >> "$LOG_FILE"
}

# 180s reads as "3 minuti", 30s as "30 sekundi" — never "0 minuti".
human_timeout() {
  if (( TIMEOUT_SECONDS >= 60 )); then
    print -r -- "$((TIMEOUT_SECONDS / 60)) minuti"
  else
    print -r -- "$TIMEOUT_SECONDS sekundi"
  fi
}

fail() {
  log "FAIL: $1"
  notify "$1"
  exit 1
}

compose() {
  "$DOCKER_BIN" compose -f "$COMPOSE_FILE" "$@"
}

# A container that keeps crashing (bad migration, missing env, port clash) is
# reported by `up -d` as started, so the exit code alone can't be trusted.
crashing_services() {
  compose ps --format '{{.Name}} {{.State}}' 2>/dev/null \
    | grep -i 'restarting' \
    | awk '{print $1}' \
    | tr '\n' ' '
}

url_ok() {
  "$CURL_BIN" -fsS -m 5 "$1" >/dev/null 2>&1
}

# Before any log() or fail(), which both write into it.
mkdir -p "$STATE_DIR" || {
  "$OSASCRIPT_BIN" -e 'display notification "Securo ajutist kausta ei saanud luua." with title "Securo"' >/dev/null 2>&1
  exit 1
}

[[ -x "$DOCKER_BIN" ]] || fail "Dockerit ei leitud. Paigalda või ava Docker Desktop."
[[ -f "$COMPOSE_FILE" ]] || fail "Securo tootmiskonfiguratsiooni ei leitud kaustast: $PROJECT_DIR"

LOCK_DIR="$STATE_DIR/launch.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  notify "Securo käivitamine juba käib."
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

log "Securo launcher: projekt=$PROJECT_DIR"

if ! "$DOCKER_BIN" info >/dev/null 2>&1; then
  if [[ -x "$COLIMA_BIN" ]]; then
    notify "Käivitan Colimat…"
    log "Docker daemon ei vasta, käivitan Colima."
    "$COLIMA_BIN" start > "$STATE_DIR/colima.log" 2>&1 || fail "Colima käivitamine ebaõnnestus."
  else
    "$OPEN_BIN" -gja Docker >/dev/null 2>&1 || true
    notify "Ootan Dockeri käivitumist…"
  fi
fi

elapsed=0
while ! "$DOCKER_BIN" info >/dev/null 2>&1; do
  (( elapsed >= TIMEOUT_SECONDS )) && fail "Docker ei käivitunud $(human_timeout) jooksul."
  sleep 2
  (( elapsed += 2 ))
done

cd "$PROJECT_DIR" || fail "Securo projekti kausta ei saanud avada."

# Refresh the published images first. A local image older than the database
# schema makes the backend abort on startup ("Can't locate revision ...") and
# crash-loop, which is invisible from the frontend. Non-fatal: offline starts
# must still work with whatever image is already on disk.
notify "Kontrollin uuendusi…"
log "Tõmban uuemad imaged."
if ! compose pull; then
  log "WARN: image'ite tõmbamine ebaõnnestus, jätkan olemasolevatega."
  notify "Uuendusi ei saanud tõmmata, käivitan olemasolevad."
fi

log "Käivitan Compose'i."
compose up -d || fail "Securo Docker Compose käivitamine ebaõnnestus."

notify "Securo käivitub…"
elapsed=0
while true; do
  crashed="$(crashing_services)"
  if [[ -n "${crashed// /}" ]]; then
    log "Crash-loop: $crashed"
    fail "Securo teenus kukub pidevalt: ${crashed}— vaata 'docker compose -f docker-compose.prod.yml logs backend'."
  fi

  if url_ok "$APP_URL" && url_ok "$HEALTH_URL"; then
    break
  fi

  if (( elapsed >= TIMEOUT_SECONDS )); then
    if url_ok "$APP_URL"; then
      fail "Securo backend ei vastanud $(human_timeout) jooksul (frontend töötab, API ei). Vaata 'docker compose -f docker-compose.prod.yml logs backend'."
    fi
    fail "Securo ei muutunud $(human_timeout) jooksul kättesaadavaks."
  fi

  sleep 2
  (( elapsed += 2 ))
done

log "Securo on valmis: $APP_URL"
"$OPEN_BIN" "$APP_URL"
notify "Securo on valmis."
