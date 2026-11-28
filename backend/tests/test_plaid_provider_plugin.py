"""End-to-end tests for the reference Plaid provider plugin in `examples/plugins/plaid`.

Verifies:
1. Manifest compliance and real installation/execution on the sandboxed plugin runner.
2. Full Hosted Link sign-in (`oauth_url` -> `oauth_exchange`), Update Mode reconnect,
   and ephemeral `pending_link_token` cleanup.
3. Full bank sync pipeline integration for Transactions (`/transactions/sync` with
   pagination, mutation retry, pending->posted settlement with tip change, removed holds),
   Liabilities (credit card bills, Nelnet student loans, mortgages), and Investments
   (M1 Finance holdings, buy/sell trades, and `auto_invested_balance` deduction).
4. Graceful handling of `PRODUCT_NOT_READY`, `PRODUCTS_NOT_SUPPORTED`, `ITEM_LOGIN_REQUIRED`,
   and HTTP 429 rate limiting.
"""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_transaction import AssetTransaction
from app.models.credit_card_bill import CreditCardBill
from app.models.transaction import Transaction
from app.plugins import connections, runner, vault
from app.plugins.manifest import parse_manifest
from app.services import account_service
from tests import test_code_plugins as code_plugins
from tests.test_code_plugins import PLUGIN, REPO, V1, _apply
from tests.test_provider_plugins import _bank, _rows

catalog = code_plugins.catalog
plugin_runner = code_plugins.plugin_runner
sync_locks = code_plugins.sync_locks
states = code_plugins.states
ids = code_plugins.ids

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "plugins" / "plaid"


