"""Unit tests for the ledger's rules, below the HTTP layer.

The API tests cover the journeys. These cover the arithmetic and the
boundaries: which state wins when a decision and a fact disagree, where
one aging bucket ends and the next begins, and whether the N:N
allocation table really is N:N — the claim the whole schema rests on.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.invoice import Invoice, InvoiceAllocation
from app.models.transaction import Transaction
from app.models.workspace import Workspace, WorkspaceMember
from app.services import invoice_service as svc

TODAY = date(2026, 8, 26)


def build_invoice(**overrides) -> Invoice:
    """An in-memory invoice. Not persisted: `derive_state` and friends are
    pure functions of the row plus its allocations."""
    invoice = Invoice(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        status="open",
        issue_date=TODAY - timedelta(days=10),
        due_date=TODAY + timedelta(days=10),
        currency="USD",
        total=Decimal("1000.00"),
    )
    for key, value in overrides.items():
        setattr(invoice, key, value)
    if not hasattr(invoice, "allocations") or invoice.allocations is None:
        invoice.allocations = []
    return invoice


def allocation(amount: str) -> InvoiceAllocation:
    return InvoiceAllocation(
        id=uuid.uuid4(),
        amount=Decimal(amount),
        method="manual",
        allocated_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# derive_state — the rule the whole design rests on
# ---------------------------------------------------------------------------
class TestDeriveState:
    def test_stored_decisions_pass_straight_through(self):
        for status in ("draft", "void", "uncollectible"):
            assert svc.derive_state(build_invoice(status=status), TODAY) == status

    def test_a_decision_outranks_a_fact(self):
        """Voided and written-off invoices are never 'overdue' or 'partial'.

        This ordering is the reason the function exists: a stored status
        records what a person decided, and no amount of arithmetic may
        overrule it.
        """
        past_due = build_invoice(
            status="void", due_date=TODAY - timedelta(days=30), allocations=[allocation("400.00")]
        )
        assert svc.derive_state(past_due, TODAY) == "void"

        written_off = build_invoice(
            status="uncollectible",
            due_date=TODAY - timedelta(days=30),
            allocations=[allocation("400.00")],
        )
        assert svc.derive_state(written_off, TODAY) == "uncollectible"

    def test_open_with_nothing_received(self):
        assert svc.derive_state(build_invoice(), TODAY) == "open"

    def test_partial_once_some_money_is_bound(self):
        invoice = build_invoice(allocations=[allocation("250.00")])
        assert svc.derive_state(invoice, TODAY) == "partial"

    def test_paid_when_the_balance_reaches_zero(self):
        invoice = build_invoice(allocations=[allocation("600.00"), allocation("400.00")])
        assert svc.derive_state(invoice, TODAY) == "paid"

    def test_paid_wins_over_overdue(self):
        """A settled invoice is not late, however long it took."""
        invoice = build_invoice(
            due_date=TODAY - timedelta(days=90), allocations=[allocation("1000.00")]
        )
        assert svc.derive_state(invoice, TODAY) == "paid"

    def test_overdue_wins_over_partial(self):
        """A part-paid invoice past its date reads as late, not as partial —
        the thing the user needs to act on is the lateness."""
        invoice = build_invoice(
            due_date=TODAY - timedelta(days=1), allocations=[allocation("500.00")]
        )
        assert svc.derive_state(invoice, TODAY) == "overdue"

    def test_due_today_is_not_yet_overdue(self):
        assert svc.derive_state(build_invoice(due_date=TODAY), TODAY) == "open"

    def test_one_day_past_is_overdue(self):
        invoice = build_invoice(due_date=TODAY - timedelta(days=1))
        assert svc.derive_state(invoice, TODAY) == "overdue"
        assert svc.days_overdue(invoice, TODAY) == 1

    def test_over_allocation_still_reads_paid_rather_than_negative(self):
        """Defence in depth: the service refuses over-allocation, but if a
        row ever got in another way, the state must not become nonsense."""
        invoice = build_invoice(allocations=[allocation("1200.00")])
        assert svc.derive_state(invoice, TODAY) == "paid"
        assert svc.balance(invoice) == Decimal("-200.00")

    def test_days_overdue_is_zero_for_anything_not_overdue(self):
        for invoice in (
            build_invoice(),
            build_invoice(status="void", due_date=TODAY - timedelta(days=5)),
            build_invoice(allocations=[allocation("1000.00")], due_date=TODAY - timedelta(days=5)),
        ):
            assert svc.days_overdue(invoice, TODAY) == 0


class TestBalanceMath:
    def test_sums_every_allocation(self):
        invoice = build_invoice(
            allocations=[allocation("100.00"), allocation("250.50"), allocation("49.50")]
        )
        assert svc.allocated_total(invoice) == Decimal("400.00")
        assert svc.balance(invoice) == Decimal("600.00")

    def test_zero_allocations_leaves_the_whole_total(self):
        assert svc.balance(build_invoice()) == Decimal("1000.00")

    def test_decimal_all_the_way_down(self):
        """Money is never a float here. Three payments of 333.33 against
        1000.00 must leave exactly 0.01, not 0.010000000000047."""
        invoice = build_invoice(
            allocations=[allocation("333.33"), allocation("333.33"), allocation("333.33")]
        )
        assert svc.balance(invoice) == Decimal("0.01")
        assert isinstance(svc.balance(invoice), Decimal)


# ---------------------------------------------------------------------------
# Presets and settings
# ---------------------------------------------------------------------------
class TestPresets:
    def test_tracking_opens_immediately_with_no_document(self):
        preset = svc.PRESETS["tracking"]
        assert preset["initial_state"] == "open"
        assert preset["document_required"] is False
        assert preset["tax_fields"] == "hidden"

    def test_document_starts_as_a_draft_and_needs_lines(self):
        preset = svc.PRESETS["document"]
        assert preset["initial_state"] == "draft"
        assert preset["document_required"] is True
        # At least optional: an EU B2B invoice without discriminated VAT
        # is not a valid invoice, so the fields have to be reachable.
        assert preset["tax_fields"] == "optional"

    def test_only_two_presets_exist(self):
        assert set(svc.PRESETS) == {"tracking", "document"}


# ---------------------------------------------------------------------------
# Persistence-level behaviour
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def workspace(session: AsyncSession, test_user) -> Workspace:
    ws = Workspace(id=uuid.uuid4(), name="PJ", kind="business", created_by_user_id=test_user.id)
    session.add(ws)
    await session.flush()
    session.add(
        WorkspaceMember(id=uuid.uuid4(), workspace_id=ws.id, user_id=test_user.id, role="owner")
    )
    await session.commit()
    return ws


@pytest_asyncio.fixture
async def credit(session: AsyncSession, workspace, test_user) -> Transaction:
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=workspace.id,
        name="Conta", type="checking", currency="USD", balance=Decimal("0"),
    )
    session.add(account)
    await session.flush()
    tx = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=workspace.id,
        account_id=account.id, description="PAYOUT", amount=Decimal("2000.00"),
        currency="USD", date=TODAY, type="credit", source="manual",
    )
    session.add(tx)
    await session.commit()
    return tx


async def create(session, workspace, user, **data):
    payload = {"total": Decimal("1000.00"), "due_date": TODAY + timedelta(days=10)}
    payload.update(data)
    invoice = await svc.create_invoice(session, workspace.id, user.id, payload)
    await session.commit()
    return invoice


@pytest.mark.asyncio
class TestAllocationTable:
    async def test_one_transaction_settles_several_invoices(
        self, session: AsyncSession, workspace, test_user, credit
    ):
        """The N:N claim, exercised.

        A gateway payout is one bank credit covering many invoices. A
        foreign key on the transaction could not express this, which is
        why the relationship lives in its own table with an amount.
        """
        first = await create(session, workspace, test_user, total=Decimal("800.00"))
        second = await create(session, workspace, test_user, total=Decimal("1200.00"))

        await svc.allocate(session, first, credit.id, Decimal("800.00"))
        await svc.allocate(session, second, credit.id, Decimal("1200.00"))
        await session.commit()

        assert svc.derive_state(first, TODAY) == "paid"
        assert svc.derive_state(second, TODAY) == "paid"
        # Same transaction, two invoices, two rows.
        assert first.allocations[0].transaction_id == credit.id
        assert second.allocations[0].transaction_id == credit.id

    async def test_several_transactions_settle_one_invoice(
        self, session: AsyncSession, workspace, test_user, credit
    ):
        """And the other direction: a Pix-first market pays in parts."""
        account_id = credit.account_id
        second_credit = Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=workspace.id,
            account_id=account_id, description="PIX 2", amount=Decimal("400.00"),
            currency="USD", date=TODAY, type="credit", source="manual",
        )
        session.add(second_credit)
        await session.commit()

        invoice = await create(session, workspace, test_user, total=Decimal("1000.00"))
        await svc.allocate(session, invoice, credit.id, Decimal("600.00"))
        assert svc.derive_state(invoice, TODAY) == "partial"
        await svc.allocate(session, invoice, second_credit.id, Decimal("400.00"))
        await session.commit()
        assert svc.derive_state(invoice, TODAY) == "paid"
        assert len(invoice.allocations) == 2

    async def test_default_amount_applies_as_much_as_fits(
        self, session: AsyncSession, workspace, test_user, credit
    ):
        """One payment closing one invoice must not require typing the
        number twice — and a larger inflow must not over-apply."""
        invoice = await create(session, workspace, test_user, total=Decimal("500.00"))
        await svc.allocate(session, invoice, credit.id)  # credit carries 2000
        await session.commit()
        assert invoice.allocations[0].amount == Decimal("500.00")
        assert svc.balance(invoice) == Decimal("0.00")

    async def test_unallocating_restores_the_balance(
        self, session: AsyncSession, workspace, test_user, credit
    ):
        invoice = await create(session, workspace, test_user)
        await svc.allocate(session, invoice, credit.id, Decimal("400.00"))
        await session.commit()
        await svc.unallocate(session, invoice, invoice.allocations[0].id)
        await session.commit()
        assert svc.balance(invoice) == Decimal("1000.00")
        assert svc.derive_state(invoice, TODAY) == "open"


@pytest.mark.asyncio
class TestNumbering:
    async def test_the_counter_never_moves_backwards(
        self, session: AsyncSession, workspace, test_user
    ):
        """A voided invoice keeps its number and the next one moves on.

        Reusing it would put two different documents under one
        identifier — forbidden where numbering is regulated, and
        confusing everywhere else.
        """
        first = await create(session, workspace, test_user)
        await svc.void_invoice(session, first)
        await session.commit()
        assert first.number == 1
        assert first.status == "void"

        second = await create(session, workspace, test_user)
        assert second.number == 2

    async def test_drafts_consume_no_number(self, session: AsyncSession, workspace, test_user):
        await svc.update_settings(session, workspace.id, {"preset": "document"})
        await session.commit()
        draft = await create(
            session, workspace, test_user,
            lines=[{"description": "Work", "quantity": 1, "unit_price": 900}],
        )
        assert draft.status == "draft"
        assert draft.number is None

        await svc.issue_invoice(session, draft)
        await session.commit()
        assert draft.number == 1


@pytest.mark.asyncio
class TestSnapshot:
    async def test_freezes_issuer_and_labels(self, session: AsyncSession, workspace, test_user):
        await svc.update_settings(
            session, workspace.id,
            {"issuer_display_name": "Alpha ME", "logo_url": "https://x/logo.png",
             "template": {"labels": {"quantity": "Hours"}}},
        )
        await session.commit()
        invoice = await create(session, workspace, test_user)
        assert invoice.snapshot["issuer"]["display_name"] == "Alpha ME"
        assert invoice.snapshot["template"]["labels"]["quantity"] == "Hours"

        # Changing settings afterwards must not rewrite the document.
        await svc.update_settings(session, workspace.id, {"issuer_display_name": "Beta LTDA"})
        await session.commit()
        await session.refresh(invoice)
        assert invoice.snapshot["issuer"]["display_name"] == "Alpha ME"

    async def test_records_the_absence_of_a_prefix_as_a_real_answer(
        self, session: AsyncSession, workspace, test_user
    ):
        """The snapshot says "no prefix", not "unknown".

        The frontend relies on this to keep an invoice issued as "1"
        reading as "1" after someone later sets a prefix.
        """
        invoice = await create(session, workspace, test_user)
        assert "number_prefix" in invoice.snapshot
        assert invoice.snapshot["number_prefix"] is None


@pytest.mark.asyncio
class TestAgingBuckets:
    async def test_bucket_boundaries(self, session: AsyncSession, workspace, test_user):
        """30/31 and 60/61 are where money moves bucket. Off-by-one here
        misreports every aging report the accountant reads."""
        cases = [
            (0, "current"),    # due today
            (1, "d1_30"),
            (30, "d1_30"),
            (31, "d31_60"),
            (60, "d31_60"),
            (61, "d61_90"),
            (90, "d61_90"),
            (91, "d90_plus"),
        ]
        for days_late, _ in cases:
            await create(
                session, workspace, test_user,
                total=Decimal("100.00"),
                issue_date=TODAY - timedelta(days=days_late + 30),
                due_date=TODAY - timedelta(days=days_late),
            )
        summary = await svc.aging_summary(session, workspace.id, TODAY)
        counts = {}
        for _, bucket in cases:
            counts[bucket] = counts.get(bucket, 0) + 1
        for bucket, n in counts.items():
            assert summary["buckets"][bucket] == Decimal("100.00") * n, bucket

    async def test_drafts_and_terminal_states_are_excluded(
        self, session: AsyncSession, workspace, test_user
    ):
        live = await create(session, workspace, test_user, total=Decimal("500.00"))
        voided = await create(session, workspace, test_user, total=Decimal("700.00"))
        await svc.void_invoice(session, voided)
        written_off = await create(session, workspace, test_user, total=Decimal("300.00"))
        await svc.mark_uncollectible(session, written_off)
        await session.commit()

        summary = await svc.aging_summary(session, workspace.id, TODAY)
        assert summary["outstanding"] == Decimal("500.00")
        assert live.id in {i.id for i in summary["upcoming"]}


@pytest.mark.asyncio
class TestCompetenceDate:
    async def test_defaults_to_the_issue_date(self, session: AsyncSession, workspace, test_user):
        invoice = await create(session, workspace, test_user)
        assert invoice.competence_date == invoice.issue_date

    async def test_can_diverge_for_work_delivered_earlier(
        self, session: AsyncSession, workspace, test_user
    ):
        """Delivered in July, invoiced in August: accrual accounting books
        July. Both dates are stored so both reports are possible."""
        delivered = date(2026, 7, 31)
        invoice = await create(session, workspace, test_user, competence_date=delivered)
        assert invoice.competence_date == delivered
        assert invoice.issue_date != delivered


@pytest.mark.asyncio
class TestAllocationProvenance:
    """`method` records who or what decided, and it is not an enum.

    The ids come from the reconciliation policy — a document the user will
    eventually edit — so closing this set would mean a migration every time
    somebody adds a strategy.
    """

    async def test_a_strategy_id_round_trips(
        self, session: AsyncSession, workspace, test_user, credit
    ):
        # The longest id in the shipped policy is 30 characters, which the
        # column was originally too narrow to hold.
        strategy = "same_client_net_of_withholding"
        assert len(strategy) > 20

        invoice = await create(session, workspace, test_user)
        await svc.allocate(session, invoice, credit.id, Decimal("100.00"), method=strategy)
        await session.commit()
        await session.refresh(invoice, ["allocations"])
        assert invoice.allocations[0].method == strategy

    async def test_manual_is_the_default(
        self, session: AsyncSession, workspace, test_user, credit
    ):
        from app.models.invoice import MANUAL_METHOD

        invoice = await create(session, workspace, test_user)
        await svc.allocate(session, invoice, credit.id, Decimal("100.00"))
        await session.commit()
        await session.refresh(invoice, ["allocations"])
        assert invoice.allocations[0].method == MANUAL_METHOD

    async def test_an_automatic_decision_is_not_a_trusted_one(
        self, session: AsyncSession, workspace, test_user, credit
    ):
        """Every guard runs regardless of who decided. A matcher that could
        over-allocate by claiming to be automatic would be a matcher that
        can corrupt the ledger."""
        invoice = await create(session, workspace, test_user, total=Decimal("100.00"))
        with pytest.raises(svc.InvoiceError) as exc:
            await svc.allocate(
                session, invoice, credit.id, Decimal("500.00"), method="same_client_exact"
            )
        assert exc.value.code == "over_allocation"
