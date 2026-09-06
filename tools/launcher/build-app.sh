#!/bin/zsh

# Assembles Securo.app from the sources in this directory.
#
#   ./build-app.sh                      # installs into /Applications
#   ./build-app.sh --dest ~/Desktop     # build somewhere else
#   ./build-app.sh --project-dir /path  # point the app at another checkout
#
# Re-running overwrites an existing bundle in place, so this doubles as the
# upgrade path.

set -eu

SRC_DIR="${0:A:h}"
REPO_ROOT="${SRC_DIR:h:h}"
DEST="/Applications"
PROJECT_DIR="$REPO_ROOT"

while (( $# )); do
  case "$1" in
    --dest)        DEST="${2:?--dest needs a path}"; shift 2 ;;
    --project-dir) PROJECT_DIR="${2:?--project-dir needs a path}"; shift 2 ;;
    -h|--help)     sed -n '3,11p' "$0"; exit 0 ;;
    *)             print -u2 "Unknown option: $1"; exit 1 ;;
  esac
done

[[ -f "$PROJECT_DIR/docker-compose.prod.yml" ]] || {
  print -u2 "No docker-compose.prod.yml under $PROJECT_DIR — pass --project-dir."
  exit 1
}

APP="$DEST/Securo.app"
print "Building $APP (project: $PROJECT_DIR)"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/tests"

cp "$SRC_DIR/Info.plist"    "$APP/Contents/Info.plist"
cp "$SRC_DIR/AppIcon.icns"  "$APP/Contents/Resources/AppIcon.icns"
cp "$SRC_DIR/app-stub.sh"   "$APP/Contents/MacOS/Securo"
cp "$SRC_DIR/tests/test-launch-securo.sh" "$APP/Contents/Resources/tests/"

# Bake the checkout path so the app works from /Applications, where it can no
# longer derive the project location from its own path. Anchored to the
# assignment: a blind global replace would also rewrite the placeholder inside
# the guard below it, turning that comparison into "x == x" and sending the
# app back to deriving a (wrong) path from its own location.
sed "s|^BAKED_PROJECT_DIR=\"__SECURO_PROJECT_DIR__\"$|BAKED_PROJECT_DIR=\"$PROJECT_DIR\"|" \
  "$SRC_DIR/launch-securo.sh" > "$APP/Contents/Resources/launch-securo.sh"

grep -q '^BAKED_PROJECT_DIR="'"$PROJECT_DIR"'"$' "$APP/Contents/Resources/launch-securo.sh" || {
  print -u2 "Failed to bake PROJECT_DIR into the bundle."
  exit 1
}

chmod +x "$APP/Contents/MacOS/Securo" \
         "$APP/Contents/Resources/launch-securo.sh" \
         "$APP/Contents/Resources/tests/test-launch-securo.sh"

# Let Finder pick up a changed icon/version instead of serving a stale one.
touch "$APP"

print "Done. Open it from Finder, Launchpad or: open -a Securo"
