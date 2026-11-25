# 0041. Notifications live inside the app, one row per person, and the first kinds are read from stored state

- Status: Accepted
- Date: 2026-11-24
- Origin: failures nobody sees (#1061, #805), budget alerts (#630)
- Extends: [0007](0007-background-work-on-celery.md)

## Context

A connection that stopped syncing, a recurring bill that did not generate, a scheduled backup that failed: Securo knew, and only someone opening the right screen did too. Delivering such news outside the app (email, push, chat) needs credentials, servers and outbound calls, and first needs one place that decides what is worth telling, or each channel ends up with rules of its own.

## Decision

- **In the app only.** `notifications` rows, a bell with what is open, a page that also lists the past ones, no outbound call. It is core, not an optional capability: no switch, as it sends nothing and needs no service.
- **Read or unread is all a person sees.** Not every notification has something to fix, so nothing on screen speaks of resolving, and the texts stay true as history ("stopped syncing").
- **Most kinds are conditions.** `notification_service` derives what holds now from what is already stored, and `_reconcile` makes the rows say exactly that: create, refresh, reopen, resolve. Running it twice changes nothing, a missed run is made up by the next one, and what was fixed leaves the bell by itself (`resolved_at`), staying on the page. No event bus or queue.
- **Seven conditions** (`_KINDS`): a connection that needs someone, or whose scheduled syncs failed three times in a row (`connection_error`); a bill due days ago that was not generated, whatever stopped it (`recurring_failed`); 80% and 100% of a budget, by what was spent and never by the projection (`budget_threshold`); a scheduled backup that failed, kept as `last_failure_at` in the backup settings (`backup_failed`); flagged transactions past their reminder date (`flag_overdue`) and reconciliation questions waiting for an answer (`reconciliation_pending`), one notification for all of each; an invoice to a client past its date (`invoice_overdue`).
- **Two kinds only inform** (`_EVENTS`): an expense someone else shares with you in a group, new or with your share changed (`group_expense`), and a payment recorded between you and another member (`group_settlement`). `expenses_shared` and `settlement_recorded` write them in the transaction that does it, never failing it; several expenses at once are one notification. Whoever did it, and the expense's author, are not told, and nobody is named by their email. A payment whose amount, currency or members are edited is told again as it is now. A person hears in the group's workspace when they belong to it, else in their own first one, if it has the module. Read, they leave the bell; undone, they are resolved by the hourly run; on the page they last 90 days.
- **A kind follows its module**, so a workspace is offered only what it has a screen for.
- **When.** An hourly task (`backend/app/tasks/notification_tasks.py`) evaluates every workspace on its own calendar. A connection is also followed in the transaction that changes it (`connection_changed`), which never fails the sync.
- **One row per person**, as reading is personal. Everyone in the workspace hears of budgets, each in their display currency and with their own share, and of the groups they are in; only who can act on it (owner, editor, manager) hears of the rest.
- **One notification, any number of ways out.** `_raise` is the only place a notification starts asking for attention, new or again. It hands it to `notification_channels.queue`, which writes one `notification_deliveries` row per channel the person wants that kind on, in the same transaction, so what is rolled back is never sent. `deliver_notifications` sends what is due, one by one: claimed with a lease, retried with growing waits, given up after five tries, and skipped when there is nothing left to say (read, over, asked again, turned off, left the workspace). Nothing in `notification_service` knows which channels exist. Delivery is at least once: a worker that dies after sending sends again.
- **A channel** (`notification_channels.Channel`) answers whether it reaches a person and sends a message of facts, writing its own text in their language and size. It is registered by `_register_configured` when its setting is on, in every process. None is shipped: this record opens the place, and sends nothing.
- **Each person chooses per kind and channel**, per workspace (`notification_preferences`). Nothing stored is the channel's default: on inside the app, off anywhere else, so a new channel writes to no one who did not ask. Turned off inside the app, rows are hidden at read time and still kept up to date, so turning a kind back on shows what holds at once.
- **Facts, not sentences.** `data` holds ids, names and decimal strings. The client writes the text, in its language and respecting privacy mode.
- **Once.** `dedup_key` is unique per person and workspace. A condition that comes back keeps its read state, and one that still holds after a week asks again; a budget threshold never does within its month.
- **Reading is open to viewers and closed to access tokens.** The write routes touch only the requester's own read state and preferences.

## Consequences

- A new condition is a check that reads stored state. If the state isn't stored, storing it comes first.
- Everything but connections takes up to an hour to show or clear.
- The evaluation runs services that fetch exchange rates on demand under `stored_rates_only()`, so nothing unattended leaves the instance (ADR 0016).
- A channel (e-mail, SMS, a chat app) is a class, its setting, a switch beside the app's in the preferences and an ADR that extends 0016; not a change to how notifications are found or raised. The first one also brings what none needs yet: the texts on the server, where a person's address other than their e-mail is kept and verified, and, for one that writes to a place shared by a workspace (a Slack channel), whose preference decides.
- Resolved rows are deleted after 90 days. The tables are left out of backups: the next evaluation recreates it.

## Enforced by

`backend/tests/test_notifications.py`, `backend/tests/test_notification_channels.py` (with a made-up channel), `backend/tests/test_write_permission_coverage.py`, `backend/tests/api_contract/`, `frontend/src/components/notification-bell.test.tsx`, `frontend/src/pages/notifications.test.tsx`.
