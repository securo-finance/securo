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
import tarfile
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
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
    """Route runner calls to the real PlaidPlugin class backed by `fake`."""
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
        ctx = SimpleNamespace(
            secrets=dict(secrets or {}),
            config=dict(config or {}),
            securo=dict(securo or {}),
        )
        try:
            return getattr(instance, method)(dict(params or {}), ctx)
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

    # Nelnet student loan & mortgage terms
    nelnet = accounts["acc-nelnet"]
    assert (nelnet.type, nelnet.balance, nelnet.loan_kind) == ("loan", Decimal("18250.40"), "student")
    assert nelnet.loan_original_amount == Decimal("22000.00")
    assert nelnet.loan_interest_rate == Decimal("5.05")
    assert nelnet.loan_rate_period == "yearly_nominal"
    assert nelnet.loan_term_months == 120
    assert nelnet.payment_due_day == 20
    assert nelnet.minimum_payment == Decimal("233.75")

    mort = accounts["acc-mort"]
    assert (mort.type, mort.loan_kind, mort.loan_term_months, mort.loan_amortization) == (
        "loan",
        "mortgage",
        360,
        "price",
    )
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
