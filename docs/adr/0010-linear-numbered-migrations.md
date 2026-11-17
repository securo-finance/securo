# 0010. One linear chain of numbered migrations, applied before anything starts

- Status: Accepted
- Date: 2026-08-27
- Origin: #720 (chain check), #1084 (one-shot migrate service)

## Context

Alembic allows branching histories with hash revision ids. With many pull requests open at once that produced two heads after merges, and services starting against a database that was still being migrated.

## Decision

- Migrations live in `backend/alembic/versions/`, named with a zero-padded sequence number that is also the revision id; `down_revision` is the previous number. The history is one line. Steps are in `CONTRIBUTING.md` ("Adding a migration").
- A pull request whose number collides with one merged meanwhile renumbers.
- A merged migration is not edited. A fix is a new migration.
- In Compose, the one-shot `migrate` service runs `alembic upgrade head`. The backend, workers and MCP server wait for it and refuse to start unless the database is at head (`alembic current --check-heads`). Restarting the backend does not migrate: run `docker compose run --rm migrate`.

## Consequences

- Most tests build the schema from the models (`Base.metadata.create_all`), not from migrations. One test applies the migrations and compares the result with the models, so a model change without its migration fails CI.
- Migrations run on every instance on upgrade, unattended: they must be safe on real data (no destructive change without a backfill, Postgres specifics tested against Postgres).

## Enforced by

The "Migration Chain" CI job runs `backend/scripts/check_migration_chain.py` on the branch merged with `main`; `tests/test_migration_chain.py` tests the checker. `tests/test_schema_matches_migrations_postgres.py` applies every migration to an empty PostgreSQL database and compares it with the models. Drift that predates it is frozen in `tests/schema_drift_baseline.txt`: new drift fails, and a line that no longer shows up fails too, so the list only shrinks. Nothing stops an edit to a merged migration.
