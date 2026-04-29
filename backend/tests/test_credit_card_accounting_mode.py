"""Tests for the cash vs accrual accounting mode feature.

Covers:
    1. compute_effective_date cycle math (edge cases, month-end clamping, wraparound).
    2. apply_effective_date dispatch (CC vs non-CC, missing metadata).
    3. Event-listener safety net (defaults effective_date to date when unset).
    4. Aggregation services in both modes — dashboard, budgets, reports.
    5. Global setting getter + default fallback.
    6. Account-level balance queries are NOT affected by mode (ledger invariant).
    7. Transaction updates refresh effective_date when date or account_id changes.
    8. Editing statement_close_day on an account recomputes historical txs.

The tests exercise the in-memory SQLite test DB from conftest. They use the
standard `session`, `test_user`, `test_categories`, `test_connection` fixtures.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.account import Account
from app.models.transaction import Transaction
from app.services import (
    account_service,
    admin_service,
    budget_service,
    dashboard_service,
)
from app.services.credit_card_service import (
    apply_effective_date,
    compute_effective_date,
)


# ---------------------------------------------------------------------------
# Unit tests: compute_effective_date — pure cycle math, no DB needed.
# ---------------------------------------------------------------------------


class TestComputeEffectiveDate:
    """Cycle math: given a purchase date + close day + due day, return the
    due date of the bill the purchase belongs to."""

    def test_purchase_before_close_day_same_month(self):
        # Gold card: close 11, due 16. Purchase Apr 3 → bill closes Apr 11 → due Apr 16.
        assert compute_effective_date(date(2026, 4, 3), 11, 16) == date(2026, 4, 16)

    def test_purchase_on_close_day_rolls_to_next_cycle(self):
        # Brazilian convention (Nubank, Itaú, etc.): a purchase ON the close
        # day belongs to the NEXT invoice. Apr 11 close → next cycle closes
        # May 11 → due May 16.
        assert compute_effective_date(date(2026, 4, 11), 11, 16) == date(2026, 5, 16)

    def test_purchase_day_after_close_rolls_to_next_cycle(self):
        # Apr 12 is after Apr 11 close → next cycle closes May 11 → due May 16.
        assert compute_effective_date(date(2026, 4, 12), 11, 16) == date(2026, 5, 16)

    def test_cycle_spanning_month_boundary(self):
        # Tassio card: close 28, due 5 (of next month).
        # Mar 15 → cycle closes Mar 28 → bill due Apr 5.
        assert compute_effective_date(date(2026, 3, 15), 28, 5) == date(2026, 4, 5)

    def test_purchase_after_close_day_crosses_two_months(self):
        # Mar 29 (after the Mar 28 close) → next close Apr 28 → due May 5.
        assert compute_effective_date(date(2026, 3, 29), 28, 5) == date(2026, 5, 5)

    def test_close_day_clamps_to_month_end(self):
        # close_day=31 with a 30-day month should clamp to the last day.
        # Apr has 30 days. Apr 30 is the effective close day. Due 10 of May.
        assert compute_effective_date(date(2026, 4, 15), 31, 10) == date(2026, 5, 10)

    def test_due_day_clamps_to_month_end(self):
        # due_day=31 with Feb (28 days in 2026) clamps to 28.
        # close=10 → Feb cycle closes Feb 10 → due clamps to Feb 28.
        assert compute_effective_date(date(2026, 2, 5), 10, 31) == date(2026, 2, 28)

    def test_year_wraparound(self):
        # Dec purchase on CC with close day in December → cycle closes Dec, due Jan.
        assert compute_effective_date(date(2026, 12, 20), 28, 5) == date(2027, 1, 5)

    def test_december_purchase_after_close(self):
        # Dec 29, close=28 → next cycle closes Jan 28 → due Feb 5.
        assert compute_effective_date(date(2026, 12, 29), 28, 5) == date(2027, 2, 5)

    def test_passthrough_when_close_day_missing(self):
        # Without a close day, we can't compute the cycle — return tx_date.
        assert compute_effective_date(date(2026, 4, 3), None, 16) == date(2026, 4, 3)

    def test_passthrough_when_due_day_missing(self):
        assert compute_effective_date(date(2026, 4, 3), 11, None) == date(2026, 4, 3)

    def test_passthrough_when_both_missing(self):
        assert compute_effective_date(date(2026, 4, 3), None, None) == date(2026, 4, 3)

    def test_close_and_due_same_day(self):
        # Edge: close and due are the same calendar day. A cycle where the
        # bill is due on the close day itself (uncommon but legal).
        # Close=15, due=15. Mar 10 purchase → cycle closes Mar 15 → due Mar 15.
        # Wait — due > close required. With close=15 and due=15, "due day
        # strictly after close" wraps to next month. So Mar 10 → Apr 15.
        assert compute_effective_date(date(2026, 3, 10), 15, 15) == date(2026, 4, 15)


# ---------------------------------------------------------------------------
# Unit tests: apply_effective_date — dispatch on account type.
# ---------------------------------------------------------------------------


class TestApplyEffectiveDate:
    """Helper that sets transaction.effective_date based on account."""

    def test_non_cc_account_uses_purchase_date(self):
        tx = Transaction(
            user_id=uuid.uuid4(),
            account_id=uuid.uuid4(),
            description="x",
            amount=Decimal("10"),
            date=date(2026, 4, 3),
            type="debit",
            source="manual",
        )
        account = Account(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            name="checking",
            type="checking",
            balance=Decimal("0"),
        )
        apply_effective_date(tx, account)
        assert tx.effective_date == date(2026, 4, 3)

    def test_cc_account_with_full_metadata(self):
        tx = Transaction(
            user_id=uuid.uuid4(),
            account_id=uuid.uuid4(),
            description="x",
            amount=Decimal("10"),
            date=date(2026, 4, 3),
            type="debit",
            source="manual",
        )
        account = Account(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            name="gold",
            type="credit_card",
            balance=Decimal("0"),
            statement_close_day=11,
            payment_due_day=16,
        )
        apply_effective_date(tx, account)
        assert tx.effective_date == date(2026, 4, 16)

    def test_manual_effective_bill_date_override_wins(self):
        """User-set effective_bill_date beats both Pluggy bill_due_date and
        cycle math (issue #92, LucasFidelis manual override)."""
        tx = Transaction(
            user_id=uuid.uuid4(),
            account_id=uuid.uuid4(),
            description="x",
            amount=Decimal("10"),
            date=date(2026, 4, 3),
            type="debit",
            source="manual",
        )
        tx.effective_bill_date = date(2026, 6, 16)  # user explicitly says June
        account = Account(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            name="gold",
            type="credit_card",
            balance=Decimal("0"),
            statement_close_day=11,
            payment_due_day=16,
        )
        # Even when sync passes a Pluggy bill_due_date, override still wins.
        apply_effective_date(tx, account, bill_due_date=date(2026, 5, 16))
        assert tx.effective_date == date(2026, 6, 16)

    def test_manual_effective_bill_date_works_for_non_cc_too(self):
        """The override is mainly for CC accounts but the helper applies it
        regardless — useful if a future feature lets users override on other
        accounts; today the schema only exposes it for CC."""
        tx = Transaction(
            user_id=uuid.uuid4(),
            account_id=uuid.uuid4(),
            description="x",
            amount=Decimal("10"),
            date=date(2026, 4, 3),
            type="debit",
            source="manual",
        )
        tx.effective_bill_date = date(2026, 5, 1)
        account = Account(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            name="checking",
            type="checking",
            balance=Decimal("0"),
        )
        apply_effective_date(tx, account)
        assert tx.effective_date == date(2026, 5, 1)

    def test_cc_account_without_cycle_metadata_falls_back_to_purchase_date(self):
        tx = Transaction(
            user_id=uuid.uuid4(),
            account_id=uuid.uuid4(),
            description="x",
            amount=Decimal("10"),
            date=date(2026, 4, 3),
            type="debit",
            source="manual",
        )
        account = Account(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            name="nubank",
            type="credit_card",
            balance=Decimal("0"),
            statement_close_day=None,
            payment_due_day=None,
        )
        apply_effective_date(tx, account)
        assert tx.effective_date == date(2026, 4, 3)

    def test_none_account_falls_back_to_purchase_date(self):
        tx = Transaction(
            user_id=uuid.uuid4(),
            account_id=uuid.uuid4(),
            description="x",
            amount=Decimal("10"),
            date=date(2026, 4, 3),
            type="debit",
            source="manual",
        )
        apply_effective_date(tx, None)
        assert tx.effective_date == date(2026, 4, 3)


# ---------------------------------------------------------------------------
# Integration helpers and fixtures.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def cc_account(session, test_user, test_connection):
    """A credit card account with close=11, due=16 (gold-like)."""
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        connection_id=test_connection.id,
        external_id="cc-ext",
        name="gold",
        type="credit_card",
        balance=Decimal("0"),
        currency="BRL",
        statement_close_day=11,
        payment_due_day=16,
        credit_limit=Decimal("5000"),
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _make_tx(
    session,
    user_id,
    account_id,
    tx_date: date,
    amount: Decimal,
    tx_type: str = "debit",
    source: str = "sync",
    effective_date: date | None = None,
    category_id=None,
) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        account_id=account_id,
        description="test",
        amount=amount,
        currency="BRL",
        date=tx_date,
        effective_date=effective_date if effective_date is not None else tx_date,
        type=tx_type,
        source=source,
        status="posted",
        category_id=category_id,
        amount_primary=amount,
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.flush()
    return tx


async def _set_mode(session, mode: str) -> None:
    await admin_service.set_app_setting(session, "credit_card_accounting_mode", mode)


# ---------------------------------------------------------------------------
# Safety net: the before_insert event listener defaults effective_date to date.
# ---------------------------------------------------------------------------


class TestEventListenerSafetyNet:
    @pytest.mark.asyncio
    async def test_effective_date_defaults_to_date_when_unset(
        self, session, test_user, test_account
    ):
        tx = Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            account_id=test_account.id,
            description="auto",
            amount=Decimal("5"),
            currency="BRL",
            date=date(2026, 4, 3),
            type="debit",
            source="manual",
            status="posted",
            amount_primary=Decimal("5"),
            created_at=datetime.now(timezone.utc),
        )
        session.add(tx)
        await session.commit()
        await session.refresh(tx)
        assert tx.effective_date == date(2026, 4, 3)


