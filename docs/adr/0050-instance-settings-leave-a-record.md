# 0050. An instance setting an administrator changes leaves a record, with no account as its target

- Status: Accepted
- Date: 2026-11-27
- Origin: #605 (board task #605: the review of #559 found `PUT /api/admin/email` saving to `app_settings` with nothing in the access log)
- Extends: [0042](0042-admin-actions-on-accounts-leave-a-record.md), [0046](0046-email-provider-and-templates.md)

## Context

ADR 0042 made every administrative mutation of an account carry a reason and leave a row in `admin_access_log`, and it left one door open: instance settings written through `PUT /api/admin/settings/{key}` and `PUT /api/admin/email` are mutations with no record at all. The email one is the one that matters. It is where the recovery message is written, and that message is the only way back into every account on the instance: a superuser who rewrites the text, or points it somewhere else within what the validator allows ([0046](0046-email-provider-and-templates.md)), changes what every locked-out person receives, and nothing said who did it, when, or what the text had been.

The table cannot name a person for this: the change is the instance's, not an account's. ADR 0042 writes each row for an action on one account; an instance setting is not one, because the affected side is every account at once.

## Decision

**`PUT /api/admin/email` records what it saved.** `admin_service.update_email_templates` in `backend/app/services/admin_service.py` reads the templates saved before, saves the new ones and adds the row in one transaction, then commits once: either the text that goes out is the logged one, or neither happened. The row reads `action` `settings.email_templates`, the actor's id and address, its IP, and `changes` naming only the languages whose text moved, `{"templates": {"en": {"from": …, "to": …}}}` with `null` standing for the built-in text. A save that changes nothing leaves the row with `changes` empty rather than inventing a difference.

**The target is the instance.** `target_user_id` stays null and `target_email` stays empty, and no `admin_access_log_viewer` rows are written. The affected side here is every account at once, so naming all of them would write one row per person per save and tell each of them nothing that is theirs. The record is read in `GET /api/admin/access-log`, which is the administrator's list.

**Nothing but the message reaches the record.** The row is built from what `email_service.validate_templates` returns, one subject and one body per language; a request carrying any other field, an API key above all, is a 400 and saves nothing. The provider and its key are configuration (`EMAIL_PROVIDER`, `RESEND_API_KEY`) and are never read by this path, so no secret can be recorded.

**No reason field.** The account routes ask for one because an administrator touching somebody's account has to say why (ADR 0042). This route changes how the instance writes its own message; it asks for nothing beyond the text, and the row carries a fixed sentence saying what the action was.

**One commit.** `set_app_setting` and `delete_app_setting` in `backend/app/services/admin_service.py` take `commit=False` for this path, the way `admin_service.update_user` adds its record to the session it is about to commit. The record cannot be lost while the change lands.

## Consequences

- The trail of who changed the recovery message, and what it said before and after, lives next to every other administrative action.
- A refused template (unknown placeholder, a link that leads elsewhere, a field the schema does not take) answers 400 and leaves neither a setting nor a record.
- The record can hold the full text of both languages. It is bounded by the validator's own limits (200 characters of subject and 4000 of body per language), and only what changed is kept.
- The other instance settings (`PUT /api/admin/settings/{key}`) still leave no record. They change presentation and reporting choices, not the path back into an account; extending this to them is a decision for when one of them starts to matter the same way.

## Enforced by

`backend/tests/test_email_providers.py`: `test_saving_the_templates_leaves_a_record_of_who_and_what` (action, actor, empty target, IP and `changes` before and after, and the provider's key absent from the row), `test_clearing_the_templates_is_recorded_with_what_it_replaced`, `test_an_unchanged_save_says_so_instead_of_inventing_a_change`, `test_a_refused_template_saves_nothing_and_leaves_no_record`, `test_a_field_the_templates_do_not_take_never_reaches_the_record` and `test_the_save_and_its_record_commit_together` (a record that cannot be written takes the save with it).
