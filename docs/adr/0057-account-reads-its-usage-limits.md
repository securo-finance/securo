# 0057. An account can read its own usage limits

- Status: Accepted
- Date: 2026-11-28
- Origin: #1992 (#1972)
- Extends: [0049](0049-hosted-usage-quotas.md), [0056](0056-operator-quota-defaults.md)

## Context

ADR 0049 refuses a creation past a limit with `403 quota_exceeded`, and ADR 0056 lets the limits come from the operator's defaults. Neither gives the account a way to know its numbers before it hits one: a person only learns about a limit when an attempt is refused, and the screens cannot say ahead of time that a button will fail.

## Decision

**One read for the signed-in account.** `GET /api/quotas/mine` (`backend/app/api/quotas.py`, `usage` in `backend/app/services/quota_service.py`) answers, for the signed-in account only, each bounded counter with its current count and its effective maximum (`effective_limits`, so an own row or the operator's defaults). The workspace and connection counters are counted the way their gates count them. `max_members_per_workspace` is different: its gate (`check_new_member`) counts only the workspace being changed, so the account-wide answer gives the seats of the fullest workspace the account created, the one closest to the limit. A screen that adds a member to one workspace counts that workspace's own members, never this aggregate. A counter with no maximum is left out. With `MANAGED_INSTANCE` off it answers `enforced: false` and no counters.

**The screens warn, the server decides.** The screens use the answer to list the limits in the account menu and to stop before an attempt that would be refused, and they read it again after anything that frees or takes a place. The server gate from ADR 0049 stays the one that decides; a stale screen can only warn too much or too little, never let a creation through.

**A shared workspace names its owner.** In a workspace someone else created, the connection and seat limits are the creator's, so the refusal says the limit belongs to the workspace owner instead of showing numbers the member cannot change.

**One optional link.** `QUOTA_INFO_URL` is an operator page explaining the limits. When set, the answer carries it and the messages link to it; when unset, they link nowhere. It is a plain link the browser follows, not a call the server makes.

## Consequences

- An account sees its own numbers and nobody else's: the answer carries no other user's limits, and a member of a shared workspace sees only their own.
- The count is made at read time, so the screen shows what the gates would count at that moment and can be out of date by the next request.
- With workspaces of 5 and 1 members, the members counter reads 5 while a member can still be added to the second one: the counter says how close the fullest workspace is, not whether a given invitation fits.

## Enforced by

`backend/tests/test_quota_mine.py`: off without the switch; no limits lists nothing; bounded counters with their count; the operator defaults for an account without a row; an invited member sees only their own numbers; the members counter reads the fullest workspace; the link only when set; a signed-in account is required. On the screens, `frontend/src/hooks/use-quota.test.tsx`, `frontend/src/components/usage-limits-dialog.test.tsx` and `frontend/src/pages/accounts.test.tsx`.
