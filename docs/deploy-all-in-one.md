# All-in-one deployment

Three containers instead of six: `db`, `redis`, and one `app` that serves the API and the
frontend and runs the background worker.

This is an **optional** topology. `docker-compose.prod.yml` and the Helm chart are unchanged
and remain the default; nothing here affects them.

## When to use it

Use it if you run Securo for yourself or your household. The multi-service split exists so
the API, the worker and the frontend can be scaled and restarted independently — which is
worth having when several people hit the instance, and worth nothing when one person does.
The chart already reflects that: every workload defaults to a single replica and autoscaling
is off.

Beyond the smaller footprint, collapsing the services removes three problems rather than
just tidying up:

- **One volume instead of three.** The API, the worker and the MCP server all need
  `/app/data/attachments`, `/app/data/agent_knowledge` and `/app/data/embedding_models`. In
  Kubernetes that means a `ReadWriteMany` volume. On hosting platforms that allow one volume
  per service, knowledge-base ingestion cannot work at all, because the worker cannot read
  the file the API just wrote. One container needs one volume.
- **No proxy between the browser and the API.** The separate frontend container has to
  resolve and proxy to the backend, which is where a recurring family of 502s comes from.
  Serving the frontend from the API removes `BACKEND_URL`, the nginx `resolver` directive and
  the problem.
- **One process reading the configuration.** The API and the worker load the same `.env` from
  the same working directory, so they cannot disagree about provider settings.

## When not to use it

- You want to scale the API or the worker independently, or run more than one replica.
- You are on Kubernetes. The Helm chart is the better fit; keep using it.
- You want a dead worker to be restarted without touching the API. Here, if either process
  exits the container exits and your restart policy brings the whole thing back.

## Quick start

```bash
git clone https://github.com/securo-finance/securo.git && cd securo
cp .env.example .env      # set SECRET_KEY at minimum
docker compose -f docker-compose.aio.yml up --build -d
```

Open <http://localhost:8000> and create an account.

Note the port: **8000**, not 3000. The API and the frontend are the same origin now, so there
is only one.

## Migrating from `docker-compose.prod.yml`

**Read this before switching.** The multi-container setup keeps your uploads in three named
volumes. The all-in-one setup uses one. If you start it without copying them across, Securo
comes up looking healthy with **no attachments and an empty knowledge base, and logs no
error** — the directories are simply created empty.

Stop the old stack first, then copy each volume into the corresponding subdirectory of the
new one:

```bash
docker compose -f docker-compose.prod.yml down

# Create the target volume before copying into it.
docker volume create securo_appdata

for pair in \
  "securo_attachments:attachments" \
  "securo_agent_knowledge:agent_knowledge" \
  "securo_agent_embedding_models:embedding_models"
do
  src="${pair%%:*}"; dst="${pair##*:}"
  docker volume inspect "$src" >/dev/null 2>&1 || continue
  docker run --rm \
    -v "$src":/from \
    -v securo_appdata:/to \
    busybox sh -c "mkdir -p /to/$dst && cp -a /from/. /to/$dst/"
done
```

Verify before starting the app:

```bash
docker run --rm -v securo_appdata:/data busybox ls -la /data /data/attachments
```

The database volume (`securo_pgdata`) is reused as-is — both topologies name it the same way,
so Postgres keeps its data and no migration is needed there.

> **Do not run both topologies against the same database.** Alembic takes no advisory lock,
> so two stacks starting at once would race each other's migrations. Use one or the other.

### Reverting

Nothing here is one-way. `docker compose -f docker-compose.aio.yml down`, then bring the old
stack back up — the three original volumes are untouched by the copy above.

## Configuration

The environment is the same as `docker-compose.prod.yml` with these differences:

