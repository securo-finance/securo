# 0045. A managed instance keeps its own settings out of the admin's reach

- Status: Accepted
- Date: 2026-11-25
- Origin: #1886 (on the hosted version the operator configures providers and the rest behind the scenes, and the customer must not change them)

## Context

Securo can be run two ways: self-hosted, where the person running it is also the person using it, and hosted, where the operator runs the instance and a customer's administrator uses the screen. The two differ in who owns what.

Some settings are instance-wide and belong to whoever operates the server: the bank and exchange-rate provider credentials, whether public sign-up is open, the default theme colours, the number and date formats, the card accounting mode, the timezone and the text of the password-recovery email. They are stored in `app_settings` with no `workspace_id`, so one value decides for everybody on the instance. On a hosted instance a customer's administrator changing them changes them for every other customer.

A flag per feature would grow into a matrix no one tests. One switch is enough, because the operator either runs the instance or does not.

## Decision

- One setting, `MANAGED_INSTANCE` (`managed_instance` in `backend/app/core/config.py`), off by default, so a self-hosted instance is exactly as before ([0004](0004-optional-capabilities.md)).
- On, the instance settings — `INSTANCE_SETTINGS` in `backend/app/api/admin.py`, every key of `ALLOWED_SETTINGS` — are the operator's. `PATCH` and `DELETE /api/admin/settings/{key}` and `PATCH /api/admin/provider-settings/{provider}` answer `403` with `detail.code = "instance_managed"`. So does `PUT /api/admin/email`: the recovery templates are stored outside `ALLOWED_SETTINGS`, under their own `email_templates` row, and are locked explicitly because the message leaves the server with the operator's sender and lands in every user's inbox on the instance. Reading them stays open: the admin screen has to show what is in force.
- `GET /api/admin/instance-mode` tells the admin screen what is locked (`managed`, `locked_settings`, `locked_providers`). It is for the superuser, like the rest of that screen.
- The screen shows the locked sections read-only with "Configured by Securo" in place of the control, instead of a form that would fail on save.
- User management, and everything a workspace owns — accounts, categories, rules, budgets, currency, theme, language, notification preferences, access tokens — is not touched by the flag.

## Consequences

- The flag does not separate the instance operator from a workspace's owner: a superuser is still a superuser of the whole instance. On a single database with row-level security, that separation and moving the instance-wide settings to the workspace have to come before a second customer shares one instance. The flag is the first step, not the whole gate.
- A new instance setting added to `ALLOWED_SETTINGS` is locked by default on a managed instance. That is the safe direction: a key the operator has not decided about is not the customer's to change. A setting stored outside `ALLOWED_SETTINGS`, as the recovery templates are, has to be refused by its own route and is easy to miss: it was, until #596.
- The screens still call the same endpoints. A write attempted from an old bundle, or by hand, is refused by the API, not only hidden by the UI.

## Enforced by

`backend/tests/test_admin_instance_mode.py`: the mode reports nothing locked with the flag off and the locked list with it on; a setting write, a setting delete, a provider write and an email-template write answer `403 instance_managed` while the saved value stays (the templates are checked in the database, not only in the response); reading providers and the rest of the admin screen still works; an unknown key is still `400`; a non-admin cannot read the mode.
