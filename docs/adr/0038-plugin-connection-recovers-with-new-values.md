# 0038. A plugin connection is fixed in place with new values, and a routine-only one proves them at once

- Status: Accepted
- Date: 2026-11-23
- Origin: a routine-only connection left with no way back after its service refused its keys
- Extends: [0035](0035-plugin-file-fields-and-routines.md)

## Context

[0035](0035-plugin-file-fields-and-routines.md) lets an integration run routines with no sync of its own. When the service refuses such a connection's keys it goes to "sign in needed" (`needs_auth`), which stops its routines. The way out was to save the connection's form again (`PUT /api/plugins/connections/{id}/values`), but the connection list offered only deleting it, which takes its routines' state with it. And after new values, a connection with no sync had nothing that ran right away: it waited for each routine's own window, up to a week, before anyone knew whether the new keys worked. Any save, a rename included, also reset its status and error.

## Decision

- The connection list offers the form that set a key-based connection up: "Update keys" when its keys were refused, "Edit" otherwise. Saving keeps the connection and its routines' state.
- `connections.update_values` returns whether any value changed. Only a change resets the connection (status back to pending for a key-based one, failures and error cleared, the bank's failure notice forgotten), and only a change makes the route start a run. A rename alone, or the same values saved again, keeps the status, the error and the history, and queues nothing, also while the connection is still pending.
- With changed values, a connection that only runs routines runs every routine once, right away (`run_plugin_routines` with `force`), whether or not it is due: that run is what tells the new keys work, and the first success marks it active. A connection that syncs is proved by that sync, and its routines keep their own schedules.
- A forced routine that finds the connection busy (its lock held by a sync or another routine) didn't run, so it isn't counted as done: the task queues those routines again every 30 seconds, up to 20 times, and then leaves them to their schedule. `needs_auth` still stops the round, with nothing retried.

## Consequences

- Fixing refused keys never costs a connection its routines' state or history.
- Saving new values on a routine-only connection calls the plugin once per routine at once, even for weekly ones; that is the price of knowing the keys work now.
- A connection that stays busy for more than about ten minutes after new values falls back to the schedule, as before this ADR.

## Enforced by

`backend/tests/test_plugin_routines.py`: a rename (or the same key) is not a change and keeps the status; renaming a pending connection queues nothing; new values queue a forced round; a forced round runs routines that are not due; a busy connection hands its routines back, the retry runs only those, and the task requeues them and gives up after its last attempt; and a refused key fixed by new values, after which the routines run and the connection is active. `frontend/src/components/plugin-code.test.tsx`: the form to update the keys of a connection whose keys were refused.
