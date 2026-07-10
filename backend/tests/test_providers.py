import pytest
from unittest.mock import patch

from app.providers import (
    register_provider,
    get_provider,
    list_providers,
    all_known_providers,
    get_storage_provider,
    _PROVIDERS,
)
from app.providers.base import BankProvider


class FakeProvider(BankProvider):
    @property
    def name(self) -> str:
        return "fake"

    @property
    def flow_type(self) -> str:
        return "widget"

    def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        return "https://fake.example.com"

    async def handle_oauth_callback(self, code: str):
        pass

    async def get_accounts(self, credentials: dict):
        return []

    async def get_transactions(self, credentials, account_id, since, **kw):
        return []

    async def refresh_credentials(self, credentials: dict) -> dict:
        return credentials


@pytest.fixture(autouse=True)
def _clean_registry():
    """Remove the 'fake' provider after each test."""
    yield
    _PROVIDERS.pop("fake", None)


def test_register_and_get_provider():
    register_provider("fake", FakeProvider)
    provider = get_provider("fake")
    assert provider.name == "fake"
    assert provider.flow_type == "widget"


def test_get_provider_unknown():
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider("nonexistent_provider")


def test_list_providers_includes_registered():
    register_provider("fake", FakeProvider)
    result = list_providers()
    names = [p["name"] for p in result]
    assert "fake" in names


def test_all_known_providers():
    result = all_known_providers()
    assert isinstance(result, list)
    for p in result:
        assert "name" in p
        assert "configured" in p


def test_get_storage_provider_local():
    import app.providers as providers_mod

    original = providers_mod._storage_provider
    providers_mod._storage_provider = None
    try:
        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.storage_provider = "local"
            mock_settings.return_value.storage_local_path = "/tmp/test-storage"
            provider = get_storage_provider()
            assert provider.name == "local"
    finally:
        providers_mod._storage_provider = original


def test_get_storage_provider_unsupported():
    import app.providers as providers_mod

    original = providers_mod._storage_provider
    providers_mod._storage_provider = None
    try:
        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.storage_provider = "s3"
            with pytest.raises(NotImplementedError, match="s3"):
                get_storage_provider()
    finally:
        providers_mod._storage_provider = original
