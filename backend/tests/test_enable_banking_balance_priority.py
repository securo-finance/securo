"""Enable Banking balance selection follows a configurable BalanceStatus order."""
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.providers.enable_banking import _pick_balance


@pytest.mark.parametrize("codes,expected", [
    (["CLBD", "CLAV", "ITBD", "ITAV", "XPCD"], "ITBD"),
    (["XPCD", "ITAV", "CLAV", "CLBD"], "CLBD"),
    # Booked plus available, where available includes the credit line.
    (["ITAV", "CLBD"], "CLBD"),
    (["XPCD", "OPBD"], "OPBD"),
    (["CLAV", "ITAV", "XPCD"], "ITAV"),
    (["XPCD", "CLAV"], "CLAV"),
    (["OPAV", "XPCD"], "XPCD"),
    (["OPAV"], "OPAV"),
    (["INFO", "OTHR"], "INFO"),
])
def test_default_balance_priority_prefers_booked(codes, expected, monkeypatch):
    monkeypatch.delenv("ENABLE_BANKING_BALANCE_PRIORITY", raising=False)
    settings = Settings(_env_file=None, _secrets_dir=None)
    balances = [{"balance_type": code} for code in codes]
    with patch("app.providers.enable_banking.get_settings", return_value=settings):
        assert _pick_balance(balances) == {"balance_type": expected}


def test_balance_priority_environment_override(monkeypatch):
    monkeypatch.setenv("ENABLE_BANKING_BALANCE_PRIORITY", " itav, clbd ")
    settings = Settings(_env_file=None, _secrets_dir=None)
    assert settings.enable_banking_balance_priority == "ITAV,CLBD"
    with patch("app.providers.enable_banking.get_settings", return_value=settings):
        assert _pick_balance([{"type": "CLBD"}, {"type": "ITAV"}]) == {"type": "ITAV"}


@pytest.mark.parametrize("value", ["", "TYPO", "CLBD,", "CLBD,clbd"])
def test_invalid_balance_priority_rejected(value):
    with pytest.raises(ValidationError, match="Balance priority"):
        Settings(enable_banking_balance_priority=value, _env_file=None, _secrets_dir=None)


def test_empty_and_malformed_balances_keep_existing_fallback():
    assert _pick_balance([]) is None
    assert _pick_balance([None]) is None
    assert _pick_balance([None, {"type": "XPCD"}]) == {"type": "XPCD"}
