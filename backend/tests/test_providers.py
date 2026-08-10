from datetime import date
from typing import Optional

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
from app.core.config import Settings


class FakeProvider(BankProvider):
    @property
    def name(self) -> str:
        return "fake"

    @property
    def flow_type(self) -> str:
        return "widget"

    async def get_oauth_url(
        self,
        redirect_uri: str,
        state: str,
        flow_params: Optional[dict] = None,
    ) -> str:
        return "https://fake.example.com"

    async def handle_oauth_callback(self, code: str):
        pass

    async def get_accounts(self, credentials: dict):
        return []

    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: Optional[date] = None,
        payee_source: str = "auto",
    ) -> list:
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


class TestOAuthRedirectDefaults:
    """Redirect URIs fall back to FRONTEND_URL so a custom port/domain works.

    Deriving here rather than in docker-compose keeps the compose files free of
    nested `${VAR:-${OTHER:-x}}` interpolation, which podman-compose cannot
    parse (containers/podman-compose#1064).
    """

    def _settings(self, **overrides) -> Settings:
        return Settings(
            frontend_url="http://localhost:3000",
            pluggy_oauth_redirect_uri="",
            enable_banking_oauth_redirect_uri="",
        ).model_copy(update=overrides)

    def test_default_derives_from_frontend_url(self):
        from app.providers.base import default_oauth_redirect_uri

        with patch("app.core.config.get_settings", return_value=self._settings()):
            assert default_oauth_redirect_uri() == "http://localhost:3000/oauth/callback"

    def test_default_honours_custom_port_and_domain(self):
        from app.providers.base import default_oauth_redirect_uri

        settings = self._settings(frontend_url="https://securo.example.com/")
        with patch("app.core.config.get_settings", return_value=settings):
            assert default_oauth_redirect_uri() == "https://securo.example.com/oauth/callback"

    def test_enable_banking_uses_derived_default_when_unset(self):
        from app.providers.enable_banking import EnableBankingProvider

        settings = self._settings(frontend_url="http://localhost:3132")
        with patch("app.core.config.get_settings", return_value=settings), \
             patch("app.providers.enable_banking.get_settings", return_value=settings):
            assert EnableBankingProvider().redirect_uri == "http://localhost:3132/oauth/callback"

    def test_explicit_value_still_wins(self):
        from app.providers.enable_banking import EnableBankingProvider

        settings = self._settings(
            frontend_url="http://localhost:3000",
            enable_banking_oauth_redirect_uri="https://registered.example.com/cb",
        )
        with patch("app.core.config.get_settings", return_value=settings), \
             patch("app.providers.enable_banking.get_settings", return_value=settings):
            assert EnableBankingProvider().redirect_uri == "https://registered.example.com/cb"
