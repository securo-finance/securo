"""An install with no SECRET_KEY of its own, or one of the public defaults that
shipped with every install path (#1173), gets a unique key generated once and
kept in the database, and the credentials encrypted with the public default
move to it. Nothing has to be done by hand, and an install that set its own
key keeps it.

Most cases run on a SQLite file; the concurrency and JSON column cases need
Postgres and run when POSTGRES_TEST_URL is set (CI sets it).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.agents.services import crypto
from app.core import instance_secrets as instance
from app.core.config import Settings, resolve_secret_key
from app.plugins import vault

PUBLIC_DEFAULTS = [
    "change-me-in-production",
    "dev-secret-change-in-production",
]
OLD_COMPOSE_DEFAULT = "dev-secret-change-in-production"


def run(coro):
    return asyncio.run(coro)


async def execute(url: str, *statements: tuple[str, dict]) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for sql, params in statements:
                await conn.execute(text(sql), params)
    finally:
        await engine.dispose()


async def fetch(url: str, sql: str, params: dict | None = None) -> list:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).all())
    finally:
        await engine.dispose()


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'instance.sqlite'}"


def settings_for(url: str, secret_key: str | None, monkeypatch) -> Settings:
    if secret_key is None:
        monkeypatch.delenv("SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("SECRET_KEY", secret_key)
    return resolve_secret_key(Settings(_env_file=None, _secrets_dir=None, database_url=url))


def stored(url: str, name: str = instance.SECRET_KEY_NAME) -> list[str]:
    return [v for (v,) in run(fetch(url, "SELECT value FROM instance_secrets WHERE name = :n", {"n": name}))]


# --- Which key the instance uses ---------------------------------------------

def test_unset_secret_key_generates_one_and_keeps_it(db_url, monkeypatch):
    first = settings_for(db_url, None, monkeypatch).secret_key.get_secret_value()

    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert stored(db_url) == [first]
    # A restart, or the worker next to the API, reads the same key.
    again = settings_for(db_url, None, monkeypatch).secret_key.get_secret_value()
    assert again == first
    # Long enough to save provider credentials in Admin Settings.
    assert len(first) >= 32


@pytest.mark.parametrize("value", ["", "   ", *PUBLIC_DEFAULTS, f" {OLD_COMPOSE_DEFAULT}\n"])
def test_public_or_blank_secret_key_is_replaced(db_url, monkeypatch, value):
    settings = settings_for(db_url, value, monkeypatch)
    key = settings.secret_key.get_secret_value()

    assert key not in PUBLIC_DEFAULTS
    assert "change" not in key
    assert stored(db_url) == [key]
    # The public default is only ever tried to open old ciphertexts.
    assert set(PUBLIC_DEFAULTS) <= set(settings.secret_key_fallbacks)
    assert key not in settings.secret_key_fallbacks


def test_public_secret_key_from_secrets_dir_is_replaced(db_url, monkeypatch, tmp_path):
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "secret_key").write_text("change-me-in-production\n")
    monkeypatch.delenv("SECRET_KEY", raising=False)

    settings = resolve_secret_key(
        Settings(_env_file=None, _secrets_dir=str(secrets_dir), database_url=db_url)
    )

    assert settings.secret_key.get_secret_value() == stored(db_url)[0]


def test_own_secret_key_is_kept_and_nothing_is_written(db_url, monkeypatch):
    settings = settings_for(db_url, "a-unique-value-only-this-install-knows", monkeypatch)

    assert settings.secret_key.get_secret_value() == "a-unique-value-only-this-install-knows"
    # An install that manages its own key gets no table and no generated key.
    assert run(fetch(db_url, "SELECT name FROM sqlite_master WHERE name = 'instance_secrets'")) == []


def test_own_secret_key_set_later_still_opens_what_the_instance_key_encrypted(db_url, monkeypatch):
    generated = settings_for(db_url, None, monkeypatch).secret_key.get_secret_value()

    settings = settings_for(db_url, "a-unique-value-only-this-install-knows", monkeypatch)

    assert settings.secret_key.get_secret_value() == "a-unique-value-only-this-install-knows"
    assert settings.secret_key_fallbacks[0] == generated


def test_own_secret_key_starts_when_the_database_is_down(monkeypatch):
    settings = settings_for(
        "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none", "a-unique-value-only-this-install-knows",
        monkeypatch,
    )

    assert settings.secret_key.get_secret_value() == "a-unique-value-only-this-install-knows"


def test_missing_key_and_unreachable_database_fail_clearly(monkeypatch):
    monkeypatch.setattr(instance, "_CONNECT_DEADLINE_SECONDS", 0.0)

    with pytest.raises(RuntimeError, match="instance secret_key"):
        settings_for("postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none", None, monkeypatch)


def test_missing_key_waits_for_the_database(db_url, monkeypatch):
    """Compose and Helm start the API next to a database still coming up."""
    calls = {"n": 0}
    real = instance._fetch_or_create

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionRefusedError("database starting")
        return await real(*args, **kwargs)

    monkeypatch.setattr(instance, "_fetch_or_create", flaky)
    monkeypatch.setattr(instance.time, "sleep", lambda _s: None)

    key = settings_for(db_url, None, monkeypatch).secret_key.get_secret_value()

    assert calls["n"] == 3
    assert stored(db_url) == [key]


def test_settings_resolve_inside_a_running_event_loop(db_url, monkeypatch):
    """uvicorn imports the app, and so first reads the settings, from inside
    its event loop."""
    async def inside_loop():
        return settings_for(db_url, None, monkeypatch).secret_key.get_secret_value()

    key = asyncio.run(inside_loop())

    assert stored(db_url) == [key]


# --- Saved credentials move to the new key ------------------------------------

def seed_credentials(url: str, old_key: str) -> dict:
    agents_old = crypto.fernet_for(old_key)
    plugins_old = vault.fernet_for(old_key)
    unrelated = crypto.fernet_for("some-other-install-key")
    values = {
        "llm": agents_old.encrypt(b"sk-llm-key").decode(),
        "plugin": plugins_old.encrypt(json.dumps({"token": "plugin-token"}).encode()).decode(),
        "simplefin": agents_old.encrypt(b"https://user:pass@bridge.example/simplefin").decode(),
        "enable_banking": agents_old.encrypt(b"eb-session-id").decode(),
        "setting": agents_old.encrypt(b"pluggy-client-secret").decode(),
        "unreadable": unrelated.encrypt(b"lost-already").decode(),
    }
    run(execute(
        url,
        ("CREATE TABLE agent_llm_connections (id INTEGER PRIMARY KEY, api_key_encrypted TEXT)", {}),
        ("CREATE TABLE plugin_connections (id INTEGER PRIMARY KEY, secrets TEXT)", {}),
        ("CREATE TABLE bank_connections (id INTEGER PRIMARY KEY, credentials TEXT)", {}),
        ("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)", {}),
        ("INSERT INTO agent_llm_connections VALUES (1, :v), (2, NULL), (3, :u)",
         {"v": values["llm"], "u": values["unreadable"]}),
        ("INSERT INTO plugin_connections VALUES (1, :v)", {"v": values["plugin"]}),
        ("INSERT INTO bank_connections VALUES (1, :a), (2, :b), (3, :c), (4, NULL)", {
            "a": json.dumps({"access_url_enc": values["simplefin"], "other": "kept"}),
            "b": json.dumps({"session_id_enc": values["enable_banking"], "valid_until": "2027-01-01"}),
            # Pluggy keeps only an item id, which isn't encrypted.
            "c": json.dumps({"item_id": "item-123"}),
        }),
        ("INSERT INTO app_settings VALUES ('pluggy_client_secret', :v), ('simplefin_enabled', 'true'),"
         " ('timezone', 'Europe/Paris')", {"v": values["setting"]}),
    ))
    return values


def read_credentials(url: str) -> dict:
    return {
        "llm": dict(run(fetch(url, "SELECT id, api_key_encrypted FROM agent_llm_connections"))),
        "plugin": run(fetch(url, "SELECT secrets FROM plugin_connections"))[0][0],
        "bank": {i: (json.loads(c) if c else None) for i, c in run(fetch(url, "SELECT id, credentials FROM bank_connections"))},
        "settings": dict(run(fetch(url, "SELECT key, value FROM app_settings"))),
    }


@pytest.mark.parametrize("old_key", PUBLIC_DEFAULTS)
def test_credentials_under_a_public_default_move_to_the_new_key(db_url, monkeypatch, old_key):
    seeded = seed_credentials(db_url, old_key)

    new_key = settings_for(db_url, old_key, monkeypatch).secret_key.get_secret_value()

    after = read_credentials(db_url)
    agents_new = crypto.fernet_for(new_key)
    agents_old = crypto.fernet_for(old_key)
    assert agents_new.decrypt(after["llm"][1].encode()) == b"sk-llm-key"
    assert after["llm"][2] is None
    assert after["llm"][3] == seeded["unreadable"]  # never readable: left alone
    assert json.loads(vault.fernet_for(new_key).decrypt(after["plugin"].encode())) == {"token": "plugin-token"}
    assert agents_new.decrypt(after["bank"][1]["access_url_enc"].encode()) == (
        b"https://user:pass@bridge.example/simplefin"
    )
    assert after["bank"][1]["other"] == "kept"
    assert agents_new.decrypt(after["bank"][2]["session_id_enc"].encode()) == b"eb-session-id"
    assert after["bank"][2]["valid_until"] == "2027-01-01"
    assert after["bank"][3] == {"item_id": "item-123"}
    assert after["bank"][4] is None
    assert agents_new.decrypt(after["settings"]["pluggy_client_secret"].encode()) == b"pluggy-client-secret"
    assert after["settings"]["simplefin_enabled"] == "true"
    assert after["settings"]["timezone"] == "Europe/Paris"
    # The public key no longer opens anything that was moved.
    for token in (after["llm"][1], after["bank"][1]["access_url_enc"], after["settings"]["pluggy_client_secret"]):
        with pytest.raises(Exception):
            agents_old.decrypt(token.encode())


def test_unset_key_moves_credentials_the_old_compose_default_encrypted(db_url, monkeypatch):
    """The new Compose file drops the default, so the environment comes in
    empty, while the data was encrypted with the default the old file set."""
    seed_credentials(db_url, OLD_COMPOSE_DEFAULT)

    new_key = settings_for(db_url, "", monkeypatch).secret_key.get_secret_value()

    after = read_credentials(db_url)
    assert crypto.fernet_for(new_key).decrypt(after["llm"][1].encode()) == b"sk-llm-key"


def test_credentials_move_only_once(db_url, monkeypatch):
    seed_credentials(db_url, OLD_COMPOSE_DEFAULT)
    settings_for(db_url, OLD_COMPOSE_DEFAULT, monkeypatch)
    first = read_credentials(db_url)

    settings_for(db_url, OLD_COMPOSE_DEFAULT, monkeypatch)

    assert read_credentials(db_url) == first


def test_a_failed_move_stores_no_key(db_url, monkeypatch):
    """Key and re-encrypted values commit together, or neither does."""
    seed_credentials(db_url, OLD_COMPOSE_DEFAULT)
    before = read_credentials(db_url)

    def broken(_old):
        async def hook(conn, new_key):
            await conn.execute(text("UPDATE agent_llm_connections SET api_key_encrypted = 'x'"))
            raise ValueError("boom")
        return hook

    monkeypatch.setattr(instance, "reencrypt_with", broken)
    with pytest.raises(ValueError, match="boom"):
        settings_for(db_url, OLD_COMPOSE_DEFAULT, monkeypatch)

    assert stored(db_url) == []
    assert read_credentials(db_url) == before


# --- Reading and writing through the app's helpers ----------------------------

@pytest.fixture
def app_settings(db_url, monkeypatch):
    """Point crypto and the plugin vault at settings resolved for db_url."""
    def use(secret_key):
        settings = settings_for(db_url, secret_key, monkeypatch)
        monkeypatch.setattr(crypto, "get_settings", lambda: settings)
        monkeypatch.setattr(vault, "get_settings", lambda: settings)
        crypto._fernet.cache_clear()
        return settings
    yield use
    crypto._fernet.cache_clear()


def test_value_written_with_the_public_default_after_the_move_still_opens(app_settings):
    """A worker still on the old key during an upgrade can write one more
    value with it; the new key's processes must still read it."""
    settings = app_settings(OLD_COMPOSE_DEFAULT)
    late = crypto.fernet_for(OLD_COMPOSE_DEFAULT).encrypt(b"late-write").decode()
    late_sealed = vault.fernet_for(OLD_COMPOSE_DEFAULT).encrypt(b'{"k": "v"}').decode()

    assert crypto.decrypt(late) == "late-write"
    assert vault.unseal(late_sealed) == {"k": "v"}
    # New values use the instance key only.
    fresh = crypto.encrypt("fresh")
    assert fresh is not None
    key = settings.secret_key.get_secret_value()
    assert crypto.fernet_for(key).decrypt(fresh.encode()) == b"fresh"
    with pytest.raises(Exception):
        crypto.fernet_for(OLD_COMPOSE_DEFAULT).decrypt(fresh.encode())


