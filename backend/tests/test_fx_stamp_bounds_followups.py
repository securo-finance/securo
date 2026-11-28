"""Follow-up FX stamp bounds checks (issue #1795).

Covers:
1. `bounded_conversion` rejecting non-finite Decimals (`NaN`, `Infinity`) with
   `MoneyOutOfRange` instead of `InvalidOperation`.
2. `_stamp_synced_primary_amount` and `stamp_account_amount` bounding provider
   conversions, derived rates, and bank account amounts while restoring all four
   FX columns on overflow so sync never half-stamps or fails.
3. MCP `propose_update_transaction` returning a preview naming the column limit
   when the restamped conversion overflows `MAX_MONEY_AMOUNT`.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import mcp_server.tools  # noqa: F401
from app.core.app_clock import app_today
from app.core.money import (
    MAX_MONEY_AMOUNT,
    MoneyOutOfRange,
    bounded_conversion,
    quantize_bounded_conversion,
)
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.providers.base import AccountData, BankProvider, ConnectionData, TransactionData
from app.services.connection_service import (
    _same_cents,
    _stamp_synced_primary_amount,
    sync_connection,
)
from app.services.fx_rate_service import (
    ACCOUNT_AMOUNT_FROM_BANK,
    resolve_primary_stamp,
    stamp_account_amount,
)
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY

OVERFLOW_AMOUNT = MAX_MONEY_AMOUNT + Decimal("0.01")


# --- 1. bounded_conversion non-finite values ---------------------------------


@pytest.mark.parametrize(
    "value",
    [
        Decimal("NaN"),
        Decimal("-NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_bounded_conversion_rejects_non_finite_values(value: Decimal):
    with pytest.raises(MoneyOutOfRange, match="amount_primary must be a finite number"):
        bounded_conversion(value)

    with pytest.raises(MoneyOutOfRange, match="amount_account must be a finite number"):
        bounded_conversion(value, "amount_account")


# --- 2. Synced FX stamp bounds & rollback ------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_bank_amount",
    [OVERFLOW_AMOUNT, Decimal("1e30"), Decimal("NaN"), Decimal("Infinity")],
)
async def test_stamp_account_amount_rejects_invalid_bank_amount(
    session: AsyncSession,
    test_user: User,
    test_account: Account,
    bad_bank_amount: Decimal,
):
    assert test_account.currency == "BRL"
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        account_id=test_account.id,
        description="Foreign card charge",
        amount=Decimal("100.00"),
        currency="USD",
        date=app_today(),
        type="debit",
        source="sync",
        amount_account=Decimal("500.00"),
        amount_account_source=ACCOUNT_AMOUNT_FROM_BANK,
    )
    session.add(tx)
    await session.flush()

    with pytest.raises(MoneyOutOfRange, match="amount_account"):
        await stamp_account_amount(session, tx, bank_amount=bad_bank_amount)

    assert tx.amount_account == Decimal("500.00")
    assert tx.amount_account_source == ACCOUNT_AMOUNT_FROM_BANK


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_converted",
    [OVERFLOW_AMOUNT, Decimal("1e30"), Decimal("NaN"), Decimal("Infinity")],
)
async def test_synced_stamp_overflowing_provider_conversion_preserves_previous_fields(
    session: AsyncSession,
    test_user: User,
    test_account: Account,
    bad_converted: Decimal,
):
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        account_id=test_account.id,
        description="Synced wire",
        amount=Decimal("100.00"),
        currency="USD",
        date=app_today(),
        type="debit",
        source="sync",
        amount_primary=Decimal("500.00"),
        fx_rate_used=Decimal("5.0000000000"),
        amount_account=Decimal("500.00"),
        amount_account_source=ACCOUNT_AMOUNT_FROM_BANK,
    )
    session.add(tx)
    await session.flush()

    await _stamp_synced_primary_amount(
        session,
        test_user.id,
        tx,
        bad_converted,
        bad_converted,
        restamp_account=True,
    )

    assert tx.amount_primary == Decimal("500.00")
    assert tx.fx_rate_used == Decimal("5.0000000000")
    assert tx.amount_account == Decimal("500.00")
    assert tx.amount_account_source == ACCOUNT_AMOUNT_FROM_BANK


@pytest.mark.asyncio
async def test_synced_stamp_overflowing_derived_rate_preserves_previous_fields(
    session: AsyncSession,
    test_user: User,
    test_account: Account,
):
    """`converted` fits in `amount_primary`, but `converted / amount` exceeds `MAX_FX_RATE`."""
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        account_id=test_account.id,
        description="Tiny native amount",
        amount=Decimal("0.01"),
        currency="USD",
        date=app_today(),
        type="debit",
        source="sync",
        amount_primary=Decimal("0.05"),
        fx_rate_used=Decimal("5.0000000000"),
        amount_account=Decimal("0.05"),
        amount_account_source=ACCOUNT_AMOUNT_FROM_BANK,
    )
    session.add(tx)
    await session.flush()

    await _stamp_synced_primary_amount(
        session,
        test_user.id,
        tx,
        Decimal("1000000000000.00"),
        Decimal("1000000000000.00"),
        restamp_account=True,
    )

    assert tx.amount_primary == Decimal("0.05")
    assert tx.fx_rate_used == Decimal("5.0000000000")
    assert tx.amount_account == Decimal("0.05")
    assert tx.amount_account_source == ACCOUNT_AMOUNT_FROM_BANK


@pytest.mark.asyncio
async def test_synced_stamp_restores_all_fields_when_bank_account_amount_overflows(
    session: AsyncSession,
    test_user: User,
    test_account: Account,
):
    """`converted` fits, but `bank_amount` passed to `stamp_account_amount` overflows."""
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        account_id=test_account.id,
        description="Half-stamp guard (bank amount)",
        amount=Decimal("100.00"),
        currency="USD",
        date=app_today(),
        type="debit",
        source="sync",
        amount_primary=Decimal("490.00"),
        fx_rate_used=Decimal("4.9000000000"),
        amount_account=Decimal("490.00"),
        amount_account_source="user",
    )
    session.add(tx)
    await session.flush()

    await _stamp_synced_primary_amount(
        session,
        test_user.id,
        tx,
        Decimal("545.10"),
        OVERFLOW_AMOUNT,
        restamp_account=True,
    )

    assert tx.amount_primary == Decimal("490.00")
    assert tx.fx_rate_used == Decimal("4.9000000000")
    assert tx.amount_account == Decimal("490.00")
    assert tx.amount_account_source == "user"


@pytest.mark.asyncio
async def test_synced_stamp_restores_all_fields_when_derived_account_amount_overflows(
    session: AsyncSession,
    test_user: User,
    test_account: Account,
):
    """`converted` fits in BRL (5e11), but `stamp_account_amount` overflows on JPY (1.5e13)."""
    today = app_today()
    session.add_all(
        [
            FxRate(
                base_currency="USD",
                quote_currency="BRL",
                date=today,
                rate=Decimal("5"),
                source="test",
            ),
            FxRate(
                base_currency="USD",
                quote_currency="JPY",
                date=today,
                rate=Decimal("150"),
                source="test",
            ),
        ]
    )
    jpy_account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        name="Yen card",
        type="checking",
        balance=Decimal("0"),
        currency="JPY",
    )
    session.add(jpy_account)
    await session.flush()

    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        account_id=jpy_account.id,
        description="Half-stamp guard (FX account leg)",
        amount=Decimal("100000000000.00"),
        currency="USD",
        date=today,
        type="debit",
        source="sync",
        amount_primary=None,
        fx_rate_used=None,
        amount_account=None,
        amount_account_source=None,
    )
    session.add(tx)
    await session.flush()

    await _stamp_synced_primary_amount(
        session,
        test_user.id,
        tx,
        Decimal("500000000000.00"),
        None,
        restamp_account=True,
    )

    assert tx.amount_primary is None
    assert tx.fx_rate_used is None
    assert tx.amount_account is None
    assert tx.amount_account_source is None


@pytest.mark.asyncio
async def test_synced_stamp_fallback_branch_overflow_preserves_previous_fields(
    session: AsyncSession,
    test_user: User,
    test_account: Account,
):
    """When `converted is None` and `stamp_primary_amount` overflows, sync still succeeds."""
    today = app_today()
    session.add(
        FxRate(
            base_currency="USD",
            quote_currency="BRL",
            date=today,
            rate=Decimal("5"),
            source="test",
        )
    )
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        account_id=test_account.id,
        description="Overflow via FX table",
        amount=Decimal("2000000000000.00"),
        currency="USD",
        date=today,
        type="debit",
        source="sync",
        amount_primary=Decimal("100.00"),
        fx_rate_used=Decimal("1.0000000000"),
        amount_account=Decimal("100.00"),
        amount_account_source=ACCOUNT_AMOUNT_FROM_BANK,
    )
    session.add(tx)
    await session.flush()

    await _stamp_synced_primary_amount(
        session,
        test_user.id,
        tx,
        None,
        None,
        restamp_account=True,
    )

    assert tx.amount_primary == Decimal("100.00")
    assert tx.fx_rate_used == Decimal("1.0000000000")
    assert tx.amount_account == Decimal("100.00")
    assert tx.amount_account_source == ACCOUNT_AMOUNT_FROM_BANK


@pytest.mark.asyncio
async def test_synced_stamp_valid_provider_conversion_rounds_and_sets_fields(
    session: AsyncSession,
    test_user: User,
    test_account: Account,
):
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        account_id=test_account.id,
        description="Valid synced conversion",
        amount=Decimal("100.00"),
        currency="USD",
        date=app_today(),
        type="debit",
        source="sync",
    )
    session.add(tx)
    await session.flush()

    await _stamp_synced_primary_amount(
        session,
        test_user.id,
        tx,
        Decimal("545.104"),
        Decimal("545.10"),
        restamp_account=True,
    )

    assert tx.amount_primary == Decimal("545.10")
    assert tx.fx_rate_used == Decimal("5.4510400000")
    assert tx.amount_account == Decimal("545.10")
    assert tx.amount_account_source == ACCOUNT_AMOUNT_FROM_BANK


# --- 3. MCP edit preview on MoneyOutOfRange ----------------------------------


@pytest.mark.asyncio
async def test_mcp_edit_preview_names_limit_when_conversion_overflows(
    session: AsyncSession,
    test_user: User,
    test_workspace,
    test_account: Account,
):
    on_date = date(2024, 5, 10)
    session.add(
        FxRate(
            base_currency="USD",
            quote_currency="BRL",
            date=on_date,
            rate=Decimal("5.0"),
            source="test",
        )
    )
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        description="USD purchase",
        amount=Decimal("60.00"),
        currency="USD",
        date=on_date,
        effective_date=on_date,
        type="debit",
        source="manual",
        status="posted",
        amount_primary=Decimal("300.00"),
        fx_rate_used=Decimal("5.0"),
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)

    tx_id = tx.id
    ctx = CallContext(user_id=test_user.id, conversation_id=uuid.uuid4(), external=True)
    handler = REGISTRY["propose_update_transaction"].handler

    preview = await handler(
        session=session,
        ctx=ctx,
        transaction_id=str(tx_id),
        amount=2_000_000_000_000.0,
    )

    assert "error" not in preview
    assert preview["kind"] == "update_transaction"
    assert preview["derived"]["amount_primary"] is None
    assert preview["derived"]["fx_rate_used"] is None
    assert str(MAX_MONEY_AMOUNT) in preview["derived"]["note"]
    assert "amount_primary" in preview["derived"]["note"]

    after_preview = (
        await session.execute(
            select(Transaction)
            .where(Transaction.id == tx_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert after_preview.amount == Decimal("60.00")
    assert after_preview.amount_primary == Decimal("300.00")
    assert after_preview.fx_rate_used == Decimal("5.0")

    # Applying with external token still refuses cleanly without committing.
    applied = await handler(
        session=session,
        ctx=ctx,
        transaction_id=str(tx_id),
        amount=2_000_000_000_000.0,
        apply=True,
    )
    assert applied["applied"] is False
    assert str(MAX_MONEY_AMOUNT) in applied["error"]

    await session.rollback()
    fresh = (
        await session.execute(
            select(Transaction)
            .where(Transaction.id == tx_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert fresh.amount == Decimal("60.00")
    assert fresh.amount_primary == Decimal("300.00")
    assert fresh.fx_rate_used == Decimal("5.0")


# --- 4. Pre-quantize guards, ROUND_HALF_UP, and e2e sync_connection bounds ---


def test_quantize_bounded_conversion_rounds_half_up_and_guards_extremes():
    assert quantize_bounded_conversion(Decimal("545.105"), "amount_account") == Decimal("545.11")
    assert quantize_bounded_conversion(Decimal("-545.105"), "amount_account") == Decimal("-545.11")

    with pytest.raises(MoneyOutOfRange, match="amount_primary must be at most"):
        quantize_bounded_conversion(Decimal("1e13") * Decimal("1e20"))

    with pytest.raises(MoneyOutOfRange, match="amount_account must be a finite number"):
        quantize_bounded_conversion(Decimal("NaN"), "amount_account")


@pytest.mark.asyncio
async def test_stamp_account_amount_rounds_half_up_and_guards_extreme_cross_rate(
    session: AsyncSession,
    test_user: User,
    test_account: Account,
):
    today = app_today()
    session.add_all(
        [
            FxRate(
                base_currency="USD",
                quote_currency="XXX",
                date=today,
                rate=Decimal("1e-10"),
                source="test",
            ),
            FxRate(
                base_currency="USD",
                quote_currency="YYY",
                date=today,
                rate=Decimal("1e9"),
                source="test",
            ),
        ]
    )
    yyy_account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        name="Extreme cross-rate",
        type="checking",
        balance=Decimal("0"),
        currency="YYY",
    )
    session.add(yyy_account)
    await session.flush()

    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        account_id=yyy_account.id,
        description="Half-cent bank amount",
        amount=Decimal("100.00"),
        currency="XXX",
        date=today,
        type="debit",
        source="sync",
    )
    session.add(tx)
    await session.flush()

    await stamp_account_amount(session, tx, bank_amount=Decimal("545.105"))
    assert tx.amount_account == Decimal("545.11")
    assert tx.amount_account_source == ACCOUNT_AMOUNT_FROM_BANK

    # Product is 1e12 * 1e19 = 1e31 (> 28-digit Decimal context), which must
    # raise MoneyOutOfRange rather than decimal.InvalidOperation.
    tx.amount = Decimal("1000000000000.00")
    with pytest.raises(MoneyOutOfRange, match="amount_account must be at most"):
        await stamp_account_amount(session, tx, allow_fetch=False)

    with pytest.raises(MoneyOutOfRange, match="amount_primary must be at most"):
        await resolve_primary_stamp(
            session, "YYY", Decimal("1000000000000.00"), "XXX", today, allow_fetch=False
        )


def test_same_cents_handles_non_finite_and_out_of_range_decimals():
    assert _same_cents(Decimal("545.10"), Decimal("545.104")) is True
    assert _same_cents(Decimal("545.105"), Decimal("545.11")) is True
    assert _same_cents(Decimal("545.10"), Decimal("1e30")) is False
    assert _same_cents(Decimal("545.10"), Decimal("NaN")) is False
    assert _same_cents(Decimal("545.10"), Decimal("Infinity")) is False
    assert _same_cents(Decimal("1e30"), Decimal("1e30")) is False


class _MockBankProvider(BankProvider):
    """Minimal BankProvider for end-to-end `sync_connection` tests."""

    def __init__(self, accounts: list[AccountData], transactions: list[TransactionData]):
        super().__init__()
        self._accounts = accounts
        self._transactions = transactions

    @property
    def name(self) -> str:
        return "test"

    async def get_oauth_url(
        self, redirect_uri: str, state: str, flow_params: Optional[dict] = None
    ) -> str:
        return "https://example.invalid/oauth"

    async def handle_oauth_callback(self, code: str) -> ConnectionData:
        raise NotImplementedError

    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        return self._accounts

    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: Optional[date] = None,
        payee_source: str = "auto",
    ) -> list[TransactionData]:
        return list(self._transactions)

    async def refresh_credentials(self, credentials: dict) -> dict:
        return credentials


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_amount",
    [OVERFLOW_AMOUNT, Decimal("1e30"), Decimal("NaN")],
)
async def test_sync_connection_initial_sync_overflow_leaves_fx_fields_clean(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    bad_amount: Decimal,
):
    assert test_user.primary_currency == "BRL"
    conn = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        provider="test",
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name="Test Bank",
        credentials={"token": "fake"},
        status="active",
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()

    ext_tx_id = f"sync-overflow-{uuid.uuid4().hex[:8]}"
    provider = _MockBankProvider(
        accounts=[
            AccountData(
                external_id="brl-card-1",
                name="Card",
                type="credit_card",
                balance=Decimal("0"),
                currency="BRL",
            )
        ],
        transactions=[
            TransactionData(
                external_id=ext_tx_id,
                description="Foreign charge",
                amount=Decimal("100.00"),
                date=app_today(),
                type="debit",
                currency="USD",
                amount_in_account_currency=bad_amount,
            )
        ],
    )

    with (
        patch("app.services.connection_service.get_provider", return_value=provider),
        patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock),
        patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock),
    ):
        result_conn, _merged, _outcome = await sync_connection(
            session, conn.id, test_workspace.id, test_user.id
        )

    assert result_conn.status == "active"
    tx = await session.scalar(
        select(Transaction)
        .where(Transaction.external_id == ext_tx_id)
        .execution_options(populate_existing=True)
    )
    assert tx is not None
    assert tx.amount == Decimal("100.00")
    assert tx.amount_primary is None
    assert tx.fx_rate_used is None
    assert tx.amount_account is None
    assert tx.amount_account_source is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_amount",
    [OVERFLOW_AMOUNT, Decimal("1e30"), Decimal("NaN"), Decimal("Infinity")],
)
async def test_sync_connection_resync_overflow_preserves_all_stamped_fx_fields(
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
    bad_amount: Decimal,
):
    assert test_user.primary_currency == "BRL"
    conn = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        provider="test",
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name="Test Bank",
        credentials={"token": "fake"},
        status="active",
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()

    ext_tx_id = f"sync-resync-{uuid.uuid4().hex[:8]}"
    accounts = [
        AccountData(
            external_id="brl-card-resync",
            name="Card",
            type="credit_card",
            balance=Decimal("0"),
            currency="BRL",
        )
    ]
    provider = _MockBankProvider(
        accounts=accounts,
        transactions=[
            TransactionData(
                external_id=ext_tx_id,
                description="Foreign charge",
                amount=Decimal("100.00"),
                date=app_today(),
                type="debit",
                currency="USD",
                amount_in_account_currency=Decimal("545.10"),
            )
        ],
    )

    with (
        patch("app.services.connection_service.get_provider", return_value=provider),
        patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock),
        patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock),
    ):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)

        tx = await session.scalar(
            select(Transaction)
            .where(Transaction.external_id == ext_tx_id)
            .execution_options(populate_existing=True)
        )
        assert tx is not None
        assert tx.amount_primary == Decimal("545.10")
        assert tx.fx_rate_used == Decimal("5.4510000000")
        assert tx.amount_account == Decimal("545.10")
        assert tx.amount_account_source == ACCOUNT_AMOUNT_FROM_BANK

        # Re-sync with a corrupted/overflowing account-currency amount:
        # `_refresh_account_charge` -> `_same_cents` must not crash and must
        # preserve all 4 prior stamped fields.
        provider._transactions = [
            TransactionData(
                external_id=ext_tx_id,
                description="Foreign charge",
                amount=Decimal("100.00"),
                date=app_today(),
                type="debit",
                currency="USD",
                amount_in_account_currency=bad_amount,
            )
        ]
        result_conn, _merged, _outcome = await sync_connection(
            session, conn.id, test_workspace.id, test_user.id
        )

    assert result_conn.status == "active"
    refreshed = await session.scalar(
        select(Transaction)
        .where(Transaction.external_id == ext_tx_id)
        .execution_options(populate_existing=True)
    )
    assert refreshed is not None
    assert refreshed.amount_primary == Decimal("545.10")
    assert refreshed.fx_rate_used == Decimal("5.4510000000")
    assert refreshed.amount_account == Decimal("545.10")
    assert refreshed.amount_account_source == ACCOUNT_AMOUNT_FROM_BANK


@pytest.mark.asyncio
async def test_primary_restamp_matches_live_half_up_rounding(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    """Restamping uses the same half-up policy as live stamps so an exact
    half-cent conversion (1.00 USD * 1.005 = 1.005 -> 1.01 BRL) does not drift
    by a cent after a primary-currency restamp."""
    from app.services.fx_rate_service import stamp_primary_amount
    from app.services.primary_restamp_service import restamp_user_primary_amounts

    on_date = date(2026, 6, 15)
    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "BRL"}
    session.add(
        FxRate(
            base_currency="USD",
            quote_currency="BRL",
            date=on_date,
            rate=Decimal("1.005"),
            source="test",
        )
    )
    acct = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="USD Checking",
        type="checking",
        currency="USD",
        balance=Decimal("0.00"),
    )
    session.add(acct)
    await session.flush()

    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=acct.id,
        description="Half-cent test",
        amount=Decimal("1.00"),
        currency="USD",
        type="debit",
        date=on_date,
        status="posted",
        source="manual",
    )
    session.add(tx)
    await session.flush()

    await stamp_primary_amount(session, test_user.id, tx, allow_fetch=False)
    await session.commit()
    assert tx.amount_primary == Decimal("1.01")

    await restamp_user_primary_amounts(session, test_user.id)
    await session.commit()
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("1.01")