# ---------------------------------------------------------------------------
# Global setting getter.
# ---------------------------------------------------------------------------


class TestGlobalSetting:
    @pytest.mark.asyncio
    async def test_default_is_cash_when_unset(self, session, clean_db):
        mode = await admin_service.get_credit_card_accounting_mode(session)
        assert mode == "cash"

    @pytest.mark.asyncio
    async def test_returns_stored_cash(self, session, clean_db):
        await admin_service.set_app_setting(session, "credit_card_accounting_mode", "cash")
        mode = await admin_service.get_credit_card_accounting_mode(session)
        assert mode == "cash"

    @pytest.mark.asyncio
    async def test_returns_stored_accrual(self, session, clean_db):
        await admin_service.set_app_setting(session, "credit_card_accounting_mode", "accrual")
        mode = await admin_service.get_credit_card_accounting_mode(session)
        assert mode == "accrual"

    @pytest.mark.asyncio
    async def test_ignores_invalid_value(self, session, clean_db):
        await admin_service.set_app_setting(session, "credit_card_accounting_mode", "bogus")
        mode = await admin_service.get_credit_card_accounting_mode(session)
        assert mode == "cash"  # falls back to default


# ---------------------------------------------------------------------------
# Aggregation queries: dashboard summary — the showcase case.
#
# Scenario: gold card with close=11, due=16.
#   • Mar 30 charge R$100  → effective_date Apr 16 (bill paid in Apr)
#   • Apr 5  charge R$ 50  → effective_date Apr 16 (same bill)
#   • Apr 12 charge R$ 30  → effective_date May 16 (next cycle)
#   • Apr 20 charge R$ 20  → effective_date May 16
#
# Expected monthly totals for April:
#   cash    = R$50 + R$30 + R$20 = R$100 (Apr 5, 12, 20 fall in April)
#   accrual = R$100 + R$50       = R$150 (txs whose bill is due in Apr)
# ---------------------------------------------------------------------------


