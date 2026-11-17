#!/bin/sh
# Start the Vite dev server, reinstalling dependencies first when they changed.
#
# The dev compose file keeps node_modules in an anonymous volume, and Compose
# reuses that volume on `docker compose up --build`. Without this check, an
# update that adds a dependency would run against the old node_modules.
set -e

stamp=node_modules/.package-lock.sha256
current=$(sha256sum package-lock.json | cut -d ' ' -f 1)

if [ "$(cat "$stamp" 2>/dev/null)" != "$current" ]; then
	echo "package-lock.json changed, reinstalling dependencies (npm ci)"
	npm ci
	echo "$current" > "$stamp"
fi

exec npm run dev
