from pathlib import Path
from pydantic import SecretStr
import pytest

from app.core.config import Settings


def write(dir: Path, name: str, value: str) -> None:
    (dir / name).write_text(value)


@pytest.fixture
def secrets(tmp_path: Path):
    d = tmp_path / "secrets"
    d.mkdir()
    return d


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