def test_own_key_set_after_the_instance_key_keeps_credentials_readable(app_settings):
    generated = app_settings(None).secret_key.get_secret_value()
    saved = crypto.encrypt("bank-token")
    sealed = vault.seal({"api_key": "x"})

    app_settings("a-unique-value-only-this-install-knows")

    assert crypto.decrypt(saved) == "bank-token"
    assert vault.unseal(sealed) == {"api_key": "x"}
    assert generated != "a-unique-value-only-this-install-knows"


def test_unrelated_ciphertext_still_reads_as_missing(app_settings):
    app_settings(None)
    stranger = crypto.fernet_for("another-install").encrypt(b"x").decode()

    assert crypto.decrypt(stranger) is None
    with pytest.raises(vault.SealBroken):
        vault.unseal(vault.fernet_for("another-install").encrypt(b"{}").decode())


# --- Sessions -----------------------------------------------------------------

def test_tokens_signed_with_a_public_default_are_refused(db_url, monkeypatch):
    """Fallback keys open saved credentials only. A session token forged with
    the public default must not verify."""
    import jwt as pyjwt

    from fastapi_users.jwt import decode_jwt, generate_jwt

    import warnings

    settings = settings_for(db_url, OLD_COMPOSE_DEFAULT, monkeypatch)
    with warnings.catch_warnings():
        # The public default is shorter than pyjwt recommends; that's the point.
        warnings.simplefilter("ignore")
        forged = generate_jwt({"sub": "admin", "aud": ["fastapi-users:auth"]}, OLD_COMPOSE_DEFAULT, 60)

    with pytest.raises(pyjwt.InvalidSignatureError):
        decode_jwt(forged, settings.secret_key.get_secret_value(), ["fastapi-users:auth"])


