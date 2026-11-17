# 0020. An install with no secret of its own gets one, kept in the database

- Status: Accepted
- Date: 2026-11-17
- Origin: #1467 (issue #1173)

## Context

`SECRET_KEY` signs sessions and derives the keys that encrypt saved credentials: bank connection credentials, LLM API keys, provider settings and plugin secrets. The MCP signing key is derived from it unless `AGENTS_MCP_JWT_SECRET` is set (ADR 0014). Every install path used to ship a public default for both, so an install that never set them signed and encrypted with values anyone can read.

Refusing to start on a public default would stop every such install the moment it updated, including those that only pull new images and keep their old Compose file. The fix has to need nothing from the person running the install.

The value has to be the same in every process: the API, the Celery worker and beat, the one-shot `migrate` service and the MCP server. A file on a volume is not shared in every deployment (Helm with object storage, bare metal), and two processes with different keys would break credentials without an error. The database is the one thing all of them always share.

## Decision

- When `SECRET_KEY` is missing or one of the public defaults, the instance uses a random value generated once and stored in the `instance_secrets` table. A value set in the environment always wins. The code is in `backend/app/core/instance_secrets.py`, called from `get_settings()` in `backend/app/core/config.py`.
- The MCP signing key keeps ADR 0014's derivation from `SECRET_KEY` (`backend/app/agents/mcp/keys.py`), which is now never public. An `AGENTS_MCP_JWT_SECRET` set to a public default is ignored with a warning instead of stopping startup, which amends ADR 0014.
- `instance_secrets` is created by that module (`CREATE TABLE IF NOT EXISTS`), not by a migration and not as a model, as an exception to ADR 0010: the key is needed while settings first load, before migrations run on a fresh database.
- A Postgres advisory lock makes one process generate the value when several start at once.
- In the transaction that stores a new `SECRET_KEY`, credentials encrypted with a public default move to it. Key and data commit together or not at all.
- Decryption accepts the current key and the keys the instance used before (`Settings.secret_key_fallbacks`, used by `backend/app/agents/services/crypto.py` and `backend/app/plugins/vault.py`). Signatures are only ever checked against the current key.
- The key derivations live in `backend/app/core/fernet_keys.py`, which imports nothing from the app, so the move can run before models and services can be imported.

## Consequences

- Nobody has to set a secret to run Securo safely, and an update never stops an install over a public default. Installs that were on one sign everyone out once, and external MCP tokens are minted again; saved credentials keep working.
- Someone with a copy of the database also has the generated key. Setting `SECRET_KEY` in the environment keeps it out of the database.
- A new kind of encrypted column must use `crypto` or `vault` (or a derivation in `fernet_keys.py`) and be added to the move in `instance_secrets.reencrypt_with`, or values written under a public default stay readable only through the fallback keys.

## Enforced by

`backend/tests/test_instance_secrets.py`: generation and reuse, every public default, an own key left alone, the move for each kind of credential and its atomicity, fallback reads, forged tokens refused, importing each entry point with no key set, and on Postgres several processes starting at once. `backend/tests/test_mcp_jwt_secret.py` and `backend/tests/test_mcp_default_secret_exposure.py` cover the MCP key. Nothing checks that a new encrypted column joins the move.
