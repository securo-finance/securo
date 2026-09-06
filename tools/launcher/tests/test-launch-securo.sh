#!/bin/zsh

set -eu

SCRIPT_DIR="${0:A:h}"
LAUNCHER="${SCRIPT_DIR:h}/launch-securo.sh"
TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT

FAILURES=0
check() {
  if eval "$2"; then
    print "  ok   $1"
  else
    print "  FAIL $1"
    (( FAILURES += 1 ))
  fi
}

mkdir -p "$TEST_DIR/project" "$TEST_DIR/bin"
touch "$TEST_DIR/project/docker-compose.prod.yml"
COMPOSE="$TEST_DIR/project/docker-compose.prod.yml"

cat > "$TEST_DIR/bin/docker" <<'EOF'
#!/bin/zsh
print -r -- "$@" >> "$TEST_LOG"
if [[ "$1" == "info" ]]; then
  if [[ "${TEST_REQUIRE_COLIMA_START:-0}" == "1" && ! -f "$TEST_DIR/colima-started" ]]; then
    exit 1
  fi
  exit 0
fi
if [[ "$1" == "compose" ]]; then
  for a in "$@"; do
    case "$a" in
      ps)   print -r -- "${TEST_PS_OUTPUT:-}"; exit 0 ;;
      pull) exit "${TEST_PULL_EXIT:-0}" ;;
      up)   exit "${TEST_UP_EXIT:-0}" ;;
    esac
  done
fi
exit 0
EOF

cat > "$TEST_DIR/bin/colima" <<'EOF'
#!/bin/zsh
print -r -- "$@" >> "$TEST_COLIMA_LOG"
touch "$TEST_DIR/colima-started"
EOF

cat > "$TEST_DIR/bin/curl" <<'EOF'
#!/bin/zsh
print -r -- "$@" >> "$TEST_CURL_LOG"
for a in "$@"; do
  case "$a" in
    *"${TEST_CURL_FAIL_URL:-__never_matches__}"*) exit 7 ;;
  esac
done
exit 0
EOF

for f in open osascript; do
  cat > "$TEST_DIR/bin/$f" <<EOF
#!/bin/zsh
print -r -- "\$@" >> "\$TEST_${f:u}_LOG"
exit 0
EOF
done

chmod +x "$TEST_DIR/bin/"*

# Runs the launcher in an isolated state dir. Extra env comes from the caller.
run_launcher() {
  local name="$1"; shift
  rm -f "$TEST_DIR"/{docker,curl,open,osascript,colima}.log
  touch "$TEST_DIR"/{docker,curl,open,osascript,colima}.log
  set +e
  env \
    TEST_DIR="$TEST_DIR" \
    TEST_LOG="$TEST_DIR/docker.log" \
    TEST_CURL_LOG="$TEST_DIR/curl.log" \
    TEST_OPEN_LOG="$TEST_DIR/open.log" \
    TEST_OSASCRIPT_LOG="$TEST_DIR/osascript.log" \
    TEST_COLIMA_LOG="$TEST_DIR/colima.log" \
    PROJECT_DIR="$TEST_DIR/project" \
    STATE_DIR="$TEST_DIR/state-$name" \
    DOCKER_BIN="$TEST_DIR/bin/docker" \
    COLIMA_BIN="$TEST_DIR/bin/colima" \
    CURL_BIN="$TEST_DIR/bin/curl" \
    OPEN_BIN="$TEST_DIR/bin/open" \
    OSASCRIPT_BIN="$TEST_DIR/bin/osascript" \
    TIMEOUT_SECONDS=1 \
    "$@" \
    "$LAUNCHER"
  LAUNCHER_EXIT=$?
  set -e
}

print "TEST 1: happy path pulls images, starts compose, probes BOTH tiers, opens browser"
run_launcher happy
check "exits 0"                      '(( LAUNCHER_EXIT == 0 ))'
check "pulls images before starting" 'grep -Fx -- "compose -f $COMPOSE pull" "$TEST_DIR/docker.log" >/dev/null'
check "starts compose"               'grep -Fx -- "compose -f $COMPOSE up -d" "$TEST_DIR/docker.log" >/dev/null'
check "probes frontend"              'grep -F -- "http://localhost:3000" "$TEST_DIR/curl.log" >/dev/null'
check "probes backend health"        'grep -F -- "http://localhost:8000/api/health" "$TEST_DIR/curl.log" >/dev/null'
check "opens browser"                'grep -Fx -- "http://localhost:3000" "$TEST_DIR/open.log" >/dev/null'
# Written directly rather than through inherited stdout, so it survives the
# .app stub backgrounding the launcher and the process being terminated.
check "writes its own log file"      '[[ -s "$TEST_DIR/state-happy/launcher.log" ]]'
check "log is timestamped"           'grep -qE "^\[[0-9]{4}-[0-9]{2}-[0-9]{2} " "$TEST_DIR/state-happy/launcher.log"'

print "TEST 2: backend never healthy -> fail loudly, do NOT open the browser"
run_launcher deadbackend TEST_CURL_FAIL_URL=/api/health
check "exits non-zero"               '(( LAUNCHER_EXIT != 0 ))'
check "browser NOT opened"           '! grep -q . "$TEST_DIR/open.log"'
check "notifies about the backend"   'grep -iF -- "backend" "$TEST_DIR/osascript.log" >/dev/null'

print "TEST 3: crash-looping container -> fail fast instead of reporting success"
run_launcher crashloop TEST_PS_OUTPUT="securo-backend-1 restarting"
check "exits non-zero"               '(( LAUNCHER_EXIT != 0 ))'
check "browser NOT opened"           '! grep -q . "$TEST_DIR/open.log"'
check "notifies about the crash"     'grep -iE "restart|kukub|crash" "$TEST_DIR/osascript.log" >/dev/null'

print "TEST 4: a failing pull is non-fatal (offline start still works)"
run_launcher offlinepull TEST_PULL_EXIT=1
check "exits 0"                      '(( LAUNCHER_EXIT == 0 ))'
check "still starts compose"         'grep -Fx -- "compose -f $COMPOSE up -d" "$TEST_DIR/docker.log" >/dev/null'
check "still opens browser"          'grep -Fx -- "http://localhost:3000" "$TEST_DIR/open.log" >/dev/null'

print "TEST 5: Colima is started when the Docker daemon is down"
run_launcher colima TEST_REQUIRE_COLIMA_START=1
check "exits 0"                      '(( LAUNCHER_EXIT == 0 ))'
check "starts colima"                'grep -Fx -- "start" "$TEST_DIR/colima.log" >/dev/null'
check "then starts compose"          'grep -Fx -- "compose -f $COMPOSE up -d" "$TEST_DIR/docker.log" >/dev/null'

print "TEST 6: compose failure is reported, browser stays shut"
run_launcher composefail TEST_UP_EXIT=1
check "exits non-zero"               '(( LAUNCHER_EXIT != 0 ))'
check "browser NOT opened"           '! grep -q . "$TEST_DIR/open.log"'

print ""
if (( FAILURES > 0 )); then
  print "FAILED: $FAILURES check(s)"
  exit 1
fi
print "PASS: all launcher checks green."
