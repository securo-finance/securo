"""Instance secrets for installs that never set their own.

SECRET_KEY signs sessions and encrypts saved credentials, and the MCP signing
key is derived from it unless AGENTS_MCP_JWT_SECRET is set
(app/agents/mcp/keys.py). Every install path used to ship a public default
(#1173), so an install that never set one signed and encrypted with a value
anyone can read.

When the environment carries no value, or one of those public defaults, the
instance uses a random value generated once and kept in the database, the one
place the API, the Celery worker and beat, the migrate job and the MCP server
always share whatever the deployment looks like. A value set in the
environment always wins, so an install that already manages its own keeps
doing so.

Generating the key re-encrypts, in the same transaction, the credentials that
were encrypted with the public default, so bank connections, LLM keys,
provider settings and plugin secrets keep working. Decryption also accepts the
earlier keys (see `secret_key_fallbacks`), so a value written by a process
that was still running the old key is not lost either. Signatures are never
checked against a public default: sessions signed with one end, and people
sign in again once.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine, Iterable

from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

# Every default that shipped in the code, Compose files, Helm chart and
# example env files carries one of these markers.
PLACEHOLDER_SECRET_MARKERS = ("change-me-in-production", "change-in-production")

# The exact public defaults, tried when opening values encrypted before an
# instance key existed.
PUBLIC_SECRET_KEYS = (
    "change-me-in-production",
    "dev-secret-change-in-production",
)

SECRET_KEY_NAME = "secret_key"

_TABLE = "instance_secrets"
# What an operator runs, as the owner of the tables, to read the stored key.
STORED_KEY_QUERY = f"SELECT value FROM {_TABLE} WHERE name = '{SECRET_KEY_NAME}'"
# Any constant works: it only has to be the same in every process.
_LOCK_ID = 0x5EC5_0001

# How long a process waits for the database when it needs the instance key to
# start. Compose and Helm start the API next to a database that may still be
# coming up.
_CONNECT_DEADLINE_SECONDS = 90.0

# SQLSTATE classes no wait will fix: the database answered and refused the
# role, at sign-in (28) or for lacking a privilege (42, e.g. 42501).
_REFUSED_SQLSTATE_CLASSES = ("28", "42")


def _refused(exc: BaseException) -> bool:
    """True when the database answered with a refusal rather than being
    unreachable, so retrying only delays the same error."""
    if not isinstance(exc, DBAPIError):
        return False
    code = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    return isinstance(code, str) and code[:2] in _REFUSED_SQLSTATE_CLASSES


def _engine(database_url: str, timeout: float):
    # asyncpg waits 60 seconds for a connection by default; a host that drops
    # packets must not hold startup that long on every attempt.
    connect_args = {"timeout": timeout} if "+asyncpg" in database_url else {}
    return create_async_engine(database_url, poolclass=NullPool, connect_args=connect_args)


def is_public_or_missing(value: str | None) -> bool:
    value = (value or "").strip()
    return not value or any(marker in value for marker in PLACEHOLDER_SECRET_MARKERS)


def _run(coro_factory: Callable[[], Coroutine]):
    """Run a coroutine to completion from synchronous code.

    Settings are first read while modules import, sometimes inside a running
    event loop (uvicorn imports the app from within one), so the coroutine
    always gets a loop of its own on a separate thread.
    """
    result: dict = {}

    def target() -> None:
        try:
            result["value"] = asyncio.run(coro_factory())
        except BaseException as exc:  # re-raised in the caller's thread
            result["error"] = exc

    thread = threading.Thread(target=target, name="securo-instance-secrets", daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


async def _ensure_table(conn: AsyncConnection) -> None:
    await conn.execute(text(
        f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
        " name VARCHAR(64) PRIMARY KEY,"
        " value TEXT NOT NULL,"
        " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    ))


def _locking(conn: AsyncConnection) -> str:
    """Row locks for the rows the move reads and rewrites, so a reconnect or
    a saved setting committing meanwhile is neither lost nor overwritten with
    the older value. SQLite has no FOR UPDATE (and one writer at a time)."""
    return " FOR UPDATE" if conn.dialect.name == "postgresql" else ""


async def _table_exists(conn: AsyncConnection, table: str) -> bool:
    if conn.dialect.name == "postgresql":
        found = await conn.scalar(text("SELECT to_regclass(:t)"), {"t": table})
    else:
        found = await conn.scalar(
            text("SELECT name FROM sqlite_master WHERE type = 'table' AND name = :t"), {"t": table}
        )
    return found is not None


async def _read(conn: AsyncConnection, name: str) -> str | None:
    if not await _table_exists(conn, _TABLE):
        return None
    return await conn.scalar(text(f"SELECT value FROM {_TABLE} WHERE name = :n"), {"n": name})


OnCreated = Callable[[AsyncConnection, str], Awaitable[None]]


async def _fetch_or_create(
    database_url: str, name: str, on_created: OnCreated | None
) -> tuple[str, bool]:
    engine = _engine(database_url, timeout=10)
    try:
        async with engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                # Every process that starts at once (API, worker, beat, migrate,
                # MCP server, several replicas) comes through here. The lock
                # makes the first one generate the value and the rest read it.
                await conn.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _LOCK_ID})
            await _ensure_table(conn)
            existing = await conn.scalar(
                text(f"SELECT value FROM {_TABLE} WHERE name = :n"), {"n": name}
            )
            if existing:
                return existing, False
            value = secrets.token_hex(32)
            if on_created is not None:
                await on_created(conn, value)
            await conn.execute(
                text(f"INSERT INTO {_TABLE} (name, value) VALUES (:n, :v)"), {"n": name, "v": value}
            )
            return value, True
    finally:
        await engine.dispose()


async def _read_only(database_url: str, name: str) -> str | None:
    engine = _engine(database_url, timeout=3)
    try:
        async with engine.connect() as conn:
            return await _read(conn, name)
    finally:
        await engine.dispose()


def fetch_or_create(database_url: str, name: str, on_created: OnCreated | None = None) -> str:
    """The instance value called `name`, generated and stored on first use.

    Retries while the database is unreachable, since the caller can't start
    without it, and gives up with a clear error after a while. A refusal
    (wrong password, missing privilege) fails at once.
    """
    deadline = time.monotonic() + _CONNECT_DEADLINE_SECONDS
    delay = 1.0
    while True:
        try:
            value, created = _run(lambda: _fetch_or_create(database_url, name, on_created))
        except (OSError, TimeoutError, DBAPIError) as exc:
            if _refused(exc):
                raise RuntimeError(
                    f"The database refused to store the instance {name}: {exc}. "
                    f"Set {name.upper()} in the environment, or let the database role "
                    f"create and read the {_TABLE} table."
                ) from exc
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Could not reach the database to load the instance {name}: {exc}. "
                    "Check DATABASE_URL, or set the value in the environment."
                ) from exc
            logger.info("Database not reachable yet for the instance %s; retrying", name)
            time.sleep(delay)
            delay = min(delay * 2, 10.0)
            continue
        if created:
            logger.warning(
                "Generated a unique %s for this instance and stored it in the database. "
                "Set it in the environment to manage it yourself.",
                name.upper(),
            )
        return value


def read_existing(database_url: str, name: str) -> str | None:
    """The stored instance value, or None when there is none. Never creates
    anything. Raises when the database can't be read, so the caller can tell
    "nothing stored" from "couldn't look"."""
    return _run(lambda: _read_only(database_url, name))


# --- Re-encrypting what a public default protected ---------------------------

async def _rewrite_column(
    conn: AsyncConnection,
    table: str,
    column: str,
    rewrite: Callable[[str], str | None],
) -> int:
    if not await _table_exists(conn, table):
        return 0
    rows = (await conn.execute(
        text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL{_locking(conn)}")
    )).all()
    changed = 0
    for row_id, value in rows:
        new_value = rewrite(value)
        if new_value is not None and new_value != value:
            await conn.execute(
                text(f"UPDATE {table} SET {column} = :v WHERE id = :id"), {"v": new_value, "id": row_id}
            )
            changed += 1
    return changed


def reencrypt_with(old_keys: Iterable[str]) -> OnCreated:
    """Build the hook that moves every saved credential from the old keys to
    the new instance key, inside the transaction that stores that key."""
    from app.core.fernet_keys import credentials_fernet, plugin_secrets_fernet

    # An empty key is a real one here: installs that ran with SECRET_KEY=""
    # encrypted with it.
    old = [k for k in dict.fromkeys(old_keys) if k is not None]

    async def hook(conn: AsyncConnection, new_key: str) -> None:
        def move(fernet_for: Callable[[str], Fernet], token: str) -> str | None:
            new = fernet_for(new_key)
            try:
                new.decrypt(token.encode())
                return None  # already under the new key
            except Exception:
                pass
            for key in old:
                try:
                    plain = fernet_for(key).decrypt(token.encode())
                except Exception:
                    continue
                return new.encrypt(plain).decode()
            return None  # unreadable with any known key: leave it as it was

        def agents_value(token: str) -> str | None:
            return move(credentials_fernet, token)

        def plugin_value(token: str) -> str | None:
            return move(plugin_secrets_fernet, token)

        def bank_credentials(raw: object) -> str | None:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(data, dict):
                return None
            changed = False
            for field in ("access_url_enc", "session_id_enc"):
                token = data.get(field)
                if isinstance(token, str) and token:
                    moved = agents_value(token)
                    if moved is not None:
                        data[field] = moved
                        changed = True
            return json.dumps(data) if changed else None

        counts = {
            "llm_keys": await _rewrite_column(
                conn, "agent_llm_connections", "api_key_encrypted", agents_value
            ),
            "plugin_secrets": await _rewrite_column(conn, "plugin_connections", "secrets", plugin_value),
            "bank_credentials": await _rewrite_bank_credentials(conn, bank_credentials),
            # Only the provider credentials there are encrypted; any other value
            # fails to open with every key and is left as it was.
            "provider_settings": await _rewrite_settings(conn, agents_value),
        }
        logger.warning("Re-encrypted saved credentials under the new instance key: %s", counts)

    return hook


async def _rewrite_bank_credentials(
    conn: AsyncConnection, rewrite: Callable[[object], str | None]
) -> int:
    if not await _table_exists(conn, "bank_connections"):
        return 0
    rows = (await conn.execute(
        text(f"SELECT id, credentials FROM bank_connections WHERE credentials IS NOT NULL{_locking(conn)}")
    )).all()
    cast = "CAST(:v AS JSON)" if conn.dialect.name == "postgresql" else ":v"
    changed = 0
    for row_id, value in rows:
        new_value = rewrite(value)
        if new_value is not None:
            await conn.execute(
                text(f"UPDATE bank_connections SET credentials = {cast} WHERE id = :id"),
                {"v": new_value, "id": row_id},
            )
            changed += 1
    return changed


async def _rewrite_settings(conn: AsyncConnection, rewrite: Callable[[str], str | None]) -> int:
    if not await _table_exists(conn, "app_settings"):
        return 0
    rows = (await conn.execute(
        text(f"SELECT key, value FROM app_settings WHERE value IS NOT NULL{_locking(conn)}")
    )).all()
    changed = 0
    for key, value in rows:
        # Fernet tokens start with "gAAAAA"; skip the rest without trying keys.
        if not isinstance(value, str) or not value.startswith("gAAAAA"):
            continue
        new_value = rewrite(value)
        if new_value is not None:
            await conn.execute(
                text("UPDATE app_settings SET value = :v WHERE key = :k"), {"v": new_value, "k": key}
            )
            changed += 1
    return changed