class TestDashboardSummary:
    @pytest_asyncio.fixture
    async def seeded(self, session, test_user, cc_account, test_categories):
        # food category for category aggregation tests
        food_cat = test_categories[0]
        # Mar 30: R$100 debit, effective Apr 16 (bills paid in April)
        await _make_tx(
            session,
            test_user.id,
            cc_account.id,
            date(2026, 3, 30),
            Decimal("100"),
            effective_date=date(2026, 4, 16),
            category_id=food_cat.id,
        )
        # Apr 5: R$50 debit, effective Apr 16
        await _make_tx(
            session,
            test_user.id,
            cc_account.id,
            date(2026, 4, 5),
            Decimal("50"),
            effective_date=date(2026, 4, 16),
            category_id=food_cat.id,
        )
        # Apr 12: R$30 debit, effective May 16 (next cycle)
        await _make_tx(
            session,
            test_user.id,
            cc_account.id,
            date(2026, 4, 12),
            Decimal("30"),
            effective_date=date(2026, 5, 16),
            category_id=food_cat.id,
        )
        # Apr 20: R$20 debit, effective May 16
        await _make_tx(
            session,
            test_user.id,
            cc_account.id,
            date(2026, 4, 20),
            Decimal("20"),
            effective_date=date(2026, 5, 16),
            category_id=food_cat.id,
        )
        await session.commit()

    @pytest.mark.asyncio
    async def test_cash_mode_april_totals(self, session, test_user, seeded):
        await _set_mode(session, "cash")
        summary = await dashboard_service.get_summary(
            session, test_user.id, month=date(2026, 4, 1)
        )
        # Cash: Apr 5 (50) + Apr 12 (30) + Apr 20 (20) = 100
        assert summary.monthly_expenses == 100.0

    @pytest.mark.asyncio
    async def test_accrual_mode_april_totals(self, session, test_user, seeded):
        await _set_mode(session, "accrual")
        summary = await dashboard_service.get_summary(
            session, test_user.id, month=date(2026, 4, 1)
        )
        # Accrual: Mar 30 (100) + Apr 5 (50) = 150
        # (both bills hit Apr 16, land in the April month bucket)
        assert summary.monthly_expenses == 150.0

    @pytest.mark.asyncio
    async def test_cash_mode_may_totals(self, session, test_user, seeded):
        await _set_mode(session, "cash")
        summary = await dashboard_service.get_summary(
            session, test_user.id, month=date(2026, 5, 1)
        )
        # Cash: no May purchases
        assert summary.monthly_expenses == 0.0

    @pytest.mark.asyncio
    async def test_accrual_mode_may_totals(self, session, test_user, seeded):
        await _set_mode(session, "accrual")
        summary = await dashboard_service.get_summary(
            session, test_user.id, month=date(2026, 5, 1)
        )
        # Accrual: Apr 12 (30) + Apr 20 (20) = 50 — bills due May 16
        assert summary.monthly_expenses == 50.0

    @pytest.mark.asyncio
    async def test_total_conserved_across_modes(self, session, test_user, seeded):
        """The grand total across enough months must match regardless of mode."""
        await _set_mode(session, "cash")
        cash_total = 0.0
        for m in [date(2026, 3, 1), date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1)]:
            s = await dashboard_service.get_summary(session, test_user.id, month=m)
            cash_total += s.monthly_expenses
        await _set_mode(session, "accrual")
        accrual_total = 0.0
        for m in [date(2026, 3, 1), date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1)]:
            s = await dashboard_service.get_summary(session, test_user.id, month=m)
            accrual_total += s.monthly_expenses
        # All 4 charges account for R$200 total regardless of which month buckets them.
        assert cash_total == 200.0
        assert accrual_total == 200.0