# --- MCP signing secret ---------------------------------------------------------

@pytest.mark.parametrize("value", ["", "dev-mcp-secret-change-in-production", "change-me-in-production"])
def test_mcp_secret_is_generated_when_missing_or_public(db_url, monkeypatch, value):
    from app.agents.config import AgentSettings, _instance_mcp_jwt_secret

    _instance_mcp_jwt_secret.cache_clear()
    monkeypatch.setattr("app.core.config.get_settings", lambda: Settings(_env_file=None, database_url=db_url))
    monkeypatch.setenv("AGENTS_MCP_JWT_SECRET", value)

    secret = AgentSettings(_env_file=None).signing_secret()
    _instance_mcp_jwt_secret.cache_clear()
    # The MCP server, another process, resolves the same value.
    again = AgentSettings(_env_file=None).signing_secret()
    _instance_mcp_jwt_secret.cache_clear()

    assert re.fullmatch(r"[0-9a-f]{64}", secret)
    assert again == secret
    assert stored(db_url, instance.MCP_JWT_SECRET_NAME) == [secret]


def test_own_mcp_secret_is_used_as_is(db_url, monkeypatch):
    from app.agents.config import AgentSettings, _instance_mcp_jwt_secret

    _instance_mcp_jwt_secret.cache_clear()
    monkeypatch.setenv("AGENTS_MCP_JWT_SECRET", "my-own-mcp-signing-secret")

    assert AgentSettings(_env_file=None).signing_secret() == "my-own-mcp-signing-secret"
    _instance_mcp_jwt_secret.cache_clear()


