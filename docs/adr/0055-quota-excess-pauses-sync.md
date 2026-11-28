# 0055. An excess over the limits has an effect only after a date the operator sets

- Status: Accepted
- Date: 2026-11-28
- Origin: #1973 (what an account above its limits can do, and from when)
- Extends: [0049](0049-hosted-usage-quotas.md)

## Context

ADR 0049 gives a managed instance four numbers per account and checks them only when something new is created. Lowering an account's numbers below what it already holds deletes nothing and refuses nothing, and `check_fit` tells the operator how far above each number the account is. It leaves one question open on purpose: what the instance does about an excess that stays.

Two lines frame the answer. ADR 0049 says the code never deletes, archives or chooses anything on the person's behalf. And stopping a whole account is a separate mechanism (#1875, #1970), meant for when the operator has to stop an account, not for an excess.

## Decision

**A date per account, set by the operator.** `user_quotas` gains `excess_effective_at`, a nullable timestamp in UTC. It is written by `PUT /api/operator/quotas/{user_id}/excess-date` (`quota_service.set_excess_date`), behind the operator token like the rest of ADR 0049. With no date nothing changes, exactly as before. Before the date nothing changes either. Setting a date on an account with no row creates one that holds only the date (`has_limits` false): the account keeps following the operator defaults of ADR 0056, or stays unbounded without them, so the date alone changes no number. Writing the account's numbers sets `has_limits`, and from then on its own row wins as ADR 0056 says. The excess is measured against the limits that hold for the account, its own or the defaults.

**After the date, the effect touches only what is above a number:**

- Above the workspace numbers: no new workspace. This was already the case under ADR 0049.
- Above the member number: no new member. This was already the case.
- Above the connection number: bank and plugin sync pause for every workspace the account created (`quota_service.check_sync`). The check runs in `connection_service.sync_connection` and in `app/plugins/connections.py` before the plugin runs, so the scheduled sync (`app/tasks/sync_tasks.py`, `app/tasks/plugin_tasks.py`) and "sync now" all go through it. A paused scheduled sync is skipped and logged. It is not a failure, so it adds no backoff and does not touch the connection's status. A manual sync answers 403 with the code `quota_sync_paused`. Nothing is disconnected, and reading, writing and exporting go on as usual.

**It ends by itself.** Every count is derived from rows each time, as in ADR 0049. The moment the account fits its numbers again, or the operator raises a number or clears the date, sync resumes with no one acting.

**The person sees it.** `GET /api/quotas/mine` answers, on a managed instance only, where the signed-in account stands: the same per-counter numbers `check_fit` gives the operator (current, maximum, excess), the date, and whether sync is paused. Elsewhere it answers 404 as if it did not exist. The sidebar shows it once a date is set (`frontend/src/components/quota-excess-banner.tsx`). A notification of kind `quota_excess` (ADR 0041) goes to the account that created the workspace. It is raised when the date is set, raised again when the date comes, and ends when the account fits or the date is cleared (`notification_service._check_quota`). The kind exists only on a managed instance.

**Off the managed instance nothing of this runs**, as in ADR 0049: `check_sync` returns at once, the route answers 404, and the notification kind is not offered.

## Consequences

- The open question in ADR 0049 is closed: an excess that stays pauses sync after the operator's date, and nothing else.
- When to set a date, and how far ahead, is the operator's decision. It stays outside this repository.
- Deciding which connection or workspace to remove stays with the person. The code says how many, never which.

## Enforced by

- `backend/tests/test_quota_excess.py`: no date means no effect; before the date, no effect; after it, sync is refused and the connections are untouched, both native and plugin; fitting again resumes sync; the manual sync answers 403; the operator route needs the token; the notification follows the date and ends when the account fits; the person's route exists only on a managed instance.
- `frontend/src/components/quota-excess-banner.test.tsx`: the banner shows only what is above, and nothing without a date or off a managed instance.
