# 0035. Plugin integrations take files in their form and run routines on schedules of their own

- Status: Accepted
- Date: 2026-11-23
- Origin: plugin integrations that need more than one text form and one sync
- Extends: [0015](0015-plugins.md)
- Exception to: [0019](0019-s3-storage.md)
- Extended by [0038](0038-plugin-connection-recovers-with-new-values.md): a connection is fixed in place with new values, and a routine-only one runs its routines at once to prove them.

## Context

An integration's connection form took text only, and its code ran in one place on a schedule: `sync`, every `sync_minutes`. Some services need a file to connect (a key bundle, a client certificate, a mapping a person prepared), and some work isn't one sync: sending something on one rhythm, collecting what came back on another, a weekly cleanup. Folding all of that into `sync` makes one failure stop everything and one interval serve all of it.

## Decision

- **File fields.** A form field can be `"type": "file"`, with optional `accept` (the file extensions it takes, such as pdf) and `max_kb` (512 by default, up to 2,048). The browser sends the file as `{"name", "content"}` with the content in base64, and `connections._split` checks it: the extension, the decoded size, and only the file's own name (never a path). It is stored like any field: sealed with the secrets when the field is `secret`, in `config` otherwise. The plugin gets the same `{"name", "content"}` in `context.secrets` or `context.config`. The API never returns a file's content: a non-secret file shows as its name and size, a secret one only as set. Leaving a file out of an update keeps it.
- **Where a file's bytes live.** A file field is an exception to [0019](0019-s3-storage.md): its bytes stay inline in the field's value, base64-encoded, sealed with the connection's secrets when the field is `secret` and in its `config` otherwise. They never go through `get_storage_provider()`, so there is no storage key and the rule that keys start with the workspace doesn't apply to them. The boundary is held by the row they live on: `plugin_connections` is scoped to its workspace, and `max_kb` bounds what one field can hold. They live there because the plugin gets the bytes in its `context` on every call, and Securo has no attachment to store, list or serve: a file in storage would only add a second copy to keep in step and to remove with the connection.
- **Routines.** An integration can declare up to 10 `routines`, each with a `key`, a `name`, `every_minutes` (15 minutes to 7 days) and, optionally, `timeout_seconds` (5 to 600; the runner's own `PLUGINS_CALL_TIMEOUT` still caps it). Securo calls the plugin's `routine(params, context)` with `params["routine"]` naming it, its own `state` and `last_run_at`; it returns `{"state", "secrets"}` like `sync`, both optional.
  - A row per connection and routine (`plugin_connection_routines`, migration 130) keeps its state, last run, error and failures, apart from the connection's sync. Rows follow the installed manifest: an update adds new routines, removes dropped ones and keeps the history of the rest.
  - The beat task that syncs connections also runs due routines (`plugin_tasks.run_due_routines`). Each backs off on its own failures as a sync does. Celery still never runs plugin code.
  - A routine holds the connection's lock, as `sync` does: one plugin call per connection at a time, so secrets one call refreshes are never overwritten by another. A busy connection makes the routine wait for the next round.
  - A routine's failure is its own: the connection's status doesn't change, except that `NeedsAuth` puts the whole connection in "sign in needed", which stops its sync and its routines. A connection with no sync of its own is active once a routine ran.
  - `POST /api/plugins/connections/{id}/routines/{key}/run` runs one now and waits for it (write permission; 409 while the connection is busy or needs signing in). The connection list shows each routine with its schedule, last run and error.

## Consequences

- A plugin can ask for a file at connection time and split its work into independent schedules, without anything in Securo knowing what the file or the work is for.
- A file travels base64-encoded, so a field's 2 MB limit is about 2.7 MB on the wire; the runner gets it on every call of that connection.
- Routines run one after another in the beat task, as syncs do: a slow routine delays the ones after it until the next round.
- Event triggers (running a routine when something happens in Securo) are not part of this; a routine runs on its schedule or when someone runs it.

## Enforced by

`backend/tests/test_plugin_routines.py`: the manifest's rules for both, a file checked and stored (and refused by extension, size, encoding or a missing name), the API hiding a file's content and keeping a file left out, a routine run through the real runner with its own state and merged secrets, its failures masked and backed off, `NeedsAuth`, the lock, the schedule, routines following a plugin update and removal, and the run-now endpoint. `frontend/src/components/plugin-code.test.tsx`: the file picker (type and size refused before upload), the kept file, the routine list and running one now.
