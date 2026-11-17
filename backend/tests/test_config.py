from pathlib import Path
from pydantic import SecretStr, ValidationError
import pytest

from app.core.config import Settings


def write(dir: Path, name: str, value: str) -> None:
    (dir / name).write_text(value)


@pytest.fixture
def secrets(tmp_path: Path):
    d = tmp_path / "secrets"
    d.mkdir()
    return d


def test_env_file_is_anchored_to_backend_dir():
    """The worker/beat must resolve the same .env as the API regardless of the
    working directory each service is launched from."""
    env_files = Settings.model_config["env_file"]
    backend_env = Path(__file__).resolve().parents[1] / ".env"
    assert isinstance(env_files, tuple)
    assert backend_env in tuple(Path(p) for p in env_files)


def test_env_file_with_unknown_keys_still_loads(tmp_path: Path, secrets: Path, monkeypatch):
    """The .env is shared with Docker Compose and the optional modules, so it
    holds keys Settings doesn't declare (COMPOSE_PROFILES, FRONTEND_PORT,
    AGENTS_*). Unknown keys must be ignored, not abort startup."""
    monkeypatch.delenv("SECRET_KEY")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SECRET_KEY=from-env-file\n"
        "AGENTS_ENABLED=true\n"
        "COMPOSE_PROFILES=agents\n"
        "FRONTEND_PORT=3132\n"
    )

    settings = Settings(_env_file=str(env_file), _secrets_dir=str(secrets))

    assert settings.secret_key.get_secret_value() == "from-env-file"


def test_agent_settings_env_file_is_anchored_to_backend_dir():
    """Agents settings must resolve the same .env as the main Settings, so a
    worker started outside backend/ doesn't silently fall back to defaults."""
    from app.agents.config import AgentSettings

    env_files = AgentSettings.model_config["env_file"]
    backend_env = Path(__file__).resolve().parents[1] / ".env"
    assert isinstance(env_files, tuple)
    assert backend_env in tuple(Path(p) for p in env_files)


def test_oidc_secret_reads_from_secrets_dir(secrets: Path):
    write(secrets, "oidc_client_secret", "file-secret")

    settings = Settings(_secrets_dir=str(secrets))
    assert settings.oidc_client_secret.get_secret_value() == "file-secret"


def test_oidc_secret_strips_whitespace(secrets: Path):
    write(secrets, "oidc_client_secret", "  stripped-value  \n\n")

    settings = Settings(_secrets_dir=str(secrets))
    assert settings.oidc_client_secret.get_secret_value() == "stripped-value"


def test_oidc_secret_inline_when_no_file(secrets: Path):
    settings = Settings(
        oidc_client_secret="inline-secret",
        _secrets_dir=str(secrets),
    )

    assert settings.oidc_client_secret.get_secret_value() == "inline-secret"


def test_oidc_secret_default_when_no_file(secrets: Path):
    settings = Settings(_secrets_dir=str(secrets))

    assert settings.oidc_client_secret.get_secret_value() == ""


def test_multiple_secrets_dirs_merge(tmp_path: Path):
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()

    secrets = {
        d1: {
            "oidc_client_secret": "from-d1",
            "oidc_provider_name": "Provider D1",
        },
        d2: {
            "oidc_client_secret": "from-d2",
            "oidc_client_id": "from-d2",
        },
    }
    for directory, values in secrets.items():
        for name, value in values.items():
            write(directory, name, value)

    expectedSecrets = {**secrets[d1], **secrets[d2]}
    settings = Settings(_secrets_dir=[str(d1), str(d2)])

    for key, expectedValue in expectedSecrets.items():
        value = getattr(settings, key)
        if isinstance(value, SecretStr):
            value = value.get_secret_value()

        assert value == expectedValue


def test_local_auth_enabled_defaults_true(secrets: Path):
    settings = Settings(_secrets_dir=str(secrets))

    assert settings.local_auth_enabled is True


def test_local_auth_can_be_disabled_when_oidc_is_enabled(secrets: Path):
    settings = Settings(
        oidc_enabled=True,
        oidc_client_id="securo",
        oidc_discovery_url="https://id.example.com/.well-known/openid-configuration",
        local_auth_enabled=False,
        _secrets_dir=str(secrets),
    )

    assert settings.local_auth_enabled is False


def test_local_auth_disabled_requires_oidc(secrets: Path):
    with pytest.raises(ValidationError, match="LOCAL_AUTH_ENABLED=false requires a complete OIDC configuration"):
        Settings(local_auth_enabled=False, _secrets_dir=str(secrets))