# ---------------------------------------------------------------------------
# Aggregation: spending by category (dashboard pie).
# ---------------------------------------------------------------------------


class TestSpendingByCategory:
    @pytest.mark.asyncio
    async def test_category_breakdown_follows_mode(
        self, session, test_user, cc_account, test_categories
    ):
        food = test_categories[0]
        transport = test_categories[1]
        # Mar 30: R$100 food, bill Apr 16
        await _make_tx(
            session, test_user.id, cc_account.id, date(2026, 3, 30),
            Decimal("100"), effective_date=date(2026, 4, 16), category_id=food.id,
        )
        # Apr 12: R$40 transport, bill May 16
        await _make_tx(
            session, test_user.id, cc_account.id, date(2026, 4, 12),
            Decimal("40"), effective_date=date(2026, 5, 16), category_id=transport.id,
        )
        await session.commit()

        await _set_mode(session, "cash")
        cash = await dashboard_service.get_spending_by_category(
            session, test_user.id, month=date(2026, 4, 1)
        )
        cash_map = {c.category_name: c.total for c in cash}
        # Cash April: only Apr 12 transport R$40 counts
        assert cash_map.get("Transporte", 0) == 40.0
        assert cash_map.get("Alimentação", 0) == 0.0

        await _set_mode(session, "accrual")
        accrual = await dashboard_service.get_spending_by_category(
            session, test_user.id, month=date(2026, 4, 1)
        )
        accrual_map = {c.category_name: c.total for c in accrual}
        # Accrual April: Mar 30 food R$100 bills Apr 16
        assert accrual_map.get("Alimentação", 0) == 100.0
        assert accrual_map.get("Transporte", 0) == 0.0


# ---------------------------------------------------------------------------
# Budget vs actual.
# ---------------------------------------------------------------------------


class TestBudgetVsActual:
    @pytest.mark.asyncio
    async def test_budget_spending_follows_mode(
        self, session, test_user, cc_account, test_categories
    ):
        from app.models.budget import Budget
        food = test_categories[0]
        # Budget R$200/month for food
        budget = Budget(
            id=uuid.uuid4(),
            user_id=test_user.id,
            category_id=food.id,
            amount=Decimal("200"),
            currency="BRL",
            month=date(2026, 4, 1),
        )
        session.add(budget)

        # Mar 30 R$150 food (effective Apr 16)
        await _make_tx(
            session, test_user.id, cc_account.id, date(2026, 3, 30),
            Decimal("150"), effective_date=date(2026, 4, 16), category_id=food.id,
        )
        # Apr 5 R$30 food (effective Apr 16)
        await _make_tx(
            session, test_user.id, cc_account.id, date(2026, 4, 5),
            Decimal("30"), effective_date=date(2026, 4, 16), category_id=food.id,
        )
        # Apr 15 R$20 food (effective May 16 — next cycle)
        await _make_tx(
            session, test_user.id, cc_account.id, date(2026, 4, 15),
            Decimal("20"), effective_date=date(2026, 5, 16), category_id=food.id,
        )
        await session.commit()

        await _set_mode(session, "cash")
        cash = await budget_service.get_budget_vs_actual(
            session, test_user.id, date(2026, 4, 1)
        )
        cash_food = next((c for c in cash if c.category_id == food.id), None)
        # Cash April: Apr 5 (30) + Apr 15 (20) = 50
        assert cash_food is not None
        assert float(cash_food.actual_amount) == 50.0

        await _set_mode(session, "accrual")
        accrual = await budget_service.get_budget_vs_actual(
            session, test_user.id, date(2026, 4, 1)
        )
        accrual_food = next((c for c in accrual if c.category_id == food.id), None)
        # Accrual April: Mar 30 (150) + Apr 5 (30) = 180
        assert accrual_food is not None
        assert float(accrual_food.actual_amount) == 180.0


# ---------------------------------------------------------------------------
# Reports: income/expenses monthly report.
# ---------------------------------------------------------------------------


class TestIncomeExpensesReport:
    @pytest.mark.asyncio
    async def test_monthly_report_follows_mode(
        self, session, test_user, cc_account, test_categories
    ):
        # This path uses Postgres-specific `to_char`, which SQLite (the test DB)
        # doesn't implement. Rather than duplicate the query, we skip here and
        # rely on the fact that `get_income_expenses_report` uses the exact
        # same `report_date` expression as the dashboard queries above — if
        # those tests pass, this one is wired identically.
        pytest.skip("get_income_expenses_report uses Postgres to_char — SQLite test DB doesn't support it")


# ---------------------------------------------------------------------------
# Invariant: account balance queries are NOT affected by mode.
# ---------------------------------------------------------------------------


