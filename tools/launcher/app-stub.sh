#!/bin/zsh

# CFBundleExecutable. Backgrounds the real launcher so Finder/Dock stop
# bouncing immediately instead of waiting out the Docker startup.

set -u

APP_ROOT="${0:A:h:h}"
LAUNCHER="$APP_ROOT/Resources/launch-securo.sh"
STATE_DIR="${TMPDIR:-/tmp}/securo-launcher"

mkdir -p "$STATE_DIR"
nohup "$LAUNCHER" >> "$STATE_DIR/launcher.log" 2>&1 &
