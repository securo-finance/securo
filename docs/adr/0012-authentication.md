# 0012. Authentication: sessions, OIDC, passkeys, TOTP and scoped access tokens

- Status: Accepted
- Date: 2026-04-03
- Origin: #52 (TOTP), #275 (OIDC), #330 (passkeys), #434 (OIDC-only mode), #1395 (access tokens), #839 (trusted proxies)

## Context

A self-hosted app is signed into by one person or by a household, sometimes behind an identity provider the owner already runs, and sometimes by scripts and AI tools acting for a user. Each of those needs a way in that grants no more than it should.

## Decision

- Sessions are JWTs from fastapi-users (`backend/app/core/auth.py`, `BearerTransport` and `JWTStrategy`, signed with `SECRET_KEY`).
- Optional ways in: OIDC (`backend/app/api/oidc_auth.py`, `OIDC_*` settings, account linking and role mapping), passkeys (`backend/app/api/passkeys.py`, `backend/app/core/webauthn.py`), TOTP second factor (`backend/app/api/two_factor.py`). `LOCAL_AUTH_ENABLED=false` makes OIDC the only way in; settings validation refuses it without a complete OIDC configuration (`backend/app/core/auth_policy.py`).
- Access tokens let a script or tool act for a user in one workspace. Scopes are `<resource>:read` and `<resource>:write` (write includes read), mapped to routes in `backend/app/core/access_token_scopes.py`, deny by default. Account, membership, admin and token minting routes are reachable only from a session. `current_superuser` never accepts a token. A token is pinned to its workspace (ADR 0002).
- Registration can be closed by an admin (`is_registration_enabled`, overriding `REGISTRATION_ENABLED`).
- Login, second factor and passkey login are rate limited per path and client IP on Redis (`backend/app/core/rate_limit.py`); `TRUSTED_PROXY_HOPS` says how many proxies to trust in `X-Forwarded-For`.

## Consequences

- A new route is unreachable by tokens until it is mapped to a resource in `access_token_scopes.py`.
- Anything that grants or changes access stays session-only.

## Enforced by

`tests/test_auth_api.py`, `tests/test_oidc_auth.py`, `tests/test_passkeys.py`, `tests/test_two_factor.py`, `tests/test_rate_limit.py`, `tests/test_access_tokens.py`, `tests/test_local_auth_cleanup.py`.