def _manifest() -> dict[str, Any]:
    return json.loads((EXAMPLE / "securo-plugin.json").read_text(encoding="utf-8"))


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("plaid_provider_example", EXAMPLE / "plaid_plugin.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(EXAMPLE.iterdir()):
            if path.is_file():
                tar.add(path, arcname=f"your-name-securo-plaid-abc/{path.name}")
    return buffer.getvalue()


@pytest.fixture
def plaid_catalog(catalog):
    catalog.manifests[V1] = {**_manifest(), "id": PLUGIN, "repository": REPO}
    catalog.archives[V1] = _archive()
    return catalog


class FakePlaidServer:
    """Deterministic in-memory mock of Plaid's HTTPS REST API."""

    def __init__(self, mod: ModuleType) -> None:
        self.mod = mod
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.link_token_counter = 0
        self.sync_round = 0
        self.require_reauth = False
        self.rate_limited = False
        self.product_not_ready = False
        self.unsupported_optional = False
        self.mutate_once = False
        self.liabilities_only = False

    def __call__(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint = "/" + url.split("://", 1)[1].split("/", 1)[1]
        self.calls.append((url, payload))

        if self.rate_limited:
            raise self.mod.PlaidAPIError(
                "RATE_LIMIT_EXCEEDED: slow down",
                status_code=429,
                error_type="RATE_LIMIT_EXCEEDED",
                error_code="TRANSACTIONS_LIMIT",
            )

        if endpoint == "/link/token/create":
            self.link_token_counter += 1
            tok = f"link-sandbox-{self.link_token_counter}"
            return {
                "link_token": tok,
                "hosted_link_url": f"https://hosted.plaid.com/link/{tok}",
            }

        if endpoint == "/link/token/get":
            if payload.get("link_token") == "link-sandbox-2":
                # Update Mode completed cleanly without issuing a new public_token.
                return {
                    "link_token": payload["link_token"],
                    "link_sessions": [
                        {
                            "link_session_id": "ls-update-1",
                            "finished_at": "2026-11-27T12:00:00Z",
                            "results": {"item_add_results": []},
                        }
                    ],
                }
            return {
                "link_token": payload["link_token"],
                "link_sessions": [
                    {
                        "link_session_id": "ls-init-1",
                        "finished_at": "2026-11-27T11:00:00Z",
                        "results": {
                            "item_add_results": [
                                {
                                    "public_token": "public-sandbox-xyz",
                                    "institution": {
                                        "institution_id": "ins_109508",
                                        "name": "First Platypus Bank",
                                    },
                                }
                            ]
                        },
                    }
                ],
            }

        if endpoint == "/item/public_token/exchange":
            assert payload["public_token"] == "public-sandbox-xyz"
            return {"access_token": "access-sandbox-token-1", "item_id": "item-sandbox-1"}

        if self.require_reauth:
            raise self.mod.PlaidAPIError(
                "ITEM_LOGIN_REQUIRED: login expired",
                status_code=400,
                error_type="ITEM_ERROR",
                error_code="ITEM_LOGIN_REQUIRED",
            )

        if endpoint == "/accounts/get":
            if self.liabilities_only:
                return {
                    "item": {"institution_id": "ins_nelnet"},
                    "accounts": [
                        {
                            "account_id": "acc-nelnet",
                            "persistent_account_id": "persist-nelnet",
                            "name": "Nelnet Direct Subsidized",
                            "mask": "8891",
                            "type": "loan",
                            "subtype": "student",
                            "balances": {"current": 18250.40, "iso_currency_code": "USD"},
                        }
                    ],
                }
            return {
                "item": {"institution_id": "ins_109508"},
                "accounts": [
                    {
                        "account_id": "acc-chk",
                        "persistent_account_id": "persist-chk",
                        "name": "Everyday Checking",
                        "mask": "0001",
                        "type": "depository",
                        "subtype": "checking",
                        "balances": {"current": 2500.00, "available": 2450.00, "iso_currency_code": "USD"},
                    },
                    {
                        "account_id": "acc-paypal",
                        "name": "PayPal Balance",
                        "mask": "7700",
                        "type": "depository",
                        "subtype": "paypal",
                        "balances": {"current": 140.25, "iso_currency_code": "USD"},
                    },
                    {
                        "account_id": "acc-cc",
                        "name": "Sapphire Card",
                        "mask": "4242",
                        "type": "credit",
                        "subtype": "credit card",
                        "balances": {
                            "current": 320.50,
                            "available": 4500.00,
                            "limit": 5000.00,
                            "iso_currency_code": "USD",
                        },
                    },
                    {
                        "account_id": "acc-nelnet",
                        "name": "Nelnet Student Loan",
                        "mask": "8891",
                        "type": "loan",
                        "subtype": "student",
                        "balances": {"current": 18250.40, "iso_currency_code": "USD"},
                    },
                    {
                        "account_id": "acc-mort",
                        "name": "Home Mortgage",
                        "mask": "9900",
                        "type": "loan",
                        "subtype": "mortgage",
                        "balances": {"current": 285000.00, "iso_currency_code": "USD"},
                    },
                    {
                        "account_id": "acc-m1",
                        "name": "M1 Finance Individual",
                        "mask": "3311",
                        "type": "investment",
                        "subtype": "brokerage",
                        "balances": {"current": 2750.00, "iso_currency_code": "USD"},
                    },
                ],
            }

        if endpoint == "/institutions/get_by_id":
            name = "Nelnet" if payload.get("institution_id") == "ins_nelnet" else "First Platypus Bank"
            return {"institution": {"institution_id": payload["institution_id"], "name": name}}

        if endpoint == "/transactions/sync":
            if self.product_not_ready:
                raise self.mod.PlaidAPIError(
                    "PRODUCT_NOT_READY",
                    status_code=400,
                    error_type="ITEM_ERROR",
                    error_code="PRODUCT_NOT_READY",
                )
            cursor = payload.get("cursor")
            if self.mutate_once and cursor == "page-1":
                self.mutate_once = False
                raise self.mod.PlaidAPIError(
                    "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION",
                    status_code=400,
                    error_type="TRANSACTIONS_ERROR",
                    error_code="TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION",
                )
            if not cursor:
                return {
                    "added": [
                        {
                            "transaction_id": "tx-paycheck",
                            "account_id": "acc-chk",
                            "amount": -3200.00,
                            "iso_currency_code": "USD",
                            "date": "2026-11-10",
                            "name": "ACME CORP PAYROLL PPD",
                            "merchant_name": "Acme Corp",
                            "pending": False,
                            "personal_finance_category": {
                                "primary": "INCOME",
                                "detailed": "INCOME_WAGES",
                            },
                        },
                        {
                            "transaction_id": "tx-dinner-pending",
                            "account_id": "acc-cc",
                            "amount": 40.00,
                            "iso_currency_code": "USD",
                            "date": "2026-11-15",
                            "name": "BISTRO 42 AUTH",
                            "merchant_name": "Bistro 42",
                            "pending": True,
                            "personal_finance_category": {
                                "primary": "FOOD_AND_DRINK",
                                "detailed": "FOOD_AND_DRINK_RESTAURANT",
                            },
                        },
                    ],
                    "modified": [],
                    "removed": [],
                    "next_cursor": "page-1",
                    "has_more": True,
                }
            if cursor == "page-1":
                return {
                    "added": [
                        {
                            "transaction_id": "tx-gas-hold",
                            "account_id": "acc-cc",
                            "amount": 75.00,
                            "iso_currency_code": "USD",
                            "date": "2026-11-16",
                            "name": "SHELL GAS HOLD",
                            "merchant_name": "Shell",
                            "pending": True,
                        },
                        {
                            "transaction_id": "tx-paypal-1",
                            "account_id": "acc-paypal",
                            "amount": 19.99,
                            "iso_currency_code": "USD",
                            "date": "2026-11-16",
                            "name": "PAYPAL *MERCHANT",
                            "merchant_name": "Bookshop",
                            "pending": False,
                        },
                    ],
                    "modified": [],
                    "removed": [],
                    "next_cursor": "cursor-v1",
                    "has_more": False,
                }
            if cursor == "cursor-v1":
                return {
                    "added": [
                        {
                            "transaction_id": "tx-dinner-posted",
                            "pending_transaction_id": "tx-dinner-pending",
                            "account_id": "acc-cc",
                            "amount": 48.00,
                            "iso_currency_code": "USD",
                            "date": "2026-11-17",
                            "name": "BISTRO 42 SETTLED",
                            "merchant_name": "Bistro 42",
                            "pending": False,
                            "personal_finance_category": {
                                "primary": "FOOD_AND_DRINK",
                                "detailed": "FOOD_AND_DRINK_RESTAURANT",
                            },
                        }
                    ],
                    "modified": [],
                    "removed": [
                        {"transaction_id": "tx-dinner-pending", "account_id": "acc-cc"},
                        {"transaction_id": "tx-gas-hold", "account_id": "acc-cc"},
                    ],
                    "next_cursor": "cursor-v2",
                    "has_more": False,
                }
            return {"added": [], "modified": [], "removed": [], "next_cursor": cursor, "has_more": False}

        if endpoint == "/liabilities/get":
            if self.unsupported_optional:
                raise self.mod.PlaidAPIError(
                    "PRODUCTS_NOT_SUPPORTED",
                    status_code=400,
                    error_type="ITEM_ERROR",
                    error_code="PRODUCTS_NOT_SUPPORTED",
                )
            return {
                "liabilities": {
                    "credit": [
                        {
                            "account_id": "acc-cc",
                            "minimum_payment_amount": 35.00,
                            "next_payment_due_date": "2026-12-15",
                            "last_statement_issue_date": "2026-11-18",
                            "last_statement_balance": 320.50,
                        }
                    ],
                    "student": [
                        {
                            "account_id": "acc-nelnet",
                            "origination_principal_amount": 22000.00,
                            "interest_rate_percentage": 5.05,
                            "origination_date": "2020-08-15",
                            "expected_payoff_date": "2030-08-15",
                            "next_payment_due_date": "2026-12-20",
                            "minimum_payment_amount": 233.75,
                        }
                    ],
                    "mortgage": [
                        {
                            "account_id": "acc-mort",
                            "origination_principal_amount": 320000.00,
                            "interest_rate": {"percentage": 6.125, "type": "fixed"},
                            "loan_term": "30 year",
                            "origination_date": "2023-05-01",
                            "next_payment_due_date": "2026-12-01",
                            "next_monthly_payment": 1944.36,
                        }
                    ],
                }
            }

        if endpoint == "/investments/holdings/get":
            if self.unsupported_optional:
                raise self.mod.PlaidAPIError(
                    "NO_INVESTMENT_ACCOUNTS",
                    status_code=400,
                    error_type="ITEM_ERROR",
                    error_code="NO_INVESTMENT_ACCOUNTS",
                )
            return {
                "securities": [
                    {
                        "security_id": "sec-vti",
                        "name": "Vanguard Total Stock Market ETF",
                        "ticker_symbol": "VTI",
                        "isin": "US9229087690",
                        "type": "etf",
                        "iso_currency_code": "USD",
                        "is_cash_equivalent": False,
                    },
                    {
                        "security_id": "sec-cash",
                        "name": "U S Dollar",
                        "type": "cash",
                        "iso_currency_code": "USD",
                        "is_cash_equivalent": True,
                    },
                ],
                "holdings": [
                    {
                        "account_id": "acc-m1",
                        "security_id": "sec-vti",
                        "quantity": 10,
                        "institution_price": 250.00,
                        "institution_value": 2500.00,
                        "cost_basis": 2300.00,
                        "iso_currency_code": "USD",
                    },
                    {
                        "account_id": "acc-m1",
                        "security_id": "sec-cash",
                        "quantity": 250,
                        "institution_price": 1.00,
                        "institution_value": 250.00,
                        "iso_currency_code": "USD",
                    },
                ],
            }

        if endpoint == "/investments/transactions/get":
            return {
                "total_investment_transactions": 2,
                "investment_transactions": [
                    {
                        "investment_transaction_id": "inv-buy-1",
                        "account_id": "acc-m1",
                        "security_id": "sec-vti",
                        "date": "2026-10-01",
                        "type": "buy",
                        "quantity": 6,
                        "price": 225.00,
                        "fees": 0,
                    },
                    {
                        "investment_transaction_id": "inv-buy-2",
                        "account_id": "acc-m1",
                        "security_id": "sec-vti",
                        "date": "2026-11-01",
                        "type": "buy",
                        "quantity": 4,
                        "price": 237.50,
                        "fees": 0,
                    },
                ],
            }

        raise AssertionError(f"Unexpected Plaid endpoint: {endpoint}")


def _wire_fake_plaid(monkeypatch, fake: FakePlaidServer) -> None:
    """Route runner calls to PlaidPlugin via MappingProxyType + JSON roundtrip, matching host.py."""
    instance = fake.mod.PlaidPlugin(requester=fake)

    async def fake_call(
        self,
        plugin_id: str,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        secrets: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        securo: dict[str, Any] | None = None,
        commit: str | None = None,
        timeout: int | None = None,
    ) -> Any:
        ctx = type(
            "Context",
            (),
            {
                "secrets": MappingProxyType(json.loads(json.dumps(secrets or {}))),
                "config": MappingProxyType(json.loads(json.dumps(config or {}))),
                "securo": MappingProxyType(json.loads(json.dumps(securo or {}))),
            },
        )()
        wire_params = json.loads(json.dumps(params or {}))
        try:
            result = getattr(instance, method)(wire_params, ctx)
            return json.loads(json.dumps(result, default=str))
        except Exception as exc:
            raise runner.PluginCallFailed(type(exc).__name__, str(exc)) from None

    monkeypatch.setattr(runner.RunnerClient, "call", fake_call)


def test_plaid_manifest_is_valid_provider_plugin():
    manifest = parse_manifest(_manifest())
    (integration,) = manifest.contributes.integrations
    assert manifest.has_code and integration.is_provider
    assert integration.auth == "oauth"
    assert integration.provides == ["accounts", "transactions", "bills", "holdings", "trades"]
    assert {h.host for h in manifest.network} == {"production.plaid.com", "sandbox.plaid.com"}


@pytest.mark.asyncio
async def test_plaid_installs_and_runs_in_real_runner_subprocess(
    session: AsyncSession, ids, plaid_catalog, plugin_runner, states
):
    """Installs `examples/plugins/plaid` into the real subprocess runner and
    verifies validation errors propagate cleanly without touching the network."""
    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)
    with pytest.raises(runner.PluginCallFailed, match="environment must be 'production' or 'sandbox'"):
        await connections.create_with_oauth(
            session,
            workspace_id=workspace_id,
            user_id=user_id,
            can_write=True,
            plugin_id=PLUGIN,
            integration_key="plaid",
            name="Bad Env",
            values={"client_id": "cid", "secret": "sec-1234", "environment": "evil.example"},
            lang="en",
        )


@pytest.mark.asyncio
async def test_plaid_full_hosted_link_sync_liabilities_investments_and_update_mode(
    session: AsyncSession, ids, plaid_catalog, plugin_runner, states, monkeypatch
):
    mod = _module()
    fake = FakePlaidServer(mod)
    fake.mutate_once = True
    _wire_fake_plaid(monkeypatch, fake)

    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)

    # 1. Start Hosted Link sign-in
    connection, link, state = await connections.create_with_oauth(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        can_write=True,
        plugin_id=PLUGIN,
        integration_key="plaid",
        name="Plaid All",
        values={"client_id": "cid-1", "secret": "sec-secret-99", "environment": "sandbox"},
        lang="en",
    )
    assert link == "https://hosted.plaid.com/link/link-sandbox-1"
    assert state is not None
    assert vault.unseal(connection.secrets)["pending_link_token"] == "link-sandbox-1"
    # Verify optional_products includes investments and liabilities
    create_call = next(p for u, p in fake.calls if u.endswith("/link/token/create"))
    assert create_call["products"] == ["transactions"]
    assert set(create_call["optional_products"]) == {"investments", "liabilities"}
    assert create_call["hosted_link"]["completion_redirect_uri"].endswith("/plugins/oauth/callback")

    # 2. Complete Hosted Link callback without `?code=`
    await connections.finish_oauth(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        code=None,
        state=state,
        params={"status": "success", "link_session_id": "ls-init-1"},
    )
    unsealed = vault.unseal(connection.secrets)
    assert unsealed["access_token"] == "access-sandbox-token-1"
    assert unsealed["item_id"] == "item-sandbox-1"
    assert "pending_link_token" not in unsealed
    assert connection.state["institution_name"] == "First Platypus Bank"

    # 3. Initial sync (exercises mutation retry, multi-page transactions, liabilities, investments)
    await connections.sync(session, connection)
    assert (connection.status, connection.last_error) == ("active", None)
    assert connection.state["transactions_cursor"] == "cursor-v1"

    bank = await _bank(session, connection)
    bank_id = bank.id
    assert bank.status == "active"
    assert bank.last_sync_at is not None

    accounts = {a.external_id: a for a in await _rows(session, Account, connection_id=bank_id)}
    assert set(accounts) == {"acc-chk", "acc-paypal", "acc-cc", "acc-nelnet", "acc-mort", "acc-m1"}

    # Checking & PayPal wallet
    chk = accounts["acc-chk"]
    assert (chk.type, chk.balance, chk.masked_number, chk.stable_external_id) == (
        "checking",
        Decimal("2500.00"),
        "0001",
        "persist-chk",
    )
    assert accounts["acc-paypal"].type == "wallet"
    assert accounts["acc-paypal"].balance == Decimal("140.25")

    # Checking paycheck sign inverted from Plaid -3200 -> credit 3200
    (paycheck,) = [
        t for t in await _rows(session, Transaction, account_id=chk.id) if t.source == "sync"
    ]
    assert (paycheck.type, paycheck.amount, paycheck.payee) == ("credit", Decimal("3200.00"), "Acme Corp")
    assert paycheck.notes == "ACME CORP PAYROLL PPD"

    # Credit card: balance uses balances.current (320.50), NOT limit - available (500.00)!
    cc = accounts["acc-cc"]
    assert (cc.type, cc.balance, cc.credit_limit, cc.payment_due_day, cc.statement_close_day, cc.minimum_payment) == (
        "credit_card",
        Decimal("320.50"),
        Decimal("5000.00"),
        15,
        18,
        Decimal("35.00"),
    )
    (bill,) = await _rows(session, CreditCardBill, account_id=cc.id)
    assert bill.total_amount == Decimal("320.50")
    assert bill.due_date.isoformat() == "2026-12-15"

    # Nelnet student loan & mortgage terms (only Plaid-established schedule fields)
    nelnet = accounts["acc-nelnet"]
    assert (nelnet.type, nelnet.balance, nelnet.loan_kind) == ("loan", Decimal("18250.40"), "student")
    assert nelnet.loan_original_amount == Decimal("22000.00")
    assert nelnet.loan_interest_rate == Decimal("5.05")
    assert nelnet.loan_rate_period == "yearly_nominal"
    assert nelnet.loan_term_months is None
    assert nelnet.loan_first_payment_date is None
    assert nelnet.payment_due_day == 20
    assert nelnet.minimum_payment == Decimal("233.75")

    mort = accounts["acc-mort"]
    assert (mort.type, mort.loan_kind, mort.loan_term_months, mort.loan_amortization) == (
        "loan",
        "mortgage",
        360,
        "price",
    )
    assert mort.loan_first_payment_date is None
    assert mort.loan_interest_rate == Decimal("6.125")

    # M1 Finance investment account: 2750 total - 2500 VTI holding = 250 cash residual!
    m1 = accounts["acc-m1"]
    assert m1.type == "investment"
    assert m1.balance == Decimal("250.00")
    (vti,) = await _rows(session, Asset, connection_id=bank.id)
    assert vti.ticker == "VTI"
    assert vti.isin == "US9229087690"
    assert vti.units == Decimal("10")
    trades = await _rows(session, AssetTransaction, asset_id=vti.id)
    assert sorted(t.external_id for t in trades) == ["inv-buy-1", "inv-buy-2"]

    # 4. Second incremental sync: dinner settles with tip (40 -> 48) and gas hold is removed
    cc_id = cc.id
    cc_txns_before = {
        t.external_id: t for t in await _rows(session, Transaction, account_id=cc_id) if t.source == "sync"
    }
    assert set(cc_txns_before) == {"tx-dinner-pending", "tx-gas-hold"}
    dinner_id = cc_txns_before["tx-dinner-pending"].id

    await connections.sync(session, connection)
    assert connection.state["transactions_cursor"] == "cursor-v2"
    cc_txns_after = [
        t for t in await _rows(session, Transaction, account_id=cc_id) if t.source == "sync"
    ]
    assert len(cc_txns_after) == 1
    settled_dinner = cc_txns_after[0]
    assert settled_dinner.id == dinner_id
    assert settled_dinner.external_id == "tx-dinner-posted"
    assert settled_dinner.status == "posted"
    assert settled_dinner.amount == Decimal("48.00")

    # 5. ITEM_LOGIN_REQUIRED -> NeedsAuth -> Hosted Link Update Mode reconnect
    fake.require_reauth = True
    await connections.sync(session, connection)
    assert connection.status == "needs_auth"
    assert (await _bank(session, connection)).status == "expired"

    reauth_url, reauth_state = await connections.start_oauth(session, connection, user_id=user_id)
    assert reauth_url == "https://hosted.plaid.com/link/link-sandbox-2"
    last_create = [p for u, p in fake.calls if u.endswith("/link/token/create")][-1]
    assert last_create["access_token"] == "access-sandbox-token-1"
    assert "products" not in last_create

    fake.require_reauth = False
    await connections.finish_oauth(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        code=None,
        state=reauth_state,
        params={"status": "success", "link_session_id": "ls-update-1"},
    )
    await connections.sync(session, connection)
    assert connection.status == "active"
    assert len(await _rows(session, Account, connection_id=bank_id)) == 6


