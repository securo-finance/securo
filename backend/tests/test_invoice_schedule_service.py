"""Recurring invoices, below the HTTP layer.

Three claims the design rests on, each pinned here:

  1. **The schedule is an agreement, never money.** Every figure about
     it (recurring revenue, paid, past due) is computed from invoices.
  2. **A period is billed once.** The cursor, the unique
     `(schedule, sequence)` and the hand-link door all agree on it.
  3. **A term is history once an invoice was issued under it.** Prices
     change from a period boundary forward, never behind.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.invoice import Invoice, InvoiceAllocation, InvoiceInstallment
from app.models.invoice_schedule import InvoiceSchedule, InvoiceScheduleTerm
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.services import invoice_forecast_service as forecast
from app.services import invoice_schedule_service as svc
from app.services import invoice_service
from app.services.invoice_service import InvoiceError

TODAY = date(2026, 9, 22)
LINE = {"description": "Retainer", "quantity": 1, "unit_price": "3000.00"}


def build(**overrides) -> InvoiceSchedule:
    """An in-memory schedule for the pure calendar and term functions."""
    schedule = InvoiceSchedule(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        name="Plano",
        status="active",
        origin="local",
        frequency="monthly",
        start_date=date(2026, 1, 5),
        end_type="never",
        currency="USD",
        next_sequence=1,
        consecutive_failures=0,
    )
    schedule.terms = []
    for key, value in overrides.items():
        setattr(schedule, key, value)
    return schedule


def term(effective_from: date, total: str, frequency_schedule=None) -> InvoiceScheduleTerm:
    return InvoiceScheduleTerm(
        id=uuid.uuid4(),
        effective_from=effective_from,
        lines=[{"description": "x", "quantity": "1", "unit_price": total}],
        discount=Decimal("0"),
        subtotal=Decimal(total),
        tax_total=Decimal("0"),
        total=Decimal(total),
    )


# ---------------------------------------------------------------------------
# The calendar: pure functions of the anchor and the frequency
# ---------------------------------------------------------------------------
class TestCalendar:
    def test_periods_count_from_the_anchor(self):
        s = build(start_date=date(2026, 1, 5))
        assert svc.period_start(s, 1) == date(2026, 1, 5)
        assert svc.period_start(s, 2) == date(2026, 2, 5)
        assert svc.period_start(s, 13) == date(2027, 1, 5)

    def test_sequence_is_one_based(self):
        with pytest.raises(ValueError):
            svc.period_start(build(), 0)

    def test_short_months_clamp_and_recover(self):
        """Anchored on the 31st: February clamps, March is the 31st again.
        Not the 28th forever, which is what a naive `+1 month` does."""
        s = build(start_date=date(2026, 1, 31))
        assert svc.period_start(s, 2) == date(2026, 2, 28)
        assert svc.period_start(s, 3) == date(2026, 3, 31)
        assert svc.period_start(s, 4) == date(2026, 4, 30)

    def test_every_frequency_advances_as_named(self):
        anchor = date(2026, 1, 5)
        expected = {
            "weekly": date(2026, 1, 12),
            "biweekly": date(2026, 1, 19),
            "monthly": date(2026, 2, 5),
            "quarterly": date(2026, 4, 5),
            "semiannual": date(2026, 7, 5),
            "yearly": date(2027, 1, 5),
        }
        for frequency, second in expected.items():
            assert svc.period_start(build(frequency=frequency, start_date=anchor), 2) == second

    def test_yearly_on_leap_day_clamps(self):
        s = build(frequency="yearly", start_date=date(2028, 2, 29))
        assert svc.period_start(s, 2) == date(2029, 2, 28)
        assert svc.period_start(s, 5) == date(2032, 2, 29)

    def test_bounds_are_inclusive_and_contiguous(self):
        s = build(start_date=date(2026, 1, 5))
        start, end = svc.period_bounds(s, 1)
        assert (start, end) == (date(2026, 1, 5), date(2026, 2, 4))
        next_start, _ = svc.period_bounds(s, 2)
        assert next_start == end + timedelta(days=1)

    def test_first_sequence_on_or_after(self):
        s = build(start_date=date(2026, 1, 5))
        assert svc.first_sequence_on_or_after(s, date(2025, 12, 1)) == 1
        assert svc.first_sequence_on_or_after(s, date(2026, 1, 5)) == 1
        assert svc.first_sequence_on_or_after(s, date(2026, 1, 6)) == 2
        assert svc.first_sequence_on_or_after(s, date(2026, 9, 22)) == 10  # Oct 5

    def test_sequence_for_period_start_accepts_boundaries_only(self):
        s = build(start_date=date(2026, 1, 5))
        assert svc.sequence_for_period_start(s, date(2026, 3, 5)) == 3
        assert svc.sequence_for_period_start(s, date(2026, 3, 6)) is None
        assert svc.sequence_for_period_start(s, date(2025, 12, 5)) is None

    def test_end_conditions(self):
        assert svc.period_within_end(build(end_type="never"), 10_000)
        counted = build(end_type="after_count", end_count=3)
        assert svc.period_within_end(counted, 3)
        assert not svc.period_within_end(counted, 4)
        dated = build(end_type="on_date", end_date=date(2026, 3, 5))
        assert svc.period_within_end(dated, 3)  # starts Mar 5, on the date itself
        assert not svc.period_within_end(dated, 4)

    def test_next_period_start_is_none_once_over(self):
        s = build(end_type="after_count", end_count=2, next_sequence=3)
        assert svc.next_period_start(s) is None
        assert svc.next_period_start(build(next_sequence=3)) == date(2026, 3, 5)
        assert svc.next_period_start(build(status="ended")) is None


# ---------------------------------------------------------------------------
# Terms: the price over time
# ---------------------------------------------------------------------------
class TestTerms:
    def test_latest_term_on_or_before_the_date_wins(self):
        s = build(terms=[term(date(2026, 1, 5), "3000"), term(date(2027, 1, 1), "3500")])
        before = svc.term_for(s, date(2026, 12, 31))
        after = svc.term_for(s, date(2027, 1, 1))
        assert before is not None and before.total == Decimal("3000")
        assert after is not None and after.total == Decimal("3500")
        assert svc.term_for(s, date(2025, 6, 1)) is None

    def test_monthly_amount_normalises_through_the_year(self):
        """52 weeks in 12 months, not 4 in one: the 8% that a naive x4 loses."""
        t = term(date(2026, 1, 5), "1200")
        expected = {
            "weekly": Decimal("5200.00"),
            "biweekly": Decimal("2600.00"),
            "monthly": Decimal("1200.00"),
            "quarterly": Decimal("400.00"),
            "semiannual": Decimal("200.00"),
            "yearly": Decimal("100.00"),
        }
        for frequency, monthly in expected.items():
            assert svc.monthly_amount(build(frequency=frequency), t) == monthly
        assert svc.monthly_amount(build(), None) == Decimal("0")

    def test_totals_follow_the_invoice_arithmetic(self):
        lines = [
            {"description": "a", "quantity": "2", "unit_price": "100.00", "tax_rate": "10"},
            {"description": "b", "quantity": "1", "unit_price": "50.00"},
        ]
        subtotal, tax, total = svc._term_totals(lines, Decimal("25"))
        assert (subtotal, tax, total) == (Decimal("250.00"), Decimal("20.00"), Decimal("245.00"))

    def test_discount_beyond_the_lines_is_refused(self):
        with pytest.raises(InvoiceError) as exc:
            svc._term_totals([{"description": "a", "unit_price": "10"}], Decimal("11"))
        assert exc.value.code == "discount_exceeds_total"

    def test_a_term_must_bill_something(self):
        t = InvoiceScheduleTerm()
        with pytest.raises(InvoiceError) as exc:
            svc._fill_term(t, [{"description": "free", "unit_price": "0"}], None)
        assert exc.value.code == "term_empty"
        with pytest.raises(InvoiceError) as exc:
            svc._fill_term(t, [], None)
        assert exc.value.code == "term_lines_required"


# ---------------------------------------------------------------------------
# Against the database
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def business_ws(client: AsyncClient, auth_headers) -> dict:
    resp = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Estudio", "kind": "business", "self_membership": True},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest_asyncio.fixture
async def ws_id(business_ws) -> uuid.UUID:
    return uuid.UUID(business_ws["id"])


@pytest_asyncio.fixture
async def client_payee(session: AsyncSession, ws_id, test_user) -> Payee:
    payee = Payee(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=ws_id, name="Cliente Alpha", source="manual"
    )
    session.add(payee)
    await session.commit()
    return payee


async def make_schedule(session, ws_id, user_id, today=TODAY, **overrides) -> InvoiceSchedule:
    data = {
        "name": "Retainer",
        "frequency": "monthly",
        "start_date": date(2026, 9, 5),
        "currency": "USD",
        "lines": [dict(LINE)],
    }
    data.update(overrides)
    schedule = await svc.create_schedule(session, ws_id, user_id, data, today=today)
    await session.commit()
    return schedule


async def invoices_of(session, schedule_id) -> list[Invoice]:
    result = await session.execute(
        select(Invoice).where(Invoice.schedule_id == schedule_id).order_by(Invoice.sequence)
    )
    return list(result.unique().scalars().all())


class TestCreate:
    @pytest.mark.asyncio
    async def test_creates_with_its_first_term_from_the_anchor(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 1))
        assert s.status == "active"
        assert len(s.terms) == 1
        assert s.terms[0].effective_from == date(2026, 10, 1)
        assert s.terms[0].total == Decimal("3000.00")
        assert s.next_sequence == 1

    @pytest.mark.asyncio
    async def test_a_start_in_the_past_does_not_back_fill(self, session, ws_id, test_user):
        """Anchored in March, created in September: the cursor sits on
        the first period from today, and March to September are history
        the user links by hand if they want them."""
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 3, 5))
        assert s.next_sequence == 8  # Oct 5
        assert svc.next_period_start(s) == date(2026, 10, 5)

    @pytest.mark.asyncio
    async def test_end_conditions_are_validated(self, session, ws_id, test_user):
        with pytest.raises(InvoiceError) as exc:
            await make_schedule(session, ws_id, test_user.id, end_type="on_date")
        assert exc.value.code == "end_date_required"
        with pytest.raises(InvoiceError) as exc:
            await make_schedule(session, ws_id, test_user.id, end_type="after_count", end_count=0)
        assert exc.value.code == "end_count_required"
        with pytest.raises(InvoiceError) as exc:
            await make_schedule(
                session, ws_id, test_user.id, end_type="on_date", end_date=date(2026, 1, 1)
            )
        assert exc.value.code == "end_before_start"

    @pytest.mark.asyncio
    async def test_imported_needs_a_source_and_converges_on_it(self, session, ws_id, test_user):
        with pytest.raises(InvoiceError) as exc:
            await make_schedule(session, ws_id, test_user.id, origin="imported")
        assert exc.value.code == "external_source_required"
        s = await make_schedule(
            session, ws_id, test_user.id, origin="imported", external_source="gateway", external_id="sub_1"
        )
        found = await svc.find_by_external_id(session, ws_id, "gateway", "sub_1")
        assert found is not None and found.id == s.id

    @pytest.mark.asyncio
    async def test_payee_must_belong_to_the_workspace(self, session, ws_id, test_user):
        with pytest.raises(InvoiceError) as exc:
            await make_schedule(session, ws_id, test_user.id, payee_id=uuid.uuid4())
        assert exc.value.code == "payee_not_found"


class TestGeneration:
    @pytest.mark.asyncio
    async def test_emits_the_period_once_its_date_has_come(self, session, ws_id, test_user, client_payee):
        s = await make_schedule(
            session, ws_id, test_user.id, start_date=date(2026, 10, 5), payee_id=client_payee.id,
            payment_terms_days=10,
        )
        assert await svc.generate_due(session, s, today=date(2026, 10, 4)) == []
        emitted = await svc.generate_due(session, s, today=date(2026, 10, 5))
        await session.commit()
        assert len(emitted) == 1
        inv = emitted[0]
        assert inv.schedule_id == s.id
        assert inv.sequence == 1
        assert (inv.period_start, inv.period_end) == (date(2026, 10, 5), date(2026, 11, 4))
        assert inv.issue_date == date(2026, 10, 5)
        assert inv.competence_date == date(2026, 10, 5)
        assert inv.due_date == date(2026, 10, 15)
        assert inv.payee_id == client_payee.id
        assert inv.currency == "USD"
        assert inv.total == Decimal("3000.00")
        assert [line.description for line in inv.lines] == ["Retainer"]
        assert inv.status == "open"  # the business workspace opens on creation
        assert inv.number is not None
        assert s.next_sequence == 2
        assert s.last_generated_at is not None

    @pytest.mark.asyncio
    async def test_a_second_run_is_a_no_op(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        await svc.generate_due(session, s, today=date(2026, 10, 5))
        assert await svc.generate_due(session, s, today=date(2026, 10, 5)) == []
        assert await svc.generate_due(session, s, today=date(2026, 10, 20)) == []
        assert len(await invoices_of(session, s.id)) == 1

    @pytest.mark.asyncio
    async def test_catches_up_every_period_the_worker_missed(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        emitted = await svc.generate_due(session, s, today=date(2027, 1, 10))
        assert [i.sequence for i in emitted] == [1, 2, 3, 4]
        assert [i.issue_date for i in emitted] == [
            date(2026, 10, 5), date(2026, 11, 5), date(2026, 12, 5), date(2027, 1, 5)
        ]
        assert s.next_sequence == 5

    @pytest.mark.asyncio
    async def test_catch_up_is_capped_per_run(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, frequency="weekly", start_date=date(2026, 10, 5))
        first = await svc.generate_due(session, s, today=date(2027, 10, 5))
        assert len(first) == svc.MAX_PERIODS_PER_RUN
        second = await svc.generate_due(session, s, today=date(2027, 10, 5))
        assert len(second) == svc.MAX_PERIODS_PER_RUN
        assert second[0].sequence == svc.MAX_PERIODS_PER_RUN + 1

    @pytest.mark.asyncio
    async def test_force_next_bills_ahead_of_the_date_once(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 12, 1))
        emitted = await svc.generate_due(session, s, today=TODAY, force_next=True)
        assert [i.sequence for i in emitted] == [1]
        assert emitted[0].issue_date == date(2026, 12, 1)
        assert s.next_sequence == 2
        # And only once: the next period is still in the future.
        assert await svc.generate_due(session, s, today=TODAY) == []

    @pytest.mark.asyncio
    async def test_a_period_already_linked_by_hand_is_skipped(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        manual = await invoice_service.create_invoice(
            session, ws_id, test_user.id,
            {"total": Decimal("3000.00"), "issue_date": date(2026, 10, 5), "due_date": date(2026, 10, 20)},
        )
        await svc.link_invoice(session, s, manual, date(2026, 10, 5))
        assert s.next_sequence == 2
        emitted = await svc.generate_due(session, s, today=date(2026, 11, 5))
        assert [i.sequence for i in emitted] == [2]
        assert len(await invoices_of(session, s.id)) == 2

    @pytest.mark.asyncio
    async def test_after_count_ends_as_completed_on_the_last_invoice(self, session, ws_id, test_user):
        s = await make_schedule(
            session, ws_id, test_user.id, start_date=date(2026, 10, 5), end_type="after_count", end_count=3
        )
        emitted = await svc.generate_due(session, s, today=date(2027, 6, 1))
        assert [i.sequence for i in emitted] == [1, 2, 3]
        assert s.status == "ended"
        assert s.end_reason == "completed"
        assert s.ended_at == date(2027, 6, 1)
        assert await svc.generate_due(session, s, today=date(2027, 7, 1)) == []

    @pytest.mark.asyncio
    async def test_on_date_stops_at_the_date(self, session, ws_id, test_user):
        s = await make_schedule(
            session, ws_id, test_user.id, start_date=date(2026, 10, 5),
            end_type="on_date", end_date=date(2026, 12, 5),
        )
        emitted = await svc.generate_due(session, s, today=date(2027, 3, 1))
        assert [i.issue_date for i in emitted] == [date(2026, 10, 5), date(2026, 11, 5), date(2026, 12, 5)]
        assert s.status == "ended"

    @pytest.mark.asyncio
    async def test_the_price_changes_at_the_term_boundary_and_not_before(self, session, ws_id, test_user):
        """A raise recorded in October for January: October to December
        bill the old price, January the new one, nobody had to remember."""
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        await svc.add_term(
            session, s, effective_from=date(2027, 1, 1),
            lines=[{"description": "Retainer", "unit_price": "3500.00"}],
        )
        emitted = await svc.generate_due(session, s, today=date(2027, 2, 10))
        assert [(i.issue_date, i.total) for i in emitted] == [
            (date(2026, 10, 5), Decimal("3000.00")),
            (date(2026, 11, 5), Decimal("3000.00")),
            (date(2026, 12, 5), Decimal("3000.00")),
            (date(2027, 1, 5), Decimal("3500.00")),
            (date(2027, 2, 5), Decimal("3500.00")),
        ]

    @pytest.mark.asyncio
    async def test_payment_terms_fall_back_to_the_workspace_default(self, session, ws_id, test_user):
        settings = await invoice_service.get_settings(session, ws_id)
        settings.default_payment_terms_days = 7
        await session.commit()
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        [inv] = await svc.generate_due(session, s, today=date(2026, 10, 5))
        assert inv.due_date == date(2026, 10, 12)

    @pytest.mark.asyncio
    async def test_document_preset_emits_drafts(self, session, ws_id, test_user):
        """The generated invoice obeys the workspace's initial state, like
        one written by hand: a document workspace reviews before sending."""
        await invoice_service.update_settings(session, ws_id, {"preset": "document"})
        await session.commit()
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        [inv] = await svc.generate_due(session, s, today=date(2026, 10, 5))
        assert inv.status == "draft"
        assert inv.number is None

    @pytest.mark.asyncio
    async def test_paused_ended_and_imported_emit_nothing(self, session, ws_id, test_user):
        paused = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        await svc.pause_schedule(session, paused)
        ended = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        await svc.end_schedule(session, ended, reason="canceled_by_client", today=TODAY)
        imported = await make_schedule(
            session, ws_id, test_user.id, start_date=date(2026, 10, 5),
            origin="imported", external_source="gateway", external_id="sub_x",
        )
        for s in (paused, ended, imported):
            assert await svc.generate_due(session, s, today=date(2027, 1, 1)) == []

    @pytest.mark.asyncio
    async def test_three_failures_pause_the_schedule(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        with patch.object(svc, "_emit", side_effect=RuntimeError("storage down")):
            for attempt in range(1, 4):
                with pytest.raises(RuntimeError):
                    await svc.generate_due(session, s, today=date(2026, 10, 5))
                assert s.consecutive_failures == attempt
        assert s.status == "paused"
        assert s.pause_reason == "failures"
        assert s.next_sequence == 1  # nothing was skipped by failing

    @pytest.mark.asyncio
    async def test_a_success_resets_the_failure_count(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        with patch.object(svc, "_emit", side_effect=RuntimeError("once")):
            with pytest.raises(RuntimeError):
                await svc.generate_due(session, s, today=date(2026, 10, 5))
        assert s.consecutive_failures == 1
        await svc.generate_due(session, s, today=date(2026, 10, 5))
        assert s.consecutive_failures == 0
        assert s.status == "active"

    @pytest.mark.asyncio
    async def test_the_unique_index_is_the_last_line_of_defence(self, session, ws_id, test_user):
        """Two invoices for one period cannot exist, whatever the code does."""
        from sqlalchemy.exc import IntegrityError

        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        [first] = await svc.generate_due(session, s, today=date(2026, 10, 5))
        await session.commit()
        dup = await invoice_service.create_invoice(
            session, ws_id, test_user.id, {"total": Decimal("1.00"), "issue_date": date(2026, 10, 5)}
        )
        dup.schedule_id = s.id
        dup.sequence = first.sequence
        dup.period_start, dup.period_end = first.period_start, first.period_end
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


class TestTermRules:
    @pytest.mark.asyncio
    async def test_a_future_term_can_be_added_changed_and_removed(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        t = await svc.add_term(
            session, s, effective_from=date(2027, 1, 1), lines=[{"description": "R", "unit_price": "3500"}]
        )
        assert t.total == Decimal("3500.00")
        await svc.update_term(session, s, t, lines=[{"description": "R", "unit_price": "3600"}])
        assert t.total == Decimal("3600.00")
        await svc.update_term(session, s, t, effective_from=date(2027, 2, 1))
        assert t.effective_from == date(2027, 2, 1)
        await svc.delete_term(session, s, t)
        assert len(s.terms) == 1

    @pytest.mark.asyncio
    async def test_adding_on_an_existing_date_replaces_that_term(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        await svc.add_term(session, s, effective_from=date(2027, 1, 1), lines=[{"description": "R", "unit_price": "3500"}])
        await svc.add_term(session, s, effective_from=date(2027, 1, 1), lines=[{"description": "R", "unit_price": "3600"}])
        assert len(s.terms) == 2
        january = svc.term_for(s, date(2027, 1, 1))
        assert january is not None and january.total == Decimal("3600.00")

    @pytest.mark.asyncio
    async def test_a_term_an_invoice_was_issued_under_is_history(self, session, ws_id, test_user):
        """The retroactivity rule. Once January was billed, nothing may
        change what January cost; a raise goes on a later date."""
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        await svc.generate_due(session, s, today=date(2026, 11, 5))  # periods 1 and 2

        for effective_from in (date(2026, 10, 5), date(2026, 11, 1), date(2026, 11, 5)):
            with pytest.raises(InvoiceError) as exc:
                await svc.add_term(session, s, effective_from=effective_from, lines=[{"description": "R", "unit_price": "1"}])
            assert exc.value.code == "term_in_use"

        first = s.terms[0]
        with pytest.raises(InvoiceError) as exc:
            await svc.update_term(session, s, first, lines=[{"description": "R", "unit_price": "1"}])
        assert exc.value.code == "term_in_use"
        with pytest.raises(InvoiceError) as exc:
            await svc.delete_term(session, s, first)
        assert exc.value.code in ("term_in_use", "last_term")

        # The day after the last billed period start is free.
        t = await svc.add_term(session, s, effective_from=date(2026, 11, 6), lines=[{"description": "R", "unit_price": "3500"}])
        [december] = await svc.generate_due(session, s, today=date(2026, 12, 5))
        assert december.total == Decimal("3500.00")
        assert december.period_start is not None
        governing = svc.term_for(s, december.period_start)
        assert governing is not None and governing.id == t.id

    @pytest.mark.asyncio
    async def test_the_last_term_cannot_go_and_none_may_precede_the_start(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        with pytest.raises(InvoiceError) as exc:
            await svc.delete_term(session, s, s.terms[0])
        assert exc.value.code == "last_term"
        with pytest.raises(InvoiceError) as exc:
            await svc.add_term(session, s, effective_from=date(2026, 10, 4), lines=[dict(LINE)])
        assert exc.value.code == "term_before_start"

    @pytest.mark.asyncio
    async def test_an_ended_agreement_takes_no_terms(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id)
        await svc.end_schedule(session, s, reason="other", today=TODAY)
        with pytest.raises(InvoiceError) as exc:
            await svc.add_term(session, s, effective_from=date(2027, 1, 1), lines=[dict(LINE)])
        assert exc.value.code == "schedule_ended"


async def an_invoice(session, ws_id, user_id, **overrides) -> Invoice:
    """Straight into the service, so money is `Decimal` as the API layer
    would have made it, and the due date follows a moved issue date."""
    data: dict[str, object] = {"total": Decimal("3000.00"), "issue_date": date(2026, 9, 5)}
    data.update(overrides)
    issue = data["issue_date"]
    assert isinstance(issue, date)
    data.setdefault("due_date", issue + timedelta(days=15))
    for key in ("total", "discount"):
        value = data.get(key)
        if isinstance(value, str):
            data[key] = Decimal(value)
    invoice = await invoice_service.create_invoice(session, ws_id, user_id, data)
    await session.commit()
    return invoice


class TestLinking:
    @pytest.mark.asyncio
    async def test_links_on_a_boundary_and_labels_the_period(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 3, 5))
        inv = await an_invoice(session, ws_id, test_user.id, issue_date=date(2026, 6, 7))
        await svc.link_invoice(session, s, inv, date(2026, 6, 5))
        assert inv.sequence == 4
        assert (inv.period_start, inv.period_end) == (date(2026, 6, 5), date(2026, 7, 4))
        # A past period does not move the cursor back or forward.
        assert s.next_sequence == 8

    @pytest.mark.asyncio
    async def test_linking_at_or_past_the_cursor_moves_it(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 3, 5))
        inv = await an_invoice(session, ws_id, test_user.id, issue_date=date(2026, 10, 5))
        await svc.link_invoice(session, s, inv, date(2026, 10, 5))
        assert s.next_sequence == 9  # Nov 5: October was billed by hand

    @pytest.mark.asyncio
    async def test_refusals(self, session, ws_id, test_user, client_payee):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 3, 5), payee_id=client_payee.id)

        inside = await an_invoice(session, ws_id, test_user.id)
        with pytest.raises(InvoiceError) as exc:
            await svc.link_invoice(session, s, inside, date(2026, 6, 6))
        assert exc.value.code == "not_a_period"

        euro = await an_invoice(session, ws_id, test_user.id, currency="EUR")
        with pytest.raises(InvoiceError) as exc:
            await svc.link_invoice(session, s, euro, date(2026, 6, 5))
        assert exc.value.code == "currency_mismatch"

        other = Payee(id=uuid.uuid4(), user_id=test_user.id, workspace_id=ws_id, name="Beta", source="manual")
        session.add(other)
        await session.flush()
        someone_else = await an_invoice(session, ws_id, test_user.id, payee_id=other.id)
        with pytest.raises(InvoiceError) as exc:
            await svc.link_invoice(session, s, someone_else, date(2026, 6, 5))
        assert exc.value.code == "payee_mismatch"

        voided = await an_invoice(session, ws_id, test_user.id)
        await invoice_service.void_invoice(session, voided)
        with pytest.raises(InvoiceError) as exc:
            await svc.link_invoice(session, s, voided, date(2026, 6, 5))
        assert exc.value.code == "invoice_void"

        ok = await an_invoice(session, ws_id, test_user.id)
        await svc.link_invoice(session, s, ok, date(2026, 6, 5))
        again = await an_invoice(session, ws_id, test_user.id)
        with pytest.raises(InvoiceError) as exc:
            await svc.link_invoice(session, s, again, date(2026, 6, 5))
        assert exc.value.code == "period_taken"
        with pytest.raises(InvoiceError) as exc:
            await svc.link_invoice(session, s, ok, date(2026, 7, 5))
        assert exc.value.code == "already_linked"

        capped = await make_schedule(
            session, ws_id, test_user.id, start_date=date(2026, 3, 5), end_type="after_count", end_count=2
        )
        with pytest.raises(InvoiceError) as exc:
            await svc.link_invoice(session, capped, again, date(2026, 6, 5))
        assert exc.value.code == "beyond_end"

    @pytest.mark.asyncio
    async def test_an_invoice_never_crosses_a_workspace(self, session, ws_id, test_user, client, auth_headers):
        resp = await client.post(
            "/api/workspaces", headers=auth_headers,
            json={"name": "Outra", "kind": "business", "self_membership": True},
        )
        other_ws = uuid.UUID(resp.json()["id"])
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 3, 5))
        foreign = await an_invoice(session, other_ws, test_user.id)
        with pytest.raises(InvoiceError) as exc:
            await svc.link_invoice(session, s, foreign, date(2026, 6, 5))
        assert exc.value.code == "invoice_not_found"

    @pytest.mark.asyncio
    async def test_unlink_frees_the_period_and_leaves_the_cursor(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        [inv] = await svc.generate_due(session, s, today=date(2026, 10, 5))
        await svc.unlink_invoice(session, inv)
        assert inv.schedule_id is None and inv.sequence is None and inv.period_start is None
        assert s.next_sequence == 2
        assert await svc.generate_due(session, s, today=date(2026, 10, 5)) == []
        replacement = await an_invoice(session, ws_id, test_user.id, issue_date=date(2026, 10, 5))
        await svc.link_invoice(session, s, replacement, date(2026, 10, 5))
        assert replacement.sequence == 1

    @pytest.mark.asyncio
    async def test_make_recurring_from_an_invoice_with_lines(self, session, ws_id, test_user, client_payee):
        inv = await an_invoice(
            session, ws_id, test_user.id, payee_id=client_payee.id, issue_date=date(2026, 9, 5),
            due_date=date(2026, 9, 25), total=None, discount="100.00",
            lines=[
                {"description": "Consultoria", "quantity": 10, "unit": "h", "unit_price": "200.00", "tax_rate": "5"},
            ],
        )
        s = await svc.make_recurring(session, inv, test_user.id, {"frequency": "monthly"}, today=TODAY)
        assert s.name == "Consultoria"
        assert s.payee_id == client_payee.id
        assert s.start_date == date(2026, 9, 5)
        assert s.payment_terms_days == 20
        assert s.terms[0].total == inv.total
        assert s.terms[0].lines[0]["unit"] == "h"
        assert inv.schedule_id == s.id and inv.sequence == 1
        assert s.next_sequence == 2
        assert svc.next_period_start(s) == date(2026, 10, 5)

    @pytest.mark.asyncio
    async def test_make_recurring_from_a_three_field_invoice(self, session, ws_id, test_user):
        inv = await an_invoice(session, ws_id, test_user.id, total="1500.00")
        s = await svc.make_recurring(
            session, inv, test_user.id, {"frequency": "quarterly", "name": "Hosting"}, today=TODAY
        )
        assert s.terms[0].lines == [
            {
                "description": "Hosting", "quantity": "1", "unit": None, "unit_price": "1500.00",
                "tax_rate": None, "product_id": None, "price_id": None, "fiscal_refs": None,
            }
        ]
        assert s.terms[0].total == Decimal("1500.00")

    @pytest.mark.asyncio
    async def test_make_recurring_with_an_earlier_anchor_leaves_room_for_history(self, session, ws_id, test_user):
        """"This has been going since March": the invoice in hand becomes
        period seven, March to August are free to link, and the cursor
        still sits on the next period from today."""
        inv = await an_invoice(session, ws_id, test_user.id, issue_date=date(2026, 9, 5))
        s = await svc.make_recurring(
            session, inv, test_user.id,
            {"frequency": "monthly", "name": "Retainer", "start_date": date(2026, 3, 5)}, today=TODAY,
        )
        assert s.start_date == date(2026, 3, 5)
        assert inv.sequence == 7
        assert s.next_sequence == 8
        march = await an_invoice(session, ws_id, test_user.id, issue_date=date(2026, 3, 5))
        await svc.link_invoice(session, s, march, date(2026, 3, 5))
        assert march.sequence == 1

        off = await an_invoice(session, ws_id, test_user.id, issue_date=date(2026, 9, 6))
        with pytest.raises(InvoiceError) as exc:
            await svc.make_recurring(
                session, off, test_user.id,
                {"frequency": "monthly", "name": "R", "start_date": date(2026, 3, 5)}, today=TODAY,
            )
        assert exc.value.code == "not_a_period"
        with pytest.raises(InvoiceError) as exc:
            await svc.make_recurring(
                session, off, test_user.id,
                {"frequency": "monthly", "name": "R", "start_date": date(2026, 10, 1)}, today=TODAY,
            )
        assert exc.value.code == "start_after_invoice"

    @pytest.mark.asyncio
    async def test_make_recurring_needs_an_issued_unlinked_invoice(self, session, ws_id, test_user):
        draft = await an_invoice(session, ws_id, test_user.id, as_draft=True)
        with pytest.raises(InvoiceError) as exc:
            await svc.make_recurring(session, draft, test_user.id, {"frequency": "monthly"})
        assert exc.value.code == "not_issued"
        inv = await an_invoice(session, ws_id, test_user.id)
        # No lines, no client, no name given: nothing to call it.
        with pytest.raises(InvoiceError) as exc:
            await svc.make_recurring(session, inv, test_user.id, {"frequency": "monthly"}, today=TODAY)
        assert exc.value.code == "name_required"
        await svc.make_recurring(session, inv, test_user.id, {"frequency": "monthly", "name": "Hosting"}, today=TODAY)
        with pytest.raises(InvoiceError) as exc:
            await svc.make_recurring(session, inv, test_user.id, {"frequency": "monthly", "name": "Hosting"}, today=TODAY)
        assert exc.value.code == "already_linked"

    @pytest.mark.asyncio
    async def test_delete_refuses_once_anything_was_billed(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        await svc.generate_due(session, s, today=date(2026, 10, 5))
        with pytest.raises(InvoiceError) as exc:
            await svc.delete_schedule(session, s)
        assert exc.value.code == "schedule_has_invoices"
        empty = await make_schedule(session, ws_id, test_user.id)
        await svc.delete_schedule(session, empty)
        assert await svc.get_schedule(session, empty.id, ws_id) is None


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_pause_then_resume_skips_the_gap(self, session, ws_id, test_user):
        """A pause is "stop billing". Resuming does not bill the months
        that fell while paused."""
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        await svc.generate_due(session, s, today=date(2026, 10, 5))
        await svc.pause_schedule(session, s)
        assert s.pause_reason == "manual"
        await svc.resume_schedule(session, s, today=date(2027, 1, 20))
        assert s.status == "active" and s.pause_reason is None
        assert s.next_sequence == 5  # Feb 5; Nov, Dec, Jan are not billed
        assert await svc.generate_due(session, s, today=date(2027, 1, 20)) == []

    @pytest.mark.asyncio
    async def test_resuming_past_the_end_completes_it(self, session, ws_id, test_user):
        s = await make_schedule(
            session, ws_id, test_user.id, start_date=date(2026, 10, 5), end_type="on_date", end_date=date(2026, 12, 31)
        )
        await svc.pause_schedule(session, s)
        await svc.resume_schedule(session, s, today=date(2027, 3, 1))
        assert s.status == "ended" and s.end_reason == "completed"

    @pytest.mark.asyncio
    async def test_end_records_when_and_why(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 3, 5))
        await svc.end_schedule(session, s, reason="canceled_by_client", ended_at=date(2026, 9, 1), today=TODAY)
        assert (s.status, s.ended_at, s.end_reason) == ("ended", date(2026, 9, 1), "canceled_by_client")
        with pytest.raises(InvoiceError):
            await svc.resume_schedule(session, s)
        with pytest.raises(InvoiceError):
            await svc.pause_schedule(session, s)
        with pytest.raises(InvoiceError):
            await svc.update_schedule(session, s, {"name": "x"})
        with pytest.raises(InvoiceError) as exc:
            await svc.end_schedule(session, await make_schedule(session, ws_id, test_user.id), reason="nope")
        assert exc.value.code == "invalid_end_reason"

    @pytest.mark.asyncio
    async def test_calendar_is_free_until_the_first_invoice(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        await svc.update_schedule(session, s, {"frequency": "weekly", "start_date": date(2026, 10, 1)}, today=TODAY)
        assert (s.frequency, s.start_date, s.next_sequence) == ("weekly", date(2026, 10, 1), 1)
        assert s.terms[0].effective_from == date(2026, 10, 1)  # the first term moved with the anchor
        await svc.generate_due(session, s, today=date(2026, 10, 1))
        for change in ({"frequency": "monthly"}, {"start_date": date(2026, 10, 2)}, {"currency": "EUR"}):
            with pytest.raises(InvoiceError) as exc:
                await svc.update_schedule(session, s, change, today=TODAY)
            assert exc.value.code == "schedule_locked"
        # The same value is not a change.
        await svc.update_schedule(session, s, {"frequency": "weekly", "currency": "usd"}, today=TODAY)

    @pytest.mark.asyncio
    async def test_editable_fields_and_an_end_already_behind_the_cursor(self, session, ws_id, test_user, client_payee):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        await svc.generate_due(session, s, today=date(2026, 12, 5))  # 1..3, cursor 4
        await svc.update_schedule(
            session, s,
            {"name": "  Plano Pro ", "payee_id": client_payee.id, "payment_terms_days": 5, "notes": "n"},
            today=date(2026, 12, 5),
        )
        assert (s.name, s.payee_id, s.payment_terms_days, s.notes) == ("Plano Pro", client_payee.id, 5, "n")
        await svc.update_schedule(session, s, {"end_type": "after_count", "end_count": 3}, today=date(2026, 12, 5))
        assert s.status == "ended" and s.end_reason == "completed"


class TestFigures:
    @pytest.mark.asyncio
    async def test_figures_come_from_the_invoices(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5), payment_terms_days=10)
        emitted = await svc.generate_due(session, s, today=date(2026, 12, 5))  # 3 invoices of 3000
        account = Account(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=ws_id, name="PJ", type="checking",
            currency="USD", balance=Decimal("0"),
        )
        session.add(account)
        await session.flush()
        tx = Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=ws_id, account_id=account.id,
            description="PIX", amount=Decimal("3000.00"), currency="USD", date=date(2026, 10, 10),
            type="credit", source="manual",
        )
        session.add(tx)
        await session.flush()
        session.add(InvoiceAllocation(
            invoice_id=emitted[0].id, workspace_id=ws_id, transaction_id=tx.id, amount=Decimal("3000.00"),
        ))
        await invoice_service.void_invoice(session, emitted[2])
        await session.commit()

        figures = (await svc.figures_for(session, ws_id, [s.id], today=date(2026, 12, 1)))[s.id]
        assert figures.invoice_count == 3
        assert figures.amount_invoiced == Decimal("6000.00")  # the voided one reads as zero
        assert figures.amount_paid == Decimal("3000.00")
        assert figures.past_due_count == 1  # November, due Nov 15, unpaid

    @pytest.mark.asyncio
    async def test_past_due_reads_the_first_unpaid_installment(self, session, ws_id, test_user):
        """An invoice's own due date is the last installment. A client who
        is late on the first one is late, whatever the last one says."""
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5), payment_terms_days=10)
        (invoice,) = await svc.generate_due(session, s, today=date(2026, 10, 6))  # 3000, due Oct 15
        invoice.due_date = date(2026, 12, 20)
        session.add_all([
            InvoiceInstallment(
                invoice_id=invoice.id, workspace_id=ws_id, position=0,
                due_date=date(2026, 10, 10), amount=Decimal("1000.00"),
            ),
            InvoiceInstallment(
                invoice_id=invoice.id, workspace_id=ws_id, position=1,
                due_date=date(2026, 12, 20), amount=Decimal("2000.00"),
            ),
        ])
        await session.commit()

        today = date(2026, 12, 1)
        figures = (await svc.figures_for(session, ws_id, [s.id], today=today))[s.id]
        assert figures.past_due_count == 1  # the first installment, not the invoice date

        account = Account(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=ws_id, name="PJ", type="checking",
            currency="USD", balance=Decimal("0"),
        )
        session.add(account)
        await session.flush()
        tx = Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=ws_id, account_id=account.id,
            description="PIX", amount=Decimal("1000.00"), currency="USD", date=date(2026, 10, 12),
            type="credit", source="manual",
        )
        session.add(tx)
        await session.flush()
        session.add(InvoiceAllocation(
            invoice_id=invoice.id, workspace_id=ws_id, transaction_id=tx.id, amount=Decimal("1000.00"),
        ))
        await session.commit()

        figures = (await svc.figures_for(session, ws_id, [s.id], today=today))[s.id]
        assert figures.past_due_count == 0  # the late one is covered, the next is not due
        assert figures.amount_paid == Decimal("1000.00")
        summary = await svc.summary(session, ws_id, today=today)
        assert all(c.past_due_count == 0 for c in summary.by_currency)

    @pytest.mark.asyncio
    async def test_summary_is_per_currency_and_counts_churn(self, session, ws_id, test_user):
        usd = await make_schedule(session, ws_id, test_user.id, frequency="monthly")
        quarterly = await make_schedule(
            session, ws_id, test_user.id, frequency="quarterly",
            lines=[{"description": "Q", "unit_price": "900.00"}],
        )
        eur = await make_schedule(session, ws_id, test_user.id, currency="EUR", lines=[{"description": "E", "unit_price": "100.00"}])
        paused = await make_schedule(session, ws_id, test_user.id)
        await svc.pause_schedule(session, paused)
        churned = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 1, 5))
        await svc.end_schedule(session, churned, reason="canceled_by_client", ended_at=TODAY - timedelta(days=10), today=TODAY)
        long_gone = await make_schedule(session, ws_id, test_user.id, start_date=date(2025, 1, 5))
        await svc.end_schedule(session, long_gone, reason="unpaid", ended_at=TODAY - timedelta(days=90), today=TODAY)
        await session.commit()

        summary = await svc.summary(session, ws_id, today=TODAY)
        assert (summary.active_count, summary.paused_count, summary.ended_count) == (3, 1, 2)
        by = {c.currency: c for c in summary.by_currency}
        assert by["USD"].monthly_recurring == Decimal("3300.00")  # 3000 + 900/3
        assert by["USD"].active_count == 2
        assert by["USD"].ended_recently_count == 1
        assert by["USD"].monthly_lost == Decimal("3000.00")
        assert by["EUR"].monthly_recurring == Decimal("100.00")
        assert usd.id and quarterly.id and eur.id


class TestForecast:
    @pytest.mark.asyncio
    async def test_projected_periods_replace_nothing_and_are_replaced_by_the_invoice(self, session, ws_id, test_user):
        """The no-double-count rule, one level up: a period is projected
        until its invoice exists, and then the invoice is what is projected."""
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5), payment_terms_days=10)
        window = (date(2026, 10, 1), date(2027, 1, 1))
        claims = await forecast.claims_in_range(session, ws_id, *window)
        assert sorted((c.due_date, c.amount) for c in claims) == [
            (date(2026, 10, 15), Decimal("3000.00")),
            (date(2026, 11, 15), Decimal("3000.00")),
            (date(2026, 12, 15), Decimal("3000.00")),
        ]
        assert all(c.direction == "receivable" and c.currency == "USD" for c in claims)

        await svc.generate_due(session, s, today=date(2026, 10, 5))
        await session.commit()
        claims = await forecast.claims_in_range(session, ws_id, *window)
        assert len(claims) == 3  # still three: October is now the invoice, not the projection
        assert sum(c.amount for c in claims) == Decimal("9000.00")

    @pytest.mark.asyncio
    async def test_projection_honours_the_end_the_pause_and_the_terms(self, session, ws_id, test_user):
        s = await make_schedule(
            session, ws_id, test_user.id, start_date=date(2026, 10, 5), payment_terms_days=0,
            end_type="after_count", end_count=2,
        )
        await svc.add_term(session, s, effective_from=date(2026, 11, 1), lines=[{"description": "R", "unit_price": "10.00"}])
        projected = await svc.projected_periods(session, ws_id, date(2026, 1, 1), date(2028, 1, 1))
        assert [(p.due_date, p.amount) for p in projected] == [
            (date(2026, 10, 5), Decimal("3000.00")),
            (date(2026, 11, 5), Decimal("10.00")),
        ]
        await svc.pause_schedule(session, s)
        assert await svc.projected_periods(session, ws_id, date(2026, 1, 1), date(2028, 1, 1)) == []

    @pytest.mark.asyncio
    async def test_payables_ask_for_no_projection(self, session, ws_id, test_user):
        await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        claims = await forecast.claims_in_range(
            session, ws_id, date(2026, 1, 1), date(2028, 1, 1), directions=("payable",)
        )
        assert claims == []


class TestWorkerTask:
    """The Celery task's own promise: one agreement failing never stops
    the others, and the failure count survives the rollback."""

    @pytest.mark.asyncio
    async def test_one_run_emits_for_every_due_schedule(self, session, ws_id, test_user, monkeypatch):
        from tests.conftest import TestSessionLocal
        from app.tasks import invoice_schedule_tasks as task

        a = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 1, 5), today=date(2026, 1, 1))
        b = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 1, 5), today=date(2026, 1, 1))
        monkeypatch.setattr(svc, "_today", lambda: date(2026, 1, 5))
        ids = await svc.due_schedule_ids(session)
        assert {a.id, b.id} <= set(ids)
        total = 0
        for schedule_id in (a.id, b.id):
            total += await task._generate_one(TestSessionLocal, schedule_id)
        assert total == 2
        assert len(await invoices_of(session, a.id)) == 1
        assert len(await invoices_of(session, b.id)) == 1

    @pytest.mark.asyncio
    async def test_a_failure_is_counted_and_committed_without_the_invoice(self, session, ws_id, test_user, monkeypatch):
        from tests.conftest import TestSessionLocal
        from app.tasks import invoice_schedule_tasks as task

        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 1, 5), today=date(2026, 1, 1))
        monkeypatch.setattr(svc, "_today", lambda: date(2026, 1, 5))
        with patch.object(svc, "_emit", side_effect=RuntimeError("boom")):
            for _ in range(3):
                assert await task._generate_one(TestSessionLocal, s.id) == 0
        await session.refresh(s)
        assert s.consecutive_failures == 3
        assert s.status == "paused" and s.pause_reason == "failures"
        assert await invoices_of(session, s.id) == []
        # Once paused it is no longer due, so the next run leaves it alone.
        assert s.id not in await svc.due_schedule_ids(session)

    @pytest.mark.asyncio
    async def test_a_missing_schedule_is_a_no_op(self, session):
        from tests.conftest import TestSessionLocal
        from app.tasks import invoice_schedule_tasks as task

        assert await task._generate_one(TestSessionLocal, uuid.uuid4()) == 0


class TestReviewRegressions:
    """One test per hole found in review, each failing if it reopens."""

    @pytest.mark.asyncio
    async def test_the_first_term_cannot_be_deleted_or_moved(self, session, ws_id, test_user):
        """Deleting or moving the price in force from the start would leave
        the first periods unpriced, and the job would pause the agreement."""
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        first = s.terms[0]
        await svc.add_term(session, s, effective_from=date(2027, 1, 1), lines=[{"description": "R", "unit_price": "3500"}])
        with pytest.raises(InvoiceError) as exc:
            await svc.delete_term(session, s, first)
        assert exc.value.code == "first_term"
        with pytest.raises(InvoiceError) as exc:
            await svc.update_term(session, s, first, effective_from=date(2026, 11, 1))
        assert exc.value.code == "first_term"
        # Its price may still change, and the later term may still go.
        await svc.update_term(session, s, first, lines=[{"description": "R", "unit_price": "3100"}])
        assert first.total == Decimal("3100.00")
        await svc.delete_term(session, s, s.terms[1])
        [october] = await svc.generate_due(session, s, today=date(2026, 10, 5))
        assert october.total == Decimal("3100.00")

    @pytest.mark.asyncio
    async def test_moving_the_start_onto_a_later_term_is_refused_not_a_500(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        await svc.add_term(session, s, effective_from=date(2027, 1, 1), lines=[{"description": "R", "unit_price": "3500"}])
        with pytest.raises(InvoiceError) as exc:
            await svc.update_schedule(session, s, {"start_date": date(2027, 1, 1)}, today=TODAY)
        assert exc.value.code == "term_before_start"

    @pytest.mark.asyncio
    async def test_resuming_after_failures_bills_the_period_that_failed(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        with patch.object(svc, "_emit", side_effect=RuntimeError("storage down")):
            for _ in range(3):
                with pytest.raises(RuntimeError):
                    await svc.generate_due(session, s, today=date(2026, 10, 5))
        assert s.pause_reason == "failures"
        await svc.resume_schedule(session, s, today=date(2026, 10, 10))
        assert s.next_sequence == 1
        [october] = await svc.generate_due(session, s, today=date(2026, 10, 10))
        assert october.period_start == date(2026, 10, 5)

    @pytest.mark.asyncio
    async def test_a_manual_pause_still_skips_the_gap(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))
        await svc.pause_schedule(session, s)
        await svc.resume_schedule(session, s, today=date(2026, 10, 10))
        assert s.next_sequence == 2

    @pytest.mark.asyncio
    async def test_a_payable_never_joins_an_agreement(self, session, ws_id, test_user):
        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 3, 5))
        bill = await an_invoice(session, ws_id, test_user.id, direction="payable", issue_date=date(2026, 6, 5))
        with pytest.raises(InvoiceError) as exc:
            await svc.link_invoice(session, s, bill, date(2026, 6, 5))
        assert exc.value.code == "not_receivable"
        with pytest.raises(InvoiceError) as exc:
            await svc.make_recurring(session, bill, test_user.id, {"frequency": "monthly", "name": "Rent"}, today=TODAY)
        assert exc.value.code == "not_receivable"

    @pytest.mark.asyncio
    async def test_a_period_issued_by_another_run_is_a_409_not_a_failure(self, session, ws_id, test_user):
        """Two runs read the same cursor; the second trips the unique
        (schedule, sequence). That is a no-op, never a counted failure."""
        from sqlalchemy.exc import IntegrityError

        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5))

        async def racing_emit(*_args, **_kwargs):
            raise IntegrityError("INSERT", {}, Exception("uq_invoices_schedule_sequence"))

        with patch.object(svc, "_emit", side_effect=racing_emit):
            with pytest.raises(InvoiceError) as exc:
                await svc.generate_due(session, s, today=date(2026, 10, 5))
        assert exc.value.code == "period_already_issued"
        assert exc.value.status_code == 409
        await session.refresh(s)
        assert s.consecutive_failures == 0
        assert s.status == "active"

    @pytest.mark.asyncio
    async def test_the_worker_treats_the_race_as_nothing_to_do(self, session, ws_id, test_user, monkeypatch):
        from tests.conftest import TestSessionLocal
        from app.tasks import invoice_schedule_tasks as task

        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 1, 5), today=date(2026, 1, 1))
        monkeypatch.setattr(svc, "_today", lambda: date(2026, 1, 5))
        race = InvoiceError("period_already_issued", "raced", status_code=409)
        with patch.object(svc, "generate_due", side_effect=race):
            assert await task._generate_one(TestSessionLocal, s.id) == 0
        await session.refresh(s)
        assert s.consecutive_failures == 0 and s.status == "active"

    @pytest.mark.asyncio
    async def test_a_worker_holding_a_stale_instance_emits_from_the_new_calendar(
        self, session, ws_id, test_user
    ):
        """A worker loads the agreement, a PATCH then moves its start and
        cursor, and only after that does the worker emit. It must emit from
        what was committed, not from what it loaded."""
        from tests.conftest import TestSessionLocal

        s = await make_schedule(session, ws_id, test_user.id, start_date=date(2026, 10, 5), today=date(2026, 10, 1))
        async with TestSessionLocal() as worker:
            stale = await worker.get(InvoiceSchedule, s.id)
            assert stale is not None and stale.start_date == date(2026, 10, 5)

            await svc.update_schedule(session, s, {"start_date": date(2026, 12, 5)}, today=date(2026, 10, 1))
            await session.commit()

            emitted = await svc.generate_due(worker, stale, today=date(2026, 12, 5))
            assert [(i.sequence, i.period_start) for i in emitted] == [(1, date(2026, 12, 5))]
            await worker.commit()