def test_mcp_secret_and_secret_key_are_distinct(db_url, monkeypatch):
    from app.agents.config import AgentSettings, _instance_mcp_jwt_secret

    _instance_mcp_jwt_secret.cache_clear()
    monkeypatch.setattr("app.core.config.get_settings", lambda: Settings(_env_file=None, database_url=db_url))
    monkeypatch.delenv("AGENTS_MCP_JWT_SECRET", raising=False)
    key = settings_for(db_url, None, monkeypatch).secret_key.get_secret_value()

    mcp = AgentSettings(_env_file=None).signing_secret()
    _instance_mcp_jwt_secret.cache_clear()

    assert mcp != key


# --- Postgres -------------------------------------------------------------------

PG_URL = os.environ.get("POSTGRES_TEST_URL")
needs_pg = pytest.mark.skipif(not PG_URL, reason="POSTGRES_TEST_URL not set")


@pytest.fixture
def pg_url():
    assert PG_URL
    url = PG_URL if "+asyncpg" in PG_URL else PG_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    drop = [
        ("DROP TABLE IF EXISTS instance_secrets", {}),
        ("DROP TABLE IF EXISTS agent_llm_connections", {}),
        ("DROP TABLE IF EXISTS plugin_connections", {}),
        ("DROP TABLE IF EXISTS bank_connections", {}),
        ("DROP TABLE IF EXISTS app_settings", {}),
    ]
    run(execute(url, *drop))
    yield url
    run(execute(url, *drop))


