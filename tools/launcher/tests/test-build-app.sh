#!/bin/zsh

# Regression cover for build-app.sh: the bundle it produces must actually
# resolve the checkout path that was baked into it. An earlier version replaced
# the placeholder everywhere, including inside the guard that detects an
# unbaked script, so the installed app silently fell back to deriving the
# project path from /Applications and could not find docker-compose.prod.yml.

set -eu

SCRIPT_DIR="${0:A:h}"
SRC_DIR="${SCRIPT_DIR:h}"
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

mkdir -p "$TEST_DIR/project" "$TEST_DIR/dest" "$TEST_DIR/bin"
touch "$TEST_DIR/project/docker-compose.prod.yml"

for f in docker curl open osascript; do
  cat > "$TEST_DIR/bin/$f" <<EOF
#!/bin/zsh
print -r -- "\$@" >> "$TEST_DIR/$f.log"
exit 0
EOF
  chmod +x "$TEST_DIR/bin/$f"
done

print "TEST: build-app.sh produces a runnable bundle pointed at the given checkout"
"$SRC_DIR/build-app.sh" --dest "$TEST_DIR/dest" --project-dir "$TEST_DIR/project" >/dev/null

APP="$TEST_DIR/dest/Securo.app"
LAUNCHER="$APP/Contents/Resources/launch-securo.sh"

check "bundle executable exists"  '[[ -x "$APP/Contents/MacOS/Securo" ]]'
check "launcher is executable"    '[[ -x "$LAUNCHER" ]]'
check "Info.plist copied"         '[[ -f "$APP/Contents/Info.plist" ]]'
check "icon copied"               '[[ -f "$APP/Contents/Resources/AppIcon.icns" ]]'
check "tests copied"              '[[ -x "$APP/Contents/Resources/tests/test-launch-securo.sh" ]]'
check "checkout path baked in"    'grep -Fx -- "BAKED_PROJECT_DIR=\"$TEST_DIR/project\"" "$LAUNCHER" >/dev/null'
check "unbaked guard left intact" 'grep -F -- "__SECURO_PROJECT_DIR__" "$LAUNCHER" >/dev/null'

# The real proof: run the built launcher with stubbed binaries and no
# PROJECT_DIR override, and confirm it drives Compose against the baked
# checkout rather than a path derived from where the bundle happens to sit.
set +e
env \
  STATE_DIR="$TEST_DIR/state" \
  DOCKER_BIN="$TEST_DIR/bin/docker" \
  COLIMA_BIN="$TEST_DIR/bin/colima" \
  CURL_BIN="$TEST_DIR/bin/curl" \
  OPEN_BIN="$TEST_DIR/bin/open" \
  OSASCRIPT_BIN="$TEST_DIR/bin/osascript" \
  TIMEOUT_SECONDS=1 \
  "$LAUNCHER" >/dev/null 2>&1
LAUNCHER_EXIT=$?
set -e

check "built launcher runs clean" '(( LAUNCHER_EXIT == 0 ))'
check "targets the baked compose file" \
  'grep -Fx -- "compose -f $TEST_DIR/project/docker-compose.prod.yml up -d" "$TEST_DIR/docker.log" >/dev/null'

print ""
if (( FAILURES > 0 )); then
  print "FAILED: $FAILURES check(s)"
  exit 1
fi
print "PASS: build-app.sh produces a correctly baked bundle."