@pytest.mark.asyncio
async def test_plaid_graceful_fallbacks_for_loan_only_product_not_ready_and_rate_limit(
    session: AsyncSession, ids, plaid_catalog, plugin_runner, states, monkeypatch
):
    mod = _module()
    fake = FakePlaidServer(mod)
    fake.liabilities_only = True
    _wire_fake_plaid(monkeypatch, fake)

    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)

    # Loan-only Nelnet connection with products="liabilities"
    connection, _, state = await connections.create_with_oauth(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        can_write=True,
        plugin_id=PLUGIN,
        integration_key="plaid",
        name="Nelnet",
        values={
            "client_id": "cid-1",
            "secret": "sec-secret-99",
            "environment": "sandbox",
            "products": "liabilities",
            "enable_investments": "false",
        },
        lang="en",
    )
    create_call = next(p for u, p in fake.calls if u.endswith("/link/token/create"))
    assert create_call["products"] == ["liabilities"]
    assert create_call["optional_products"] == ["transactions"]

    assert state is not None
    await connections.finish_oauth(
        session, workspace_id=workspace_id, user_id=user_id, code=None, state=state, params={"status": "success"}
    )
    await connections.sync(session, connection)
    assert connection.status == "active"
    bank = await _bank(session, connection)
    assert bank.last_sync_at is not None
    (nelnet,) = await _rows(session, Account, connection_id=bank.id)
    assert nelnet.type == "loan" and nelnet.loan_kind == "student"

    # Rate limit keeps connection active with 0 failures
    fake.rate_limited = True
    await connections.sync(session, connection)
    assert connection.status == "active" and connection.failures == 0
    assert (connection.last_error or "").startswith("RateLimited")

    # Optional products returning PRODUCTS_NOT_SUPPORTED do not fail a banking sync
    fake.rate_limited = False
    fake.liabilities_only = False
    fake.unsupported_optional = True
    await connections.sync(session, connection)
    assert connection.status == "active" and connection.last_error is None


