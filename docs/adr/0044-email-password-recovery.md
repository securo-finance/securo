# 0044. Password recovery is sent over the instance's own SMTP server, behind one switch

- Status: Accepted
- Date: 2026-11-25
- Origin: #540 (production audit: `POST /api/auth/forgot-password` answered success and sent nothing)
- Extends: [0004](0004-optional-features.md), [0016](0016-outbound-calls-and-no-telemetry.md)

## Context

fastapi-users mounts the reset flow, but Securo never implemented `on_after_forgot_password` and had no way to send mail. The endpoint answered `202` and the token died there, so anyone who forgot a password had no way back in. Sending mail means an outbound connection to an SMTP server the operator chooses, which the promise of "every outbound call is gated" (ADR 0016) puts behind a switch.

## Decision

- **One switch, off by default.** `EMAIL_ENABLED` in `backend/app/core/config.py`, documented in `.env.example` and the README (ADR 0004). Off, the backend opens no connection and the recovery route answers `503` with a readable message instead of pretending a mail went out. Configured on but missing `SMTP_HOST` or a sender address refuses to start, so a half-configured relay cannot fail silently later.
- **The instance's own relay.** `backend/app/services/email_service.py` speaks plain SMTP (stdlib `smtplib`, no new dependency): `SMTP_SECURITY=starttls|ssl|none`, optional username and password, and `EMAIL_FROM` falling back to the username. The message is composed and handed to the relay in a worker thread, so a slow server never holds the event loop.
- **The route is Securo's, not fastapi-users'.** `backend/app/api/password_reset.py` replaces `get_reset_password_router()` and keeps both paths: `/api/auth/forgot-password` and `/api/auth/reset-password`. That is what lets the first one answer `503` when the switch is off. With it on, a known and an unknown address get the same `202` and the same empty body; an inactive account is answered the same way.
- **The link.** `on_after_forgot_password` builds `FRONTEND_URL/reset-password?token=...` and sends a short message in the account's language (English and Portuguese today, English otherwise). Token lifetime is `PASSWORD_RESET_TOKEN_MINUTES` (60 by default). A relay that refuses the message is logged without the token or the link and does not change the answer, so the endpoint still reveals nothing about which addresses exist. The existing rate limit (`password_reset_rate_limit`) stays on the route.
- **The sign-in page knows.** `GET /api/auth/oidc/config`, which the sign-in card already reads for its other options, reports `password_reset_enabled`; the "Forgot password?" link appears only when recovery works.
- **Outbound call** (extends ADR 0016): a message goes to the SMTP server the operator configured, only with `EMAIL_ENABLED` on and only when someone asks for a recovery link.

## Consequences

- With the switch off, "Forgot password?" is hidden and the endpoint is explicit (`503`) rather than a false success.
- Delivery is the operator's SMTP: Securo does not queue, retry or store messages, and does not know whether the relay accepted one.
- The link is the only delivery path; there are no recovery codes. An address that cannot receive mail is recovered by an administrator through the admin area, which now records that access (see #541).

## Enforced by

`backend/tests/test_password_reset_email.py`: 503 with the switch off; unknown and known addresses get the same answer; the sent message carries a working link; the link changes the password and the old one stops working; an expired token is refused; reset-password validates the new password. `backend/tests/api_contract/` records the route statuses.