@pytest.mark.parametrize(
    ("missing_field", "oidc_client_id", "oidc_discovery_url"),
    [
        (
            "OIDC_CLIENT_ID",
            "",
            "https://id.example.com/.well-known/openid-configuration",
        ),
        ("OIDC_DISCOVERY_URL", "securo", ""),
    ],
)
def test_local_auth_disabled_requires_complete_oidc_configuration(
    secrets: Path,
    missing_field: str,
    oidc_client_id: str,
    oidc_discovery_url: str,
):
    with pytest.raises(ValidationError, match=missing_field):
        Settings(
            oidc_enabled=True,
            oidc_client_id=oidc_client_id,
            oidc_discovery_url=oidc_discovery_url,
            local_auth_enabled=False,
            _secrets_dir=str(secrets),
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"openid email profile groups"', "openid email profile groups"),
        ("'openid email profile groups'", "openid email profile groups"),
        ('  "openid email profile"  ', "openid email profile"),
        ("openid email profile", "openid email profile"),
        ("openid", "openid"),
    ],
)
def test_oidc_scopes_strips_surrounding_quotes(monkeypatch, raw: str, expected: str):
    """Deploy scripts that source the .env with a shell force users to quote
    values with spaces, and some panels keep those quotes in the value; the
    provider must still receive clean scope tokens (#887)."""
    monkeypatch.setenv("OIDC_SCOPES", raw)

    assert Settings(_env_file=None).oidc_scopes == expected


def test_empty_frontend_url_uses_frontend_port(monkeypatch):
    """docker-compose.yml passes FRONTEND_URL empty when unset (no nested
    default, which podman-compose < 1.6.0 mangles: #1162) plus FRONTEND_PORT."""
    monkeypatch.setenv("FRONTEND_URL", "")
    monkeypatch.setenv("FRONTEND_PORT", "3132")

    assert Settings(_env_file=None).frontend_url == "http://localhost:3132"


def test_set_frontend_url_is_used_as_is(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://securo.example.com")
    monkeypatch.setenv("FRONTEND_PORT", "3132")

    assert Settings(_env_file=None).frontend_url == "https://securo.example.com"


# The values that shipped as defaults in code, Compose, Helm and the example
# env files are public: a token signed with one of them can be forged by
# anyone (#1173). Startup must refuse them instead of running with them.
PUBLIC_SECRETS = [
    "change-me-in-production",
    "dev-secret-change-in-production",
    "dev-mcp-secret-change-in-production",
]


def test_secret_key_unset_refuses_to_start(monkeypatch, secrets: Path):
    monkeypatch.delenv("SECRET_KEY")

    with pytest.raises(ValidationError, match="SECRET_KEY is not set"):
        Settings(_env_file=None, _secrets_dir=str(secrets))


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_secret_key_refuses_to_start(monkeypatch, secrets: Path, value: str):
    monkeypatch.setenv("SECRET_KEY", value)

    with pytest.raises(ValidationError, match="SECRET_KEY is not set"):
        Settings(_env_file=None, _secrets_dir=str(secrets))


@pytest.mark.parametrize("value", PUBLIC_SECRETS)
def test_public_default_secret_key_refuses_to_start(monkeypatch, secrets: Path, value: str):
    monkeypatch.setenv("SECRET_KEY", f" {value}\n")

    with pytest.raises(ValidationError, match="SECRET_KEY is set to a public default"):
        Settings(_env_file=None, _secrets_dir=str(secrets))


def test_public_secret_key_is_refused_from_secrets_dir(monkeypatch, secrets: Path):
    monkeypatch.delenv("SECRET_KEY")
    write(secrets, "secret_key", "change-me-in-production\n")

    with pytest.raises(ValidationError, match="SECRET_KEY is set to a public default"):
        Settings(_env_file=None, _secrets_dir=str(secrets))


def test_unique_secret_key_starts(monkeypatch, secrets: Path):
    monkeypatch.setenv("SECRET_KEY", "a-unique-value-only-this-install-knows")

    settings = Settings(_env_file=None, _secrets_dir=str(secrets))

    assert settings.secret_key.get_secret_value() == "a-unique-value-only-this-install-knows"


@pytest.mark.parametrize("value", ["", *PUBLIC_SECRETS])
def test_agents_refuse_public_or_missing_mcp_secret(monkeypatch, value: str):
    from app.agents.config import AgentSettings

    monkeypatch.setenv("AGENTS_ENABLED", "true")
    monkeypatch.setenv("AGENTS_MCP_JWT_SECRET", value)

    with pytest.raises(ValidationError, match="AGENTS_MCP_JWT_SECRET"):
        AgentSettings(_env_file=None)


def test_agents_off_do_not_need_an_mcp_secret(monkeypatch):
    """Nobody pays for a feature they didn't turn on: with agents off, no
    token is minted or checked, so the secret isn't required."""
    from app.agents.config import AgentSettings

    monkeypatch.setenv("AGENTS_ENABLED", "false")
    monkeypatch.delenv("AGENTS_MCP_JWT_SECRET", raising=False)

    assert AgentSettings(_env_file=None).enabled is False


def test_mcp_server_refuses_public_secret_even_with_agents_off(monkeypatch):
    """The MCP server verifies tokens whenever it runs, so it checks the
    secret on its own, whatever AGENTS_ENABLED says."""
    from app.agents.config import AgentSettings

    monkeypatch.setenv("AGENTS_ENABLED", "false")
    monkeypatch.setenv("AGENTS_MCP_JWT_SECRET", "dev-mcp-secret-change-in-production")
    settings = AgentSettings(_env_file=None)

    with pytest.raises(ValueError, match="AGENTS_MCP_JWT_SECRET is set to a public default"):
        settings.require_mcp_jwt_secret()