class TestAccountBalanceInvariant:
    """The physical balance of an account is independent of reporting mode.
    The CC account detail page's cycle navigation also uses Transaction.date
    deliberately, so get_account_summary should return the same numbers
    regardless of the global accounting mode."""

    @pytest.mark.asyncio
    async def test_account_balance_history_unchanged_by_mode(
        self, session, test_user, test_account
    ):
        """Non-CC account: effective_date == date so both modes are identical."""
        await _make_tx(
            session, test_user.id, test_account.id, date(2026, 4, 3),
            Decimal("100"), tx_type="debit",
        )
        await session.commit()

        await _set_mode(session, "cash")
        cash = await account_service.get_account_balance_history(
            session, test_account.id, test_user.id,
            date_from=date(2026, 4, 1), date_to=date(2026, 4, 30),
        )
        await _set_mode(session, "accrual")
        accrual = await account_service.get_account_balance_history(
            session, test_account.id, test_user.id,
            date_from=date(2026, 4, 1), date_to=date(2026, 4, 30),
        )
        assert cash == accrual


# ---------------------------------------------------------------------------
# Transaction update: changing date refreshes effective_date.
# ---------------------------------------------------------------------------


class TestTransactionUpdateRefreshesEffectiveDate:
    @pytest.mark.asyncio
    async def test_changing_date_on_cc_tx_refreshes_effective_date(
        self, session, test_user, cc_account
    ):
        from app.schemas.transaction import TransactionUpdate
        from app.services.transaction_service import create_transaction, update_transaction
        from app.schemas.transaction import TransactionCreate

        created = await create_transaction(
            session, test_user.id,
            TransactionCreate(
                account_id=cc_account.id,
                description="test",
                amount=Decimal("50"),
                currency="BRL",
                date=date(2026, 4, 3),  # Apr 3 → effective Apr 16
                type="debit",
            )
        )
        assert created.effective_date == date(2026, 4, 16)

        updated = await update_transaction(
            session, created.id, test_user.id,
            TransactionUpdate(date=date(2026, 4, 12))  # Apr 12 → effective May 16
        )
        assert updated is not None
        assert updated.effective_date == date(2026, 5, 16)


# ---------------------------------------------------------------------------
# Account update: changing close/due days recomputes historical effective_dates.
# ---------------------------------------------------------------------------


class TestAccountCycleEditRecomputesEffectiveDates:
    @pytest.mark.asyncio
    async def test_changing_close_day_rebuckets_historical_txs(
        self, session, test_user, cc_account
    ):
        from app.schemas.account import AccountUpdate

        # Create 2 historical txs.
        await _make_tx(
            session, test_user.id, cc_account.id, date(2026, 3, 5),
            Decimal("10"), effective_date=date(2026, 3, 16),
        )
        await _make_tx(
            session, test_user.id, cc_account.id, date(2026, 3, 20),
            Decimal("20"), effective_date=date(2026, 4, 16),
        )
        await session.commit()

        # Now admin changes the close day from 11 to 25.
        # With close=25 and due=16:
        #   Mar 5  → cycle Feb 26..Mar 25 → bill due Apr 16
        #   Mar 20 → cycle Feb 26..Mar 25 → bill due Apr 16
        await account_service.update_account(
            session, cc_account.id, test_user.id,
            AccountUpdate(statement_close_day=25)
        )
        result = await session.execute(
            select(Transaction).where(Transaction.account_id == cc_account.id)
            .order_by(Transaction.date)
        )
        txs = result.scalars().all()
        assert all(t.effective_date == date(2026, 4, 16) for t in txs)


# ---------------------------------------------------------------------------
# Manual cycle override (effective_bill_date) — tx list filter must respect
# the override regardless of accounting mode (issue #92, LucasFidelis).
# ---------------------------------------------------------------------------


