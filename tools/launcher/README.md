# Securo macOS launcher

A small `Securo.app` bundle that starts the self-hosted stack and opens it in a
browser, so the app can be launched from Finder, Launchpad or Spotlight instead
of a terminal.

It is a convenience wrapper around `docker compose -f docker-compose.prod.yml
up -d` — it does not replace it, and it changes nothing about how Securo runs.

## Install

```sh
tools/launcher/build-app.sh
```

That assembles `/Applications/Securo.app`, baking in the path of the checkout it
was built from. Re-run it to upgrade an existing bundle.

```sh
tools/launcher/build-app.sh --dest ~/Desktop           # build elsewhere
tools/launcher/build-app.sh --project-dir /srv/securo  # target another checkout
```

## What it does

1. Starts the container runtime if it is down — `colima start` when Colima is
   installed, otherwise it opens Docker Desktop and waits.
2. Pulls the published images, so a local image older than the database schema
   cannot silently crash-loop the backend. A failed pull is non-fatal, so
   offline starts still work.
3. Runs `docker compose -f docker-compose.prod.yml up -d`.
4. Waits until **both** the frontend (`:3000`) and the API
   (`:8000/api/health`) answer, and aborts if any container enters a restart
   loop.
5. Opens `http://localhost:3000`.

Progress is reported as macOS notifications; a timestamped log is written to
`$TMPDIR/securo-launcher/launcher.log`.

Checking both tiers matters: the frontend is nginx serving static files and
answers `200` even when the API is dead, so a frontend-only probe reports a
broken stack as ready and the user lands on a login screen that cannot reach
the server.

## Configuration

Every path and timeout is an environment variable with a sensible default —
useful for non-Homebrew installs and for the tests:

| Variable | Default |
| --- | --- |
| `PROJECT_DIR` | baked in at build time |
| `DOCKER_BIN` | `/opt/homebrew/bin/docker` |
| `COLIMA_BIN` | `/opt/homebrew/bin/colima` |
| `APP_URL` | `http://localhost:3000` |
| `HEALTH_URL` | `http://localhost:8000/api/health` |
| `TIMEOUT_SECONDS` | `180` |
| `STATE_DIR` | `$TMPDIR/securo-launcher` |

## Tests

```sh
zsh tools/launcher/tests/test-launch-securo.sh
```

The suite stubs `docker`, `colima`, `curl`, `open` and `osascript` with fake
binaries in a temp directory, so it exercises the launcher's decisions —
readiness probing, crash-loop detection, pull failure handling, Colima startup
— without touching a real stack.

## Notes

- Notification strings are in Estonian.
- The bundle is unsigned. Gatekeeper may need a one-time right-click → Open.