@needs_pg
def test_processes_starting_together_agree_on_one_key(pg_url, monkeypatch):
    """API, worker, beat, migrate and the MCP server start at once. Exactly
    one of them generates the key and moves the credentials."""
    old = OLD_COMPOSE_DEFAULT
    run(execute(
        pg_url,
        ("CREATE TABLE agent_llm_connections (id INTEGER PRIMARY KEY, api_key_encrypted TEXT)", {}),
        ("INSERT INTO agent_llm_connections VALUES (1, :v)",
         {"v": crypto.fernet_for(old).encrypt(b"sk-llm-key").decode()}),
        # The table can already exist, e.g. when the MCP secret came first, so
        # creating it does not serialize the processes by itself.
        ("CREATE TABLE instance_secrets (name VARCHAR(64) PRIMARY KEY, value TEXT NOT NULL,"
         " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)", {}),
        ("INSERT INTO instance_secrets (name, value) VALUES ('mcp_jwt_secret', 'x')", {}),
    ))
    monkeypatch.setenv("SECRET_KEY", old)
    keys: list[str] = []
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def boot():
        try:
            start.wait()
            settings = resolve_secret_key(Settings(_env_file=None, database_url=pg_url))
            keys.append(settings.secret_key.get_secret_value())
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=boot) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(set(keys)) == 1
    assert stored(pg_url) == keys[:1]
    (token,) = run(fetch(pg_url, "SELECT api_key_encrypted FROM agent_llm_connections"))[0]
    # Moved exactly once: a second move would have failed to open it with the
    # public key and left it as is, so this alone can't tell. The key check
    # above and the single stored row can.
    assert crypto.fernet_for(keys[0]).decrypt(token.encode()) == b"sk-llm-key"


@needs_pg
def test_bank_credentials_json_column_on_postgres(pg_url, monkeypatch):
    old = OLD_COMPOSE_DEFAULT
    enc = crypto.fernet_for(old).encrypt(b"https://bridge.example/simplefin").decode()
    run(execute(
        pg_url,
        ("CREATE TABLE bank_connections (id UUID PRIMARY KEY, credentials JSON)", {}),
        ("INSERT INTO bank_connections VALUES ('00000000-0000-0000-0000-000000000001', CAST(:c AS JSON))",
         {"c": json.dumps({"access_url_enc": enc, "other": 1})}),
    ))

    key = settings_for(pg_url, old, monkeypatch).secret_key.get_secret_value()

    (creds,) = run(fetch(pg_url, "SELECT credentials FROM bank_connections"))[0]
    creds = json.loads(creds) if isinstance(creds, str) else creds
    assert crypto.fernet_for(key).decrypt(creds["access_url_enc"].encode()) == b"https://bridge.example/simplefin"
    assert creds["other"] == 1


def test_settings_secret_key_is_a_secretstr(db_url, monkeypatch):
    assert isinstance(settings_for(db_url, None, monkeypatch).secret_key, SecretStr)


# --- Entry points ---------------------------------------------------------------

@pytest.mark.parametrize("module", [
    "app.core.database",  # what alembic's env.py and every model import first
    "app.main",
    "app.worker",
    "mcp_server.main",
])
def test_entry_points_start_without_a_secret_key(tmp_path, module):
    """Settings first load while app.core.database imports, so generating the
    key and moving credentials must not need the models or services."""
    import subprocess
    import sys

    url = f"sqlite+aiosqlite:///{tmp_path / 'boot.sqlite'}"
    env = {k: v for k, v in os.environ.items() if k not in ("SECRET_KEY", "AGENTS_MCP_JWT_SECRET")}
    env.update(DATABASE_URL=url, CREDENTIALS_DIRECTORY="", AGENTS_ENABLED="true")
    done = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=120,
    )

    assert done.returncode == 0, done.stderr[-2000:]
    assert len(stored(url)) == 1