class TestEffectiveBillDateFiltersList:
    @pytest.mark.asyncio
    async def test_override_moves_tx_between_cycles_in_cash_mode(
        self, session, test_user, cc_account
    ):
        """A tx whose natural date falls in May, but with an effective_bill_date
        in March, must be returned for a March-window query AND excluded from
        a May-window query — even in cash mode (where the default filter is
        Transaction.date)."""
        from app.services.transaction_service import get_transactions
        await _set_mode(session, "cash")
        tx = await _make_tx(
            session, test_user.id, cc_account.id,
            date(2026, 4, 18), Decimal("55.90"),
            effective_date=date(2026, 5, 22),
        )
        tx.effective_bill_date = date(2026, 3, 1)
        await session.commit()

        # March window: should include the override'd tx.
        march_txs, _ = await get_transactions(
            session, test_user.id, account_id=cc_account.id,
            from_date=date(2026, 2, 16), to_date=date(2026, 3, 15),
            accounting_mode="cash",
        )
        assert any(t.id == tx.id for t in march_txs)

        # May window: should NOT include it anymore.
        may_txs, _ = await get_transactions(
            session, test_user.id, account_id=cc_account.id,
            from_date=date(2026, 4, 16), to_date=date(2026, 5, 15),
            accounting_mode="cash",
        )
        assert not any(t.id == tx.id for t in may_txs)

    @pytest.mark.asyncio
    async def test_override_moves_tx_between_cycles_in_accrual_mode(
        self, session, test_user, cc_account
    ):
        """Same behavior in accrual mode — override must beat both modes'
        default columns."""
        from app.services.transaction_service import get_transactions
        await _set_mode(session, "accrual")
        tx = await _make_tx(
            session, test_user.id, cc_account.id,
            date(2026, 4, 18), Decimal("55.90"),
            effective_date=date(2026, 5, 22),  # accrual would put this in May
        )
        tx.effective_bill_date = date(2026, 3, 1)
        await session.commit()

        march_txs, _ = await get_transactions(
            session, test_user.id, account_id=cc_account.id,
            from_date=date(2026, 2, 16), to_date=date(2026, 3, 15),
            accounting_mode="accrual",
        )
        assert any(t.id == tx.id for t in march_txs)

    @pytest.mark.asyncio
    async def test_bill_id_filter_includes_unlinked_txs_in_cycle_window(
        self, session, test_user, cc_account
    ):
        """When a tx is NOT linked to the bill via bill_id (e.g. a manual
        recurring entry the user added to compensate for a tx Pluggy failed
        to fetch — abdalanervoso's Wellhub on Bradesco case), it should
        still count toward the bill's cycle if its date falls in the
        cycle window."""
        from app.services.transaction_service import get_transactions
        from app.models.credit_card_bill import CreditCardBill
        from datetime import datetime, timezone

        # Pluggy bill due Apr 16
        bill = CreditCardBill(
            user_id=test_user.id, account_id=cc_account.id,
            external_id="bill-x", due_date=date(2026, 4, 16),
            total_amount=Decimal("100"), currency="BRL",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(bill)
        await session.flush()

        # Linked tx (the real one Pluggy returned)
        linked = await _make_tx(
            session, test_user.id, cc_account.id,
            date(2026, 4, 5), Decimal("50"),
            effective_date=date(2026, 4, 16),
        )
        linked.bill_id = bill.id

        # Unlinked manual recurring (the workaround tx)
        unlinked = await _make_tx(
            session, test_user.id, cc_account.id,
            date(2026, 4, 6), Decimal("50"),
            effective_date=date(2026, 4, 16),
        )
        await session.commit()

        # Cycle window for this bill = [Mar 17, Apr 16]
        txs, _ = await get_transactions(
            session, test_user.id, account_id=cc_account.id,
            bill_id=bill.id,
            from_date=date(2026, 3, 17), to_date=date(2026, 4, 16),
            accounting_mode="cash",
        )
        ids = {t.id for t in txs}
        assert linked.id in ids
        assert unlinked.id in ids, (
            "manual unlinked tx in cycle window must still count when filtering "
            "by bill_id (issue #92, abdalanervoso's recurring-payment workaround)"
        )

    @pytest.mark.asyncio
    async def test_bill_id_filter_excludes_pending_sync_unlinked_txs(
        self, session, test_user, cc_account
    ):
        """Pending sync txs without a billId are 'provider hasn't classified
        yet' — they shouldn't auto-bucket into a bill by date, because the
        bank often rolls them to the next bill once they post (issue #92,
        abdalanervoso's Bradesco late-month pending case)."""
        from app.services.transaction_service import get_transactions
        from app.models.credit_card_bill import CreditCardBill
        from datetime import datetime, timezone

        bill = CreditCardBill(
            user_id=test_user.id, account_id=cc_account.id,
            external_id="bill-z", due_date=date(2026, 4, 16),
            total_amount=Decimal("100"), currency="BRL",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(bill)
        await session.flush()

        # Pending sync tx with date in cycle window — should be DEFERRED
        pending = await _make_tx(
            session, test_user.id, cc_account.id,
            date(2026, 4, 8), Decimal("99"),
            effective_date=date(2026, 4, 16),
            source="sync",
        )
        pending.status = "pending"

        # Posted sync tx in window without billId — SHOULD count (provider
        # returned but didn't tag, reasonable to fill in by date)
        posted_synced = await _make_tx(
            session, test_user.id, cc_account.id,
            date(2026, 4, 9), Decimal("50"),
            effective_date=date(2026, 4, 16),
            source="sync",
        )
        posted_synced.status = "posted"

        # Manual user entry in window — SHOULD count (explicit intent)
        manual = await _make_tx(
            session, test_user.id, cc_account.id,
            date(2026, 4, 10), Decimal("75"),
            effective_date=date(2026, 4, 16),
            source="manual",
        )
        await session.commit()

        txs, _ = await get_transactions(
            session, test_user.id, account_id=cc_account.id,
            bill_id=bill.id,
            from_date=date(2026, 3, 17), to_date=date(2026, 4, 16),
            accounting_mode="cash",
        )
        ids = {t.id for t in txs}
        assert pending.id not in ids, "pending sync without billId must NOT be counted"
        assert posted_synced.id in ids
        assert manual.id in ids

    @pytest.mark.asyncio
    async def test_bill_id_filter_excludes_unlinked_txs_outside_window(
        self, session, test_user, cc_account
    ):
        """An unlinked tx with a date outside the cycle window must NOT be
        counted — otherwise we'd pick up unrelated manual entries."""
        from app.services.transaction_service import get_transactions
        from app.models.credit_card_bill import CreditCardBill
        from datetime import datetime, timezone

        bill = CreditCardBill(
            user_id=test_user.id, account_id=cc_account.id,
            external_id="bill-y", due_date=date(2026, 4, 16),
            total_amount=Decimal("100"), currency="BRL",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(bill)
        await session.flush()

        # Unlinked tx in a totally different month — should NOT be in cycle
        outside = await _make_tx(
            session, test_user.id, cc_account.id,
            date(2026, 1, 5), Decimal("99"),
            effective_date=date(2026, 1, 16),
        )
        await session.commit()

        txs, _ = await get_transactions(
            session, test_user.id, account_id=cc_account.id,
            bill_id=bill.id,
            from_date=date(2026, 3, 17), to_date=date(2026, 4, 16),
            accounting_mode="cash",
        )
        assert outside.id not in {t.id for t in txs}

    @pytest.mark.asyncio
    async def test_bill_id_filter_includes_pending_tx_with_bill_id_set(
        self, session, test_user, cc_account
    ):
        """The pending-sync exclusion only applies when bill_id IS NULL.
        A pending tx that Pluggy already tagged with a billId is still
        bank-truth and must count — otherwise we'd lose pending charges
        that the bank has already classified."""
        from app.services.transaction_service import get_transactions
        from app.models.credit_card_bill import CreditCardBill
        from datetime import datetime, timezone

        bill = CreditCardBill(
            user_id=test_user.id, account_id=cc_account.id,
            external_id="bill-pending-tagged", due_date=date(2026, 4, 16),
            total_amount=Decimal("100"), currency="BRL",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(bill)
        await session.flush()

        # Pluggy tagged a pending tx — bill_id is set even though status is pending
        tx = await _make_tx(
            session, test_user.id, cc_account.id,
            date(2026, 4, 8), Decimal("50"),
            effective_date=date(2026, 4, 16),
            source="sync",
        )
        tx.bill_id = bill.id
        tx.status = "pending"
        await session.commit()

        txs, _ = await get_transactions(
            session, test_user.id, account_id=cc_account.id,
            bill_id=bill.id,
            from_date=date(2026, 3, 17), to_date=date(2026, 4, 16),
            accounting_mode="cash",
        )
        assert tx.id in {t.id for t in txs}

    @pytest.mark.asyncio
    async def test_bill_id_filter_includes_posted_sync_unlinked_in_window(
        self, session, test_user, cc_account
    ):
        """Posted sync tx without billId is the rare case where the provider
        returned the tx but didn't tag a bill. Date-window inclusion is
        reasonable — the user wants to see their charges, and the tx is
        definitively settled. Only pending sync without billId is excluded."""
        from app.services.transaction_service import get_transactions
        from app.models.credit_card_bill import CreditCardBill
        from datetime import datetime, timezone

        bill = CreditCardBill(
            user_id=test_user.id, account_id=cc_account.id,
            external_id="bill-posted-untagged", due_date=date(2026, 4, 16),
            total_amount=Decimal("100"), currency="BRL",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(bill)
        await session.flush()

        tx = await _make_tx(
            session, test_user.id, cc_account.id,
            date(2026, 4, 8), Decimal("50"),
            effective_date=date(2026, 4, 16),
            source="sync",
        )
        tx.status = "posted"
        # bill_id stays None
        await session.commit()

        txs, _ = await get_transactions(
            session, test_user.id, account_id=cc_account.id,
            bill_id=bill.id,
            from_date=date(2026, 3, 17), to_date=date(2026, 4, 16),
            accounting_mode="cash",
        )
        assert tx.id in {t.id for t in txs}

    @pytest.mark.asyncio
    async def test_bill_id_filter_includes_ofx_imported_in_window(
        self, session, test_user, cc_account
    ):
        """An OFX import is a definitive user intent — must count toward the
        bill cycle whose window contains its date. Confirms the pending-sync
        exclusion is narrow (sync+pending only), not blanket source-based."""
        from app.services.transaction_service import get_transactions
        from app.models.credit_card_bill import CreditCardBill
        from datetime import datetime, timezone

        bill = CreditCardBill(
            user_id=test_user.id, account_id=cc_account.id,
            external_id="bill-ofx", due_date=date(2026, 4, 16),
            total_amount=Decimal("100"), currency="BRL",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(bill)
        await session.flush()

        tx = await _make_tx(
            session, test_user.id, cc_account.id,
            date(2026, 4, 8), Decimal("50"),
            effective_date=date(2026, 4, 16),
            source="ofx",
        )
        await session.commit()

        txs, _ = await get_transactions(
            session, test_user.id, account_id=cc_account.id,
            bill_id=bill.id,
            from_date=date(2026, 3, 17), to_date=date(2026, 4, 16),
            accounting_mode="cash",
        )
        assert tx.id in {t.id for t in txs}

    @pytest.mark.asyncio
    async def test_override_to_date_with_no_matching_bill_keeps_bill_id_null(
        self, session, test_user, cc_account
    ):
        """The user can pick any date as effective_bill_date — including one
        that doesn't correspond to a known bill (e.g. a far-future statement
        that hasn't been issued yet). bill_id stays null, effective_date
        follows the override, and the tx is bucketed by override only."""
        from app.services.transaction_service import update_transaction
        from app.schemas.transaction import TransactionUpdate

        tx = await _make_tx(
            session, test_user.id, cc_account.id,
            date(2026, 4, 18), Decimal("55.90"),
            effective_date=date(2026, 5, 16),
            source="manual",
        )
        await session.commit()

        # No bill exists for 2099-01-01 — override still takes effect
        await update_transaction(
            session, tx.id, test_user.id,
            TransactionUpdate(effective_bill_date=date(2099, 1, 1)),
        )
        await session.commit()
        await session.refresh(tx)

        assert tx.effective_bill_date == date(2099, 1, 1)
        assert tx.effective_date == date(2099, 1, 1)
        assert tx.bill_id is None

    @pytest.mark.asyncio
    async def test_credit_tx_with_bill_id_counts_as_income(
        self, session, test_user, cc_account
    ):
        """Refund/return txs are type=credit. Per-bill summary must include
        them in monthly_income when their bill_id matches — otherwise CC
        refunds show up as 0 for the cycle."""
        from app.services.account_service import get_account_summary
        from app.models.credit_card_bill import CreditCardBill
        from datetime import datetime, timezone

        bill = CreditCardBill(
            user_id=test_user.id, account_id=cc_account.id,
            external_id="bill-refund", due_date=date(2026, 4, 16),
            total_amount=Decimal("0"), currency="BRL",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(bill)
        await session.flush()

        refund = await _make_tx(
            session, test_user.id, cc_account.id,
            date(2026, 4, 8), Decimal("80"),
            effective_date=date(2026, 4, 16),
            tx_type="credit",
        )
        refund.bill_id = bill.id
        await session.commit()

        summary = await get_account_summary(
            session, cc_account.id, test_user.id,
            date_from=date(2026, 3, 17), date_to=date(2026, 4, 16),
            bill_id=bill.id,
        )
        assert summary["monthly_income"] == 80.0

    @pytest.mark.asyncio
    async def test_year_boundary_cycle_buckets_correctly(
        self, session, test_user, cc_account
    ):
        """December → January cycle: a tx dated 27/12 with override 5/1 must
        bucket into the January cycle, not December. Catches off-by-month
        bugs in the COALESCE/OR logic at year boundaries."""
        from app.services.transaction_service import get_transactions

        tx = await _make_tx(
            session, test_user.id, cc_account.id,
            date(2025, 12, 27), Decimal("100"),
            effective_date=date(2025, 12, 27),
            source="manual",
        )
        tx.effective_bill_date = date(2026, 1, 5)
        await session.commit()

        # December window: tx must NOT appear (override moved it to Jan)
        dec_txs, _ = await get_transactions(
            session, test_user.id, account_id=cc_account.id,
            from_date=date(2025, 12, 1), to_date=date(2025, 12, 31),
            accounting_mode="cash",
        )
        assert tx.id not in {t.id for t in dec_txs}

        # January window: tx must appear
        jan_txs, _ = await get_transactions(
            session, test_user.id, account_id=cc_account.id,
            from_date=date(2026, 1, 1), to_date=date(2026, 1, 31),
            accounting_mode="cash",
        )
        assert tx.id in {t.id for t in jan_txs}

    @pytest.mark.asyncio
    async def test_summary_excludes_pending_sync_unlinked_in_window(
        self, session, test_user, cc_account
    ):
        """get_account_summary applies the same pending-sync exclusion as
        get_transactions — otherwise the totals card and bar chart would
        sum a tx the transactions list omits."""
        from app.services.account_service import get_account_summary
        from app.models.credit_card_bill import CreditCardBill
        from datetime import datetime, timezone

        bill = CreditCardBill(
            user_id=test_user.id, account_id=cc_account.id,
            external_id="bill-summary", due_date=date(2026, 4, 16),
            total_amount=Decimal("100"), currency="BRL",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(bill)
        await session.flush()

        # Pending sync without billId — must be excluded
        pending = await _make_tx(
            session, test_user.id, cc_account.id,
            date(2026, 4, 8), Decimal("99"),
            effective_date=date(2026, 4, 16),
            source="sync",
        )
        pending.status = "pending"

        # Manual entry in the same window — must be included
        await _make_tx(
            session, test_user.id, cc_account.id,
            date(2026, 4, 9), Decimal("50"),
            effective_date=date(2026, 4, 16),
            source="manual",
        )
        await session.commit()

        summary = await get_account_summary(
            session, cc_account.id, test_user.id,
            date_from=date(2026, 3, 17), date_to=date(2026, 4, 16),
            bill_id=bill.id,
        )
        # Manual 50 counted, pending 99 excluded
        assert summary["monthly_expenses"] == 50.0

    @pytest.mark.asyncio
    async def test_override_unset_falls_back_to_mode_column(
        self, session, test_user, cc_account
    ):
        """Without an override, the configured accounting mode's column
        (date for cash, effective_date for accrual) drives bucketing."""
        from app.services.transaction_service import get_transactions
        await _set_mode(session, "cash")
        tx = await _make_tx(
            session, test_user.id, cc_account.id,
            date(2026, 4, 18), Decimal("55.90"),
            effective_date=date(2026, 5, 22),
        )
        await session.commit()

        # April window: cash mode uses date (Apr 18), should include
        apr_txs, _ = await get_transactions(
            session, test_user.id, account_id=cc_account.id,
            from_date=date(2026, 4, 1), to_date=date(2026, 4, 30),
            accounting_mode="cash",
        )
        assert any(t.id == tx.id for t in apr_txs)