def test_plaid_executes_via_host_subprocess_with_mapping_proxy_and_json_roundtrip():
    """Executes `examples/plugins/plaid` directly through `plugin_runner/host.py`
    with `python -I -S` to verify stdlib-only isolation, MappingProxyType context,
    and JSON stdin/stdout error/result serialization."""
    host_py = Path(__file__).resolve().parents[1] / "plugin_runner" / "host.py"

    def run_host(payload: dict[str, Any]) -> dict[str, Any]:
        proc = subprocess.run(
            [sys.executable, "-I", "-S", str(host_py), str(EXAMPLE), "plaid_plugin:plugin"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
            timeout=15,
        )
        return json.loads(proc.stdout)

    missing_token = run_host(
        {
            "method": "sync",
            "params": {"state": {}},
            "secrets": {"secret": "sec-1"},
            "config": {"client_id": "cid-1", "environment": "sandbox"},
            "securo": {},
        }
    )
    assert missing_token["ok"] is False
    assert missing_token["error"]["type"] == "NeedsAuth"

    exited = run_host(
        {
            "method": "oauth_exchange",
            "params": {"params": {"status": "exited"}},
            "secrets": {"access_token": "acc-1", "pending_link_token": "link-1"},
            "config": {"client_id": "cid-1", "environment": "sandbox"},
            "securo": {},
        }
    )
    assert exited["ok"] is False
    assert exited["error"]["type"] == "RuntimeError"
    assert "closed before finishing" in exited["error"]["message"]


def test_plaid_hosted_link_update_mode_exit_and_legacy_fallbacks():
    """Voluntary Hosted Link exit in Update Mode (exit.error is None) and on_exit
    must fail instead of succeeding; multi-session & on_success fallback work."""
    mod = _module()
    ctx = type(
        "Context",
        (),
        {
            "secrets": MappingProxyType(
                {"secret": "sec-1", "access_token": "access-existing", "pending_link_token": "link-upd"}
            ),
            "config": MappingProxyType({"client_id": "cid-1", "environment": "sandbox"}),
            "securo": MappingProxyType({}),
        },
    )()

    # 1. Voluntary Update Mode exit: finished_at is set and exit.error is None.
    def exit_no_error(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert url.endswith("/link/token/get")
        return {
            "link_token": "link-upd",
            "link_sessions": [
                {
                    "link_session_id": "ls-exit",
                    "finished_at": "2026-11-27T12:00:00Z",
                    "exit": {"error": None, "metadata": {"status": "requires_credentials"}},
                    "results": {"item_add_results": []},
                }
            ],
        }

    plugin = mod.PlaidPlugin(requester=exit_no_error)
    with pytest.raises(RuntimeError, match="closed before finishing"):
        plugin.oauth_exchange({"params": {}}, ctx)

    # 2. Deprecated on_exit with error=None must also reject Update Mode.
    def legacy_on_exit(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "link_token": "link-upd",
            "link_sessions": [
                {
                    "link_session_id": "ls-old-exit",
                    "finished_at": "2026-11-27T12:00:00Z",
                    "on_exit": {"error": None, "metadata": {"status": "requires_account_selection"}},
                }
            ],
        }

    with pytest.raises(RuntimeError, match="closed before finishing"):
        mod.PlaidPlugin(requester=legacy_on_exit).oauth_exchange({"params": {}}, ctx)

    # 3. Multi-session: earlier exit followed by finished success in Update Mode succeeds;
    #    earlier finished session followed by trailing exited session rejects.
    def multi_success(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "link_token": "link-upd",
            "link_sessions": [
                {
                    "link_session_id": "ls-1",
                    "finished_at": "2026-11-27T11:55:00Z",
                    "exit": {"error": None, "metadata": {}},
                },
                {
                    "link_session_id": "ls-2",
                    "finished_at": "2026-11-27T12:00:00Z",
                    "results": {"item_add_results": []},
                },
            ],
        }

    res = mod.PlaidPlugin(requester=multi_success).oauth_exchange({"params": {}}, ctx)
    assert res["secrets"] == {"access_token": "access-existing", "pending_link_token": None}

    def multi_trailing_exit(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "link_token": "link-upd",
            "link_sessions": [
                {"link_session_id": "ls-1", "finished_at": "2026-11-27T11:55:00Z", "results": {"item_add_results": []}},
                {"link_session_id": "ls-2", "finished_at": "2026-11-27T12:00:00Z", "exit": {"error": None, "metadata": {}}},
            ],
        }

    with pytest.raises(RuntimeError, match="closed before finishing"):
        mod.PlaidPlugin(requester=multi_trailing_exit).oauth_exchange({"params": {}}, ctx)

    # 4. Deprecated on_success.public_token fallback when results.item_add_results is empty.
    init_ctx = type(
        "Context",
        (),
        {
            "secrets": MappingProxyType({"secret": "sec-1", "pending_link_token": "link-new"}),
            "config": MappingProxyType({"client_id": "cid-1", "environment": "sandbox"}),
            "securo": MappingProxyType({}),
        },
    )()

    def legacy_on_success(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if url.endswith("/link/token/get"):
            return {
                "link_token": "link-new",
                "link_sessions": [
                    {
                        "link_session_id": "ls-legacy",
                        "finished_at": "2026-11-27T12:00:00Z",
                        "results": {"item_add_results": []},
                        "on_success": {
                            "public_token": "public-legacy-123",
                            "metadata": {"institution": {"institution_id": "ins_1", "name": "Legacy Bank"}},
                        },
                    }
                ],
            }
        assert url.endswith("/item/public_token/exchange")
        assert payload["public_token"] == "public-legacy-123"
        return {"access_token": "acc-legacy", "item_id": "item-legacy"}

    exchanged = mod.PlaidPlugin(requester=legacy_on_success).oauth_exchange({"params": {}}, init_ctx)
    assert exchanged["secrets"]["access_token"] == "acc-legacy"
    assert exchanged["state"]["institution_name"] == "Legacy Bank"


@pytest.mark.parametrize(
    "error_code",
    [
        "ITEM_LOGIN_REQUIRED",
        "ITEM_LOCKED",
        "INVALID_CREDENTIALS",
        "USER_SETUP_REQUIRED",
        "PASSWORD_RESET_REQUIRED",
        "MFA_NOT_SUPPORTED",
        "INSUFFICIENT_CREDENTIALS",
        "ADDITIONAL_CONSENT_REQUIRED",
    ],
)
def test_plaid_auth_error_codes_raise_needs_auth_on_primary_calls(error_code: str):
    mod = _module()
    ctx = type(
        "Context",
        (),
        {
            "secrets": MappingProxyType({"secret": "sec-1", "access_token": "acc-1"}),
            "config": MappingProxyType({"client_id": "cid-1", "environment": "sandbox"}),
            "securo": MappingProxyType({}),
        },
    )()

    def fail_accounts(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise mod.PlaidAPIError(
            f"{error_code}: action needed",
            status_code=400,
            error_type="ITEM_ERROR",
            error_code=error_code,
        )

    with pytest.raises(mod.NeedsAuth):
        mod.PlaidPlugin(requester=fail_accounts).sync({"state": {}}, ctx)


def test_plaid_sync_edge_cases_not_ready_unsupported_loan_txns_cursor_guard_and_liabilities():
    mod = _module()
    ctx = type(
        "Context",
        (),
        {
            "secrets": MappingProxyType({"secret": "sec-1", "access_token": "acc-1"}),
            "config": MappingProxyType({"client_id": "cid-1", "environment": "sandbox"}),
            "securo": MappingProxyType({}),
        },
    )()

    # 1. 200 OK with transactions_update_status == "NOT_READY" & empty next_cursor returns data=None.
    def sync_200_not_ready(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if url.endswith("/accounts/get"):
            return {
                "item": {},
                "accounts": [
                    {
                        "account_id": "acc-1",
                        "name": "Checking",
                        "type": "depository",
                        "subtype": "checking",
                        "balances": {"current": 100.0, "iso_currency_code": "USD"},
                    }
                ],
            }
        if url.endswith("/transactions/sync"):
            return {
                "transactions_update_status": "NOT_READY",
                "accounts": [],
                "added": [],
                "modified": [],
                "removed": [],
                "next_cursor": "",
                "has_more": False,
            }
        raise AssertionError(url)

    not_ready_res = mod.PlaidPlugin(requester=sync_200_not_ready).sync({"state": {}}, ctx)
    assert not_ready_res["data"] is None

    # 2. ADDITIONAL_CONSENT_REQUIRED on optional liabilities/investments + PRODUCTS_NOT_SUPPORTED on
    #    /transactions/sync still imports accounts & marks incremental transactions=[] (not incomplete).
    def sync_unsupported_txns_and_unconsented_optional(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if url.endswith("/accounts/get"):
            return {
                "item": {},
                "accounts": [
                    {
                        "account_id": "acc-chk",
                        "name": "Checking",
                        "type": "depository",
                        "subtype": "checking",
                        "balances": {"current": 500.0, "iso_currency_code": "USD"},
                    },
                    {
                        "account_id": "acc-brk",
                        "name": "Legacy Brokerage",
                        "type": "brokerage",
                        "subtype": "brokerage",
                        "balances": {"current": 1200.0, "iso_currency_code": "USD"},
                    },
                ],
            }
        if url.endswith("/transactions/sync"):
            raise mod.PlaidAPIError(
                "PRODUCTS_NOT_SUPPORTED",
                status_code=400,
                error_type="ITEM_ERROR",
                error_code="PRODUCTS_NOT_SUPPORTED",
            )
        if url.endswith("/investments/holdings/get"):
            raise mod.PlaidAPIError(
                "ADDITIONAL_CONSENT_REQUIRED",
                status_code=400,
                error_type="INVALID_INPUT",
                error_code="ADDITIONAL_CONSENT_REQUIRED",
            )
        raise AssertionError(url)

    unsupported_res = mod.PlaidPlugin(requester=sync_unsupported_txns_and_unconsented_optional).sync(
        {"state": {}}, ctx
    )
    accts = {a["external_id"]: a for a in unsupported_res["data"]["accounts"]}
    assert accts["acc-chk"]["transactions"] == []
    assert accts["acc-chk"]["incremental"] is True
    assert accts["acc-brk"]["type"] == "investment"

    # 3. has_more=True with unchanged or empty next_cursor raises instead of infinite loop.
    def non_advancing_cursor(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if url.endswith("/accounts/get"):
            return {
                "item": {},
                "accounts": [
                    {
                        "account_id": "acc-1",
                        "name": "Checking",
                        "type": "depository",
                        "subtype": "checking",
                        "balances": {"current": 100.0, "iso_currency_code": "USD"},
                    }
                ],
            }
        return {
            "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE",
            "added": [],
            "modified": [],
            "removed": [],
            "next_cursor": "same-cur",
            "has_more": True,
        }

    with pytest.raises(RuntimeError, match="without advancing next_cursor"):
        mod.PlaidPlugin(requester=non_advancing_cursor).sync(
            {"state": {"transactions_cursor": "same-cur"}}, ctx
        )

    # 4. Loan account /transactions/sync + liabilities.loan + line_of_credit + M1 holdings unofficial currencies.
    def rich_sync(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if url.endswith("/accounts/get"):
            return {
                "item": {},
                "accounts": [
                    {
                        "account_id": "acc-auto",
                        "name": "Auto Loan",
                        "type": "loan",
                        "subtype": "auto",
                        "balances": {"current": 12000.0, "iso_currency_code": "USD"},
                    },
                    {
                        "account_id": "acc-stu-idr",
                        "name": "IDR Student Loan",
                        "type": "loan",
                        "subtype": "student",
                        "balances": {"current": 9000.0, "iso_currency_code": "USD"},
                    },
                    {
                        "account_id": "acc-loc",
                        "name": "Personal Line",
                        "type": "credit",
                        "subtype": "line of credit",
                        "balances": {"current": 800.0, "limit": 5000.0, "iso_currency_code": "USD"},
                    },
                    {
                        "account_id": "acc-m1",
                        "name": "M1 Crypto & Stock",
                        "type": "brokerage",
                        "subtype": "brokerage",
                        "balances": {"current": 3000.0, "iso_currency_code": "USD"},
                    },
                ],
            }
        if url.endswith("/transactions/sync"):
            return {
                "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE",
                "added": [
                    {
                        "transaction_id": "tx-loan-pmt",
                        "account_id": "acc-auto",
                        "amount": -350.0,
                        "iso_currency_code": "USD",
                        "date": "2026-11-10",
                        "name": "AUTO LOAN PAYMENT",
                        "pending": False,
                    }
                ],
                "modified": [],
                "removed": [],
                "next_cursor": "cur-loan-1",
                "has_more": False,
            }
        if url.endswith("/liabilities/get"):
            return {
                "liabilities": {
                    "credit": [],
                    "mortgage": [],
                    "student": [
                        {
                            "account_id": "acc-stu-idr",
                            "origination_principal_amount": 10000.0,
                            "interest_rate_percentage": 4.5,
                            "origination_date": "2022-01-01",
                            "expected_payoff_date": "2032-01-01",
                            "repayment_plan": {"type": "income-based repayment", "description": "IBR"},
                        }
                    ],
                    "loan": [
                        {
                            "account_id": "acc-auto",
                            "origination_principal_amount": 18000.0,
                            "interest_rate": {"percentage": 5.25, "type": "fixed"},
                            "origination_date": "2024-06-01",
                            "loan_term_months": 60,
                            "payment_frequency": "monthly",
                            "next_payment_due_date": "2026-12-05",
                            "next_payment_amount": 341.75,
                        }
                    ],
                    "line_of_credit": [
                        {
                            "account_id": "acc-loc",
                            "credit_limit": 5000.0,
                            "minimum_payment_amount": 25.0,
                            "next_payment_due_date": "2026-12-12",
                            "last_statement_issue_date": "2026-11-12",
                            "last_statement_balance": 800.0,
                        }
                    ],
                }
            }
        if url.endswith("/investments/holdings/get"):
            return {
                "securities": [
                    {"security_id": "sec-btc", "name": "Bitcoin", "ticker_symbol": "BTC", "type": "cryptocurrency"},
                    {"security_id": "sec-usdt", "name": "Tether", "ticker_symbol": "USDT", "type": "cryptocurrency"},
                ],
                "holdings": [
                    {
                        "account_id": "acc-m1",
                        "security_id": "sec-btc",
                        "quantity": 0.05,
                        "institution_price": 60000.0,
                        "institution_value": 3000.0,
                        "unofficial_currency_code": "BTC",
                    },
                    {
                        "account_id": "acc-m1",
                        "security_id": "sec-usdt",
                        "quantity": 100.0,
                        "institution_price": 1.0,
                        "institution_value": 100.0,
                        "unofficial_currency_code": "USDT",
                    },
                ],
            }
        if url.endswith("/investments/transactions/get"):
            return {"total_investment_transactions": 0, "investment_transactions": []}
        raise AssertionError(url)

    rich_res = mod.PlaidPlugin(requester=rich_sync).sync({"state": {}}, ctx)
    rich_accts = {a["external_id"]: a for a in rich_res["data"]["accounts"]}
    assert [t["external_id"] for t in rich_accts["acc-auto"]["transactions"]] == ["tx-loan-pmt"]
    assert rich_accts["acc-auto"]["loan_term_months"] == 60
    assert rich_accts["acc-auto"]["loan_amortization"] == "price"
    assert rich_accts["acc-auto"]["minimum_payment"] == "341.75"
    assert "loan_amortization" not in rich_accts["acc-stu-idr"]
    assert rich_accts["acc-loc"]["bills"][0]["total_amount"] == "800.00"
    # 3-char unofficial_currency_code BTC kept, 4-char USDT skipped without crashing.
    assert [(h["ticker"], h["currency"]) for h in rich_res["data"]["holdings"]] == [("BTC", "BTC")]


@pytest.mark.asyncio
async def test_nelnet_student_loan_computes_loan_schedule(
    session: AsyncSession, ids, plaid_catalog, plugin_runner, states, monkeypatch
):
    """Nelnet student loan sets Plaid-established fields without fabricating
    first_payment_date/term from origination/deferment, and once the user fills
    those schedule inputs in, subsequent Plaid syncs preserve them."""
    from datetime import date

    mod = _module()
    fake = FakePlaidServer(mod)
    fake.liabilities_only = True
    _wire_fake_plaid(monkeypatch, fake)

    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)

    connection, _, state = await connections.create_with_oauth(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        can_write=True,
        plugin_id=PLUGIN,
        integration_key="plaid",
        name="Nelnet Schedule",
        values={
            "client_id": "cid-1",
            "secret": "sec-secret-99",
            "environment": "sandbox",
            "products": "liabilities",
            "enable_investments": "false",
        },
        lang="en",
    )
    assert state is not None
    await connections.finish_oauth(
        session, workspace_id=workspace_id, user_id=user_id, code=None, state=state, params={"status": "success"}
    )
    await connections.sync(session, connection)
    bank = await _bank(session, connection)
    bank_id = bank.id
    (nelnet,) = await _rows(session, Account, connection_id=bank_id)
    nelnet_id = nelnet.id
    assert nelnet.loan_amortization == "price"
    assert nelnet.loan_term_months is None
    assert nelnet.loan_first_payment_date is None

    # Before user supplies repayment start/term, schedule reports the exact missing fields.
    schedule_before = await account_service.get_loan_schedule(session, nelnet_id, workspace_id)
    assert schedule_before is not None and schedule_before["configured"] is False
    assert set(schedule_before["missing"]) == {"loan_term_months", "loan_first_payment_date"}

    # User sets their actual post-deferment start date and repayment term; sync preserves them.
    nelnet.loan_first_payment_date = date(2024, 1, 20)
    nelnet.loan_term_months = 120
    await session.commit()

    await connections.sync(session, connection)
    (nelnet_after,) = await _rows(session, Account, connection_id=bank_id)
    assert nelnet_after.loan_first_payment_date == date(2024, 1, 20)
    assert nelnet_after.loan_term_months == 120
    schedule_after = await account_service.get_loan_schedule(session, nelnet_id, workspace_id)
    assert schedule_after is not None and schedule_after["configured"] is True


def test_plaid_nonascii_unofficial_currency_falls_back_without_payload_error():
    """A non-ASCII 3-char unofficial_currency_code (e.g. 'üsd') must be omitted
    from the transaction record so it falls back to account currency instead of
    failing contract ^[A-Z]{3}$ validation, while valid ASCII codes (e.g. 'BTC') stay."""
    from app.plugins.contract import parse_payload

    mod = _module()
    ctx = type(
        "Context",
        (),
        {
            "secrets": MappingProxyType({"secret": "sec-1", "access_token": "acc-1"}),
            "config": MappingProxyType({"client_id": "cid-1", "environment": "sandbox"}),
            "securo": MappingProxyType({}),
        },
    )()

    def sync_nonascii_currency(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if url.endswith("/accounts/get"):
            return {
                "item": {},
                "accounts": [
                    {
                        "account_id": "acc-1",
                        "name": "Checking",
                        "type": "depository",
                        "subtype": "checking",
                        "balances": {"current": 100.0, "iso_currency_code": "USD"},
                    }
                ],
            }
        if url.endswith("/transactions/sync"):
            return {
                "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE",
                "added": [
                    {
                        "transaction_id": "tx-nonascii",
                        "account_id": "acc-1",
                        "amount": 15.0,
                        "iso_currency_code": None,
                        "unofficial_currency_code": "üsd",
                        "date": "2026-11-15",
                        "name": "Weird Currency",
                        "pending": False,
                    },
                    {
                        "transaction_id": "tx-btc",
                        "account_id": "acc-1",
                        "amount": 1.5,
                        "iso_currency_code": None,
                        "unofficial_currency_code": "btc",
                        "date": "2026-11-15",
                        "name": "Bitcoin Spend",
                        "pending": False,
                    },
                ],
                "modified": [],
                "removed": [],
                "next_cursor": "cur-1",
                "has_more": False,
            }
        if url.endswith("/liabilities/get") or url.endswith("/investments/holdings/get"):
            raise mod.PlaidAPIError(
                "PRODUCTS_NOT_SUPPORTED",
                status_code=400,
                error_type="ITEM_ERROR",
                error_code="PRODUCTS_NOT_SUPPORTED",
            )
        raise AssertionError(url)

    res = mod.PlaidPlugin(requester=sync_nonascii_currency).sync({"state": {}}, ctx)
    parsed = parse_payload(res["data"], ["accounts", "transactions"])
    assert parsed is not None and parsed.accounts is not None
    txns = {t.external_id: t for t in (parsed.accounts[0].transactions or [])}
    assert txns["tx-nonascii"].currency is None
    assert txns["tx-btc"].currency == "BTC"


def test_plaid_oauth_exchange_sorts_reverse_chronological_link_sessions():
    """If Plaid returns link_sessions out of chronological order, oauth_exchange
    must select the session with the latest finished_at (tie-broken by index)."""
    mod = _module()
    ctx = type(
        "Context",
        (),
        {
            "secrets": MappingProxyType({"secret": "sec-1", "pending_link_token": "link-rev"}),
            "config": MappingProxyType({"client_id": "cid-1", "environment": "sandbox"}),
            "securo": MappingProxyType({}),
        },
    )()

    # 1. Newer exited session appears BEFORE older succeeded session.
    def rev_newer_exit(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert url.endswith("/link/token/get")
        return {
            "link_token": "link-rev",
            "link_sessions": [
                {
                    "link_session_id": "ls-newer-exit",
                    "finished_at": "2026-11-27T12:05:00Z",
                    "exit": {"error": None, "metadata": {"status": "requires_credentials"}},
                },
                {
                    "link_session_id": "ls-older-success",
                    "finished_at": "2026-11-27T12:00:00Z",
                    "results": {
                        "item_add_results": [
                            {
                                "public_token": "public-older",
                                "institution": {"institution_id": "ins_old", "name": "Old Bank"},
                            }
                        ]
                    },
                },
            ],
        }

    with pytest.raises(RuntimeError, match="closed before finishing"):
        mod.PlaidPlugin(requester=rev_newer_exit).oauth_exchange({"params": {}}, ctx)

    # 2. Newer succeeded session appears BEFORE older exited session.
    def rev_newer_success(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if url.endswith("/link/token/get"):
            return {
                "link_token": "link-rev",
                "link_sessions": [
                    {
                        "link_session_id": "ls-newer-success",
                        "finished_at": "2026-11-27T12:05:00Z",
                        "results": {
                            "item_add_results": [
                                {
                                    "public_token": "public-newer",
                                    "institution": {"institution_id": "ins_new", "name": "New Bank"},
                                }
                            ]
                        },
                    },
                    {
                        "link_session_id": "ls-older-exit",
                        "finished_at": "2026-11-27T12:00:00Z",
                        "exit": {"error": None, "metadata": {}},
                    },
                ],
            }
        assert url.endswith("/item/public_token/exchange")
        assert payload["public_token"] == "public-newer"
        return {"access_token": "acc-newer", "item_id": "item-newer"}

    exchanged = mod.PlaidPlugin(requester=rev_newer_success).oauth_exchange({"params": {}}, ctx)
    assert exchanged["secrets"]["access_token"] == "acc-newer"
    assert exchanged["state"]["institution_name"] == "New Bank"


def test_plaid_rejects_primary_product_when_opted_out_before_calling_plaid():
    """Conflicting products + enable_*=false must be rejected before calling
    /link/token/create so Plaid is never initialized for an opted-out product."""
    mod = _module()
    calls: list[tuple[str, dict[str, Any]]] = []

    def recorder(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((url, payload))
        return {"hosted_link_url": "https://hosted.plaid.com/link/ok", "link_token": "link-ok"}

    def _ctx(config: dict[str, Any]):
        return type(
            "Context",
            (),
            {
                "secrets": MappingProxyType({"secret": "sec-1"}),
                "config": MappingProxyType({"client_id": "cid-1", "environment": "sandbox", **config}),
                "securo": MappingProxyType({"connection_id": "c-1"}),
            },
        )()

    with pytest.raises(ValueError, match="enable_investments is false"):
        mod.PlaidPlugin(requester=recorder).oauth_url(
            {"redirect_uri": "https://securo.example/plugins/oauth/callback"},
            _ctx({"products": "investments", "enable_investments": "false"}),
        )
    with pytest.raises(ValueError, match="enable_liabilities is false"):
        mod.PlaidPlugin(requester=recorder).oauth_url(
            {"redirect_uri": "https://securo.example/plugins/oauth/callback"},
            _ctx({"products": "liabilities", "enable_liabilities": "false"}),
        )
    assert calls == []

    # Allowing investments as a primary product when enabled still works.
    res = mod.PlaidPlugin(requester=recorder).oauth_url(
        {"redirect_uri": "https://securo.example/plugins/oauth/callback"},
        _ctx({"products": "investments", "enable_investments": "true"}),
    )
    assert res["url"] == "https://hosted.plaid.com/link/ok"
    assert calls[0][1]["products"] == ["investments"]


def test_plaid_incomplete_holdings_snapshot_omits_holdings_instead_of_archiving(monkeypatch):
    """A holdings response with a null institution_value or exceeding the cap
    must omit `holdings` from data so Securo does not archive live positions,
    while a confirmed empty portfolio still sends `holdings: []`."""
    mod = _module()
    ctx = type(
        "Context",
        (),
        {
            "secrets": MappingProxyType({"secret": "sec-1", "access_token": "acc-1"}),
            "config": MappingProxyType({"client_id": "cid-1", "environment": "sandbox"}),
            "securo": MappingProxyType({}),
        },
    )()

    def _make_requester(holdings_rows: list[dict[str, Any]]):
        def req(url: str, payload: dict[str, Any]) -> dict[str, Any]:
            if url.endswith("/accounts/get"):
                return {
                    "item": {},
                    "accounts": [
                        {
                            "account_id": "acc-m1",
                            "name": "M1 Brokerage",
                            "type": "investment",
                            "subtype": "brokerage",
                            "balances": {"current": 5000.0, "iso_currency_code": "USD"},
                        }
                    ],
                }
            if url.endswith("/investments/holdings/get"):
                return {
                    "securities": [
                        {"security_id": f"sec-{i}", "name": f"ETF {i}", "ticker_symbol": f"E{i}", "type": "etf"}
                        for i in range(5)
                    ],
                    "holdings": holdings_rows,
                }
            if url.endswith("/investments/transactions/get"):
                return {"investment_transactions": [], "total_investment_transactions": 0}
            raise AssertionError(url)

        return req

    # 1. Null institution_value -> omit holdings so existing positions aren't archived,
    #    and report gross balance as auto_invested_balance upper bound for retained holdings.
    res_null = mod.PlaidPlugin(
        requester=_make_requester(
            [
                {"account_id": "acc-m1", "security_id": "sec-0", "quantity": 10, "institution_value": 2500.0},
                {"account_id": "acc-m1", "security_id": "sec-1", "quantity": 5, "institution_value": None},
            ]
        )
    ).sync({"state": {}}, ctx)
    assert "holdings" not in res_null["data"]
    assert res_null["data"]["accounts"][0]["auto_invested_balance"] == "5000.00"

    # 2. Exceeding MAX_SYNC_HOLDINGS -> omit holdings rather than publishing a truncated list.
    monkeypatch.setattr(mod, "MAX_SYNC_HOLDINGS", 2)
    res_cap = mod.PlaidPlugin(
        requester=_make_requester(
            [
                {"account_id": "acc-m1", "security_id": f"sec-{i}", "quantity": 1, "institution_value": 100.0}
                for i in range(3)
            ]
        )
    ).sync({"state": {}}, ctx)
    assert "holdings" not in res_cap["data"]
    assert res_cap["data"]["accounts"][0]["auto_invested_balance"] == "5000.00"

    # 3. Confirmed empty portfolio -> still sends holdings: [] with no auto_invested_balance.
    res_empty = mod.PlaidPlugin(requester=_make_requester([])).sync({"state": {}}, ctx)
    assert res_empty["data"]["holdings"] == []
    assert "auto_invested_balance" not in res_empty["data"]["accounts"][0]


@pytest.mark.asyncio
async def test_plaid_omitted_incomplete_holdings_retains_assets_and_cash_balance(
    session: AsyncSession, ids, plaid_catalog, plugin_runner, states, monkeypatch
):
    """End-to-end Plaid regression: after an initial complete sync (2750 gross,
    2500 VTI -> 250 cash), a follow-up sync where Plaid returns a position with
    `institution_value: null` omits `holdings` and reports gross balance as the
    `auto_invested_balance` cap, so Securo retains VTI and keeps cash at 250
    (and clamps cleanly to 0 if gross balance drops below stored VTI value). An
    explicit empty portfolio (`holdings: []`) later archives VTI and restores 2750."""
    mod = _module()

    class _IncompleteHoldingsPlaid(FakePlaidServer):
        def __init__(self, plugin_mod: ModuleType) -> None:
            super().__init__(plugin_mod)
            self.holdings_mode = "complete"
            self.gross_override: float | None = None

        def __call__(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
            if url.endswith("/accounts/get") and self.gross_override is not None:
                resp = super().__call__(url, payload)
                accounts = [
                    {**a, "balances": {**a["balances"], "current": self.gross_override}}
                    if a["account_id"] == "acc-m1"
                    else a
                    for a in resp["accounts"]
                ]
                return {**resp, "accounts": accounts}
            if url.endswith("/investments/holdings/get"):
                if self.holdings_mode == "incomplete":
                    return {
                        "securities": [
                            {
                                "security_id": "sec-vti",
                                "name": "Vanguard Total Stock Market ETF",
                                "ticker_symbol": "VTI",
                                "type": "etf",
                                "iso_currency_code": "USD",
                                "is_cash_equivalent": False,
                            },
                            {
                                "security_id": "sec-unpriced",
                                "name": "Unpriced Fund",
                                "ticker_symbol": "UNPR",
                                "type": "mutual fund",
                                "iso_currency_code": "USD",
                                "is_cash_equivalent": False,
                            },
                        ],
                        "holdings": [
                            {
                                "account_id": "acc-m1",
                                "security_id": "sec-vti",
                                "quantity": 10,
                                "institution_price": 250.00,
                                "institution_value": 2500.00,
                                "iso_currency_code": "USD",
                            },
                            {
                                "account_id": "acc-m1",
                                "security_id": "sec-unpriced",
                                "quantity": 5,
                                "institution_price": None,
                                "institution_value": None,
                                "iso_currency_code": "USD",
                            },
                        ],
                    }
                if self.holdings_mode == "empty":
                    return {"securities": [], "holdings": []}
            return super().__call__(url, payload)

    fake = _IncompleteHoldingsPlaid(mod)
    _wire_fake_plaid(monkeypatch, fake)

    workspace_id, user_id = ids
    await _apply(session, workspace_id, user_id)

    connection, _, state = await connections.create_with_oauth(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        can_write=True,
        plugin_id=PLUGIN,
        integration_key="plaid",
        name="Plaid M1",
        values={"client_id": "cid-1", "secret": "sec-secret-99", "environment": "sandbox"},
        lang="en",
    )
    assert state is not None
    await connections.finish_oauth(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        code=None,
        state=state,
        params={"status": "success", "link_session_id": "ls-init-1"},
    )

    # 1. Initial complete sync: 2750 gross - 2500 VTI = 250 cash.
    await connections.sync(session, connection)
    bank = await _bank(session, connection)
    bank_id = bank.id
    accounts = {a.external_id: a for a in await _rows(session, Account, connection_id=bank_id)}
    assert accounts["acc-m1"].balance == Decimal("250.00")
    assert accounts["acc-chk"].balance == Decimal("2500.00")
    (vti,) = await _rows(session, Asset, connection_id=bank_id)
    vti_id = vti.id
    assert vti.ticker == "VTI" and vti.is_archived is False

    # 2. Incomplete holdings snapshot (null institution_value): holdings omitted,
    #    VTI stays live AND acc-m1 cash balance stays 250.00 (not inflated to 2750.00).
    fake.holdings_mode = "incomplete"
    await connections.sync(session, connection)
    assert connection.status == "active"
    accounts_incomplete = {a.external_id: a for a in await _rows(session, Account, connection_id=bank_id)}
    assert accounts_incomplete["acc-m1"].balance == Decimal("250.00")
    assert accounts_incomplete["acc-chk"].balance == Decimal("2500.00")
    (vti_retained,) = await _rows(session, Asset, connection_id=bank_id)
    assert vti_retained.id == vti_id and vti_retained.is_archived is False

    # 2b. If gross balance drops below stored VTI value (2200 < 2500) during an
    #     incomplete snapshot, deduction caps at gross balance so cash clamps at 0.00.
    fake.gross_override = 2200.00
    await connections.sync(session, connection)
    accounts_dropped = {a.external_id: a for a in await _rows(session, Account, connection_id=bank_id)}
    assert accounts_dropped["acc-m1"].balance == Decimal("0.00")
    fake.gross_override = None

    # 3. Authoritative empty holdings snapshot: VTI is archived and cash becomes 2750.00.
    fake.holdings_mode = "empty"
    await connections.sync(session, connection)
    accounts_empty = {a.external_id: a for a in await _rows(session, Account, connection_id=bank_id)}
    assert accounts_empty["acc-m1"].balance == Decimal("2750.00")
    (vti_archived,) = await _rows(session, Asset, connection_id=bank_id)
    assert vti_archived.is_archived is True