| Variable | Change |
|---|---|
| `APP_PORT` | Replaces `FRONTEND_PORT` and `BACKEND_PORT`. Defaults to `8000`. |
| `FRONTEND_URL` | Now the app's own URL. **Still required** — see below. |
| `BACKEND_URL` | Gone. There is no proxy. |
| `FRONTEND_DIST_PATH` | Set by the image to `/app/frontend_dist`. Leave it alone. |
| `UVICORN_HOST` | Bind address, default `0.0.0.0`. Set to `::` on IPv6-only networks. |
| `UVICORN_FORWARDED_ALLOW_IPS` | Set this when behind a reverse proxy. See below. |
| `CELERY_CONCURRENCY` | Worker processes, default `2`. `1` is plenty for one household. |
| `AGENTS_MCP_INPROCESS` | Serves `POST /mcp` from the API instead of a separate container. |

`FRONTEND_URL` is not just a CORS setting, so do not drop it: it builds the bank OAuth
callback URL and the OIDC redirect URI. If you put Securo on a public hostname, set it to
that hostname.

## Differences you will notice

**Bank and OIDC redirect URIs move.** The paths are unchanged but the origin is not — port
3000 becomes 8000 by default. Re-register the callback URL with Pluggy, Enable Banking or
your identity provider, or keep the same public URL in front of both.

**Passkeys survive a port change but not a hostname change.** The relying-party ID comes from
the browser's origin and ignores the port, so moving 3000 → 8000 is fine. Changing the
hostname invalidates every registered passkey — that is WebAuthn, not Securo.

**Uploads are no longer capped at 1 MB.** The nginx config never set
`client_max_body_size`, so nginx's 1 MB default silently limited every statement import and
attachment. There is no such limit here. Imports that used to fail with a 413 will work.

**Login rate limiting becomes per-IP for real.** The limiter keys on the client address,
which used to be the frontend container for everybody — one shared bucket for the whole
instance. It now sees each caller separately.

**Agent chat responses stream properly.** nginx buffered the event stream; without it in the
path, tokens arrive as they are produced.

**`curl https://your-host/transactions` returns 404.** Frontend routes are served only to
requests that accept `text/html`, which browsers always send and `curl` does not. This is
expected. **Point uptime monitors at `/api/health`**, which returns JSON to anything.

## Behind a reverse proxy

Without nginx in the container, uvicorn is what your proxy talks to. Tell it to trust the
forwarding headers, or every request will appear to come from the proxy and HTTPS-aware
redirects will break:

```yaml
environment:
  UVICORN_FORWARDED_ALLOW_IPS: "172.16.0.0/12"   # your proxy's address or network
  FRONTEND_URL: "https://securo.example.com"
```

Use the narrowest value that works. `*` trusts any caller's `X-Forwarded-For`, which lets
anyone spoof their address past the rate limiter.

## Operating it

**Logs are interleaved** — the API and the worker share one stream. Celery lines carry their
own `[timestamp: LEVEL/Process]` prefix, so `docker compose -f docker-compose.aio.yml logs -f
app | grep MainProcess` separates them.

**Restarts are all-or-nothing.** If either process exits, the container exits with that
process's status and the restart policy brings both back. Migrations run on every start and
are a no-op when the database is current.

**Shutdown is graceful.** `docker stop` gives Celery time to finish an in-flight bank sync;
`stop_grace_period` is set to 60s for that reason. Do not lower it.

**Never run more than one replica.** Celery beat runs inside the worker and keeps its
schedule in a file. A second replica means a second scheduler, and every periodic task —
bank sync, recurring generation, price refresh — fires twice.

**Updating:**

```bash
git pull
docker compose -f docker-compose.aio.yml up --build -d
```

## Platforms with one volume per service

This is the topology to use on Railway, Fly.io and similar, since the whole data directory is
a single mount. Two things to set:

- `UVICORN_HOST=::` if the platform's private network is IPv6-only.
- `FRONTEND_URL` to the public URL the platform assigns, and the matching callback URL at
  your bank provider.

Mount the volume at `/app/data`. Attachments, the knowledge store, the embedding-model cache
and the Celery beat schedule all live under it.

## Building the image

The build context is the repository root, because the image contains both the frontend build
and the backend:

```bash
docker build -f backend/Dockerfile.aio -t securo-aio .
```

`docker compose -f docker-compose.aio.yml build` does this for you. Pass
`--build-arg VITE_APP_VERSION=v0.14.4` to stamp a version into the UI; without it the
frontend falls back to `dev`.
