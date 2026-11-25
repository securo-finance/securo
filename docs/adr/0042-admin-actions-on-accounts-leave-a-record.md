# 0042. An administrator manages accounts, never becomes one, and every action leaves a record the affected side can read

- Status: Accepted
- Date: 2026-11-25
- Origin: #541 (board task #541: the hosted launch rehearsal found that an administrator could take over a customer account)

## Context

Securo will be sold hosted, and the production rehearsal found two problems in the same place: an administrator could set a customer's password through `PATCH /api/admin/users/{id}` and then sign in as that customer, and nothing recorded that the account had been touched at all. The privacy line we want to stand behind ("our team does not look at your data in the routine; exceptional access needs a reason and is recorded") was therefore untrue in two ways: there was an access path with no reason, and no record.

Two things had to be decided together, because either alone is theatre: take the password field away, and make every remaining administrative action on an account carry a reason and leave a trail the affected side can read.

## Decision

**A password belongs to the person it authenticates.** `AdminUserUpdate` in `backend/app/schemas/admin.py` has no `password` field, and the schema refuses unknown fields (`extra="forbid"`), so an older client that still sends one gets a 422 instead of having it silently dropped. `admin_service.update_user` in `backend/app/services/admin_service.py` no longer writes `hashed_password` for anyone. Administrators still create accounts with an initial password on `POST /api/admin/users`; that path is replaced by invite links (#542) and until then it stays as it is, because it is creation, not takeover.

**Every administrative mutation of an account needs a reason.** `PATCH` carries `reason` in the body (3 to 500 characters, required); `DELETE` carries it as a required query parameter. The request without one is a 422 and nothing changes.

**The action and its reason are one transaction.** The row in `admin_access_log` (`backend/app/models/admin_access_log.py`: actor id and email snapshot, target id and email snapshot, action, reason, client IP, created at) is added to the same session as the update or the deletion and committed with it: either both happen or neither does. The IP is read with `resolve_client_ip`, the same trusted-proxy rule as the rate limiter ([0024](0024-client-ip-behind-trusted-proxies.md)), because it is evidence and must not be what the caller wrote in a header. The table is instance-wide, not per workspace: it records who at the vendor touched which account, so it has no `workspace_id` and is listed as a deliberate exception in `backend/tests/test_workspace_boundary_schema.py`.

**The record outlives the accounts it names.** The foreign keys clear on delete (`ON DELETE SET NULL`) and the emails are snapshots, so deleting a user never deletes the evidence of the deletion, and a deleted administrator still appears as someone who used to have this address.

**The affected side can read it.** `GET /api/access-log/mine` (`backend/app/api/access_log.py`) returns entries about the caller's own account and about members of workspaces the caller owned or managed when the entry was written. Whoever could have been impersonated through an account, or whose workspace could have been read through it, gets to know that an administrator touched it, when and why. An owner is named through a membership and a manager through `workspaces.managed_by_user_id`, which carries owner rights without putting them on the roster ([0003](0003-workspace-roles-and-write-gates.md)). The audience is stored with the entry in `admin_access_log_viewer` (`backend/app/models/admin_access_log.py`) instead of being resolved when the log is read: a deletion takes the target's memberships with it, and a live join would then hide the member's records, the deletion among them, from the very owner who needs to see them. The frontend shows it in the account menu (`frontend/src/components/access-log-dialog.tsx`), a page of 50 at a time, because the endpoint refuses more than 100. `GET /api/admin/access-log` gives administrators the whole list, optionally for one account.

**Reads are not logged.** Listing users or reading a profile stays out of the log: it is not a mutation and would bury the actions that matter. If a read-level audit is ever needed, it is a separate decision with its own retention and cost.

## Consequences

- An administrator who needs to help someone locked out sends a recovery link (#540); they cannot hand out a password.
- Any automation that patched a user without a reason now gets a 422, and that is the point.
- The log grows with administrative work only, never with routine use.
- Admin-created accounts and member invites still set an initial password typed by someone else; closing that is #542, and it depends on the email transport from #540.
- There is no admin route that acts on a workspace today. When one exists, it logs into the same table with the workspace as its target.

## Enforced by

`backend/tests/test_admin_access_log.py`: `test_admin_cannot_set_a_password` (422 and the customer's password still signs in), `test_update_without_a_reason_is_refused`, `test_update_is_logged_with_actor_target_and_ip`, `test_delete_needs_a_reason`, `test_delete_is_logged_and_outlives_the_account`, `test_the_affected_account_sees_what_was_done`, `test_a_workspace_owner_sees_access_to_a_member`, `test_a_workspace_owner_still_sees_it_after_the_member_is_deleted`, `test_a_workspace_manager_sees_access_to_a_member` and `test_someone_else_sees_nothing`. `frontend/src/components/access-log-dialog.test.tsx` covers what the affected side is shown, including that a second page is appended and never asks for more than the endpoint allows.
