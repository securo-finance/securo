"""Two ways one invoice's money is not one amount on one date.

Installments: the schedule the client agreed to. Nothing is allocated to
an installment; settled money covers the schedule first-to-last when
read, so the tests here are mostly about that reading: which
installment is late, when the invoice as a whole is late, and what the
forecast promises on which date.

Deductions: debt closed without money. The one rule to pin down is the
split between *settled* and *received*: a deduction moves the balance
and the state, never `amount_paid` or "received this month".
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.invoice import Invoice
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.models.workspace import WorkspaceMember
from app.services import invoice_forecast_service as forecast
from app.services import invoice_service as svc
from app.services.invoice_service import InvoiceError

TODAY = date(2026, 9, 24)
ISSUE = date(2026, 9, 1)


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
async def biz_headers(auth_headers, business_ws) -> dict:
    return {**auth_headers, "X-Workspace-Id": business_ws["id"]}


@pytest_asyncio.fixture
async def account(session: AsyncSession, ws_id, test_user) -> Account:
    acc = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=ws_id, name="PJ", type="checking",
        currency="USD", balance=Decimal("0"),
    )
    session.add(acc)
    await session.commit()
    return acc


async def credit(session, ws_id, user_id, account, amount: str, on: date = TODAY) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(), user_id=user_id, workspace_id=ws_id, account_id=account.id,
        description="PIX", amount=Decimal(amount), currency="USD", date=on, type="credit", source="manual",
    )
    session.add(tx)
    await session.commit()
    return tx


THREE = [
    {"due_date": date(2026, 10, 1), "amount": "1000.00", "label": "1/3"},
    {"due_date": date(2026, 11, 1), "amount": "1000.00", "label": "2/3"},
    {"due_date": date(2026, 12, 1), "amount": "1000.00", "label": "3/3"},
]


async def an_invoice(session, ws_id, user_id, **overrides) -> Invoice:
    data = {"total": Decimal("3000.00"), "issue_date": ISSUE, "due_date": date(2026, 10, 1), "currency": "USD"}
    data.update(overrides)
    invoice = await svc.create_invoice(session, ws_id, user_id, data)
    await session.commit()
    return invoice


# ---------------------------------------------------------------------------
# Installments
# ---------------------------------------------------------------------------
class TestInstallments:
    @pytest.mark.asyncio
    async def test_the_schedule_is_written_sorted_and_sets_the_due_date(self, session, ws_id, test_user):
        inv = await an_invoice(session, ws_id, test_user.id, installments=list(reversed(THREE)))
        assert [(i.position, i.label, i.due_date) for i in inv.installments] == [
            (0, "1/3", date(2026, 10, 1)), (1, "2/3", date(2026, 11, 1)), (2, "3/3", date(2026, 12, 1)),
        ]
        # The invoice's own due date is the last of them, so "due" keeps
        # meaning "when is the last of it owed" in every list.
        assert inv.due_date == date(2026, 12, 1)
        assert svc.first_unpaid_due(inv) == date(2026, 10, 1)

    @pytest.mark.asyncio
    async def test_the_schedule_must_add_up_and_make_sense(self, session, ws_id, test_user):
        for rows, code in (
            ([THREE[0], {**THREE[1], "amount": "999.00"}, THREE[2]], "installments_mismatch"),
            ([THREE[0]], "installments_too_few"),
            ([THREE[0], {**THREE[1], "amount": "0"}, {**THREE[2], "amount": "2000"}], "installment_amount_not_positive"),
            ([THREE[0], {**THREE[1], "due_date": date(2026, 8, 1)}, THREE[2]], "due_before_issue"),
        ):
            with pytest.raises(InvoiceError) as exc:
                await an_invoice(session, ws_id, test_user.id, installments=rows)
            assert exc.value.code == code

    @pytest.mark.asyncio
    async def test_money_covers_the_schedule_first_to_last(self, session, ws_id, test_user, account):
        """One transfer of 1,500 pays the first installment and half of the
        second. Nobody had to say which."""
        inv = await an_invoice(session, ws_id, test_user.id, installments=THREE)
        tx = await credit(session, ws_id, test_user.id, account, "1500.00")
        await svc.allocate(session, inv, tx.id)
        states = svc.installment_states(inv, today=date(2026, 10, 15))
        assert [(s["state"], s["settled"]) for s in states] == [
            ("paid", Decimal("1000.00")), ("partial", Decimal("500.00")), ("open", Decimal("0")),
        ]
        assert svc.first_unpaid_due(inv) == date(2026, 11, 1)
        # Not late on October 15: the first one is paid and the second is
        # not due yet. The whole invoice reads as partial.
        assert svc.derive_state(inv, date(2026, 10, 15)) == "partial"
        assert svc.days_overdue(inv, date(2026, 10, 15)) == 0

    @pytest.mark.asyncio
    async def test_late_means_the_first_unpaid_installment_is_late(self, session, ws_id, test_user, account):
        inv = await an_invoice(session, ws_id, test_user.id, installments=THREE)
        # October 2: the first installment was due yesterday. Late by one
        # day, even though the invoice's own due date is December.
        assert svc.derive_state(inv, date(2026, 10, 2)) == "overdue"
        assert svc.days_overdue(inv, date(2026, 10, 2)) == 1
        states = svc.installment_states(inv, today=date(2026, 10, 2))
        assert [s["state"] for s in states] == ["overdue", "open", "open"]
        # Paying it stops the clock until November.
        tx = await credit(session, ws_id, test_user.id, account, "1000.00")
        await svc.allocate(session, inv, tx.id)
        assert svc.derive_state(inv, date(2026, 10, 2)) == "partial"
        assert svc.derive_state(inv, date(2026, 11, 2)) == "overdue"
        assert svc.days_overdue(inv, date(2026, 11, 2)) == 1

    @pytest.mark.asyncio
    async def test_paid_in_full_reads_paid_whatever_the_dates(self, session, ws_id, test_user, account):
        inv = await an_invoice(session, ws_id, test_user.id, installments=THREE)
        tx = await credit(session, ws_id, test_user.id, account, "3000.00")
        await svc.allocate(session, inv, tx.id)
        assert svc.derive_state(inv, date(2027, 6, 1)) == "paid"
        assert svc.first_unpaid_due(inv) is None
        assert [s["state"] for s in svc.installment_states(inv)] == ["paid"] * 3

    @pytest.mark.asyncio
    async def test_a_draft_edit_keeps_the_schedule_honest(self, session, ws_id, test_user):
        inv = await an_invoice(session, ws_id, test_user.id, as_draft=True, installments=THREE)
        assert inv.status == "draft"
        # A new total without a new schedule is refused: the old one no
        # longer adds up and nobody said what the split is now.
        with pytest.raises(InvoiceError) as exc:
            await svc.update_invoice(session, inv, {"total": Decimal("4000.00")})
        assert exc.value.code == "installments_mismatch"
        await svc.update_invoice(
            session, inv,
            {"total": Decimal("4000.00"),
             "installments": [{"due_date": date(2026, 10, 1), "amount": "2000"}, {"due_date": date(2026, 12, 1), "amount": "2000"}]},
        )
        assert [i.amount for i in inv.installments] == [Decimal("2000.00"), Decimal("2000.00")]
        assert inv.due_date == date(2026, 12, 1)
        # An empty list clears it, and the due date is the caller's again.
        await svc.update_invoice(session, inv, {"installments": [], "due_date": date(2026, 10, 20)})
        assert inv.installments == [] and inv.due_date == date(2026, 10, 20)

    @pytest.mark.asyncio
    async def test_an_issued_invoice_keeps_its_schedule(self, session, ws_id, test_user):
        inv = await an_invoice(session, ws_id, test_user.id, installments=THREE)
        assert inv.status == "open"
        with pytest.raises(InvoiceError) as exc:
            await svc.update_invoice(session, inv, {"installments": []})
        assert exc.value.code == "issued_invoice_immutable"

    @pytest.mark.asyncio
    async def test_the_forecast_promises_each_installment_on_its_date(self, session, ws_id, test_user, account):
        inv = await an_invoice(session, ws_id, test_user.id, installments=THREE)
        window = (date(2026, 9, 1), date(2027, 1, 1))
        claims = sorted((c.due_date, c.amount) for c in await forecast.claims_in_range(session, ws_id, *window))
        assert claims == [(date(2026, 10, 1), Decimal("1000.00")), (date(2026, 11, 1), Decimal("1000.00")), (date(2026, 12, 1), Decimal("1000.00"))]
        # 1,500 received: the first is gone, the second is half, the third whole.
        tx = await credit(session, ws_id, test_user.id, account, "1500.00")
        await svc.allocate(session, inv, tx.id)
        await session.commit()
        claims = sorted((c.due_date, c.amount) for c in await forecast.claims_in_range(session, ws_id, *window))
        assert claims == [(date(2026, 11, 1), Decimal("500.00")), (date(2026, 12, 1), Decimal("1000.00"))]
        # A window that only holds the middle date holds only the middle claim.
        claims = await forecast.claims_in_range(session, ws_id, date(2026, 10, 15), date(2026, 11, 15))
        assert [(c.due_date, c.amount) for c in claims] == [(date(2026, 11, 1), Decimal("500.00"))]

    @pytest.mark.asyncio
    async def test_aging_reads_the_late_installment_not_the_last_date(self, session, ws_id, test_user):
        """December's invoice, first installment a week late: the whole
        balance is 1 to 30 days overdue, not "current"."""
        await an_invoice(session, ws_id, test_user.id, installments=THREE)
        summary = await svc.aging_summary(session, ws_id, today=date(2026, 10, 8))
        assert summary["overdue_count"] == 1
        assert summary["overdue_amount"] == Decimal("3000.00")
        assert summary["buckets"]["d1_30"] == Decimal("3000.00")


# ---------------------------------------------------------------------------
# Deductions
# ---------------------------------------------------------------------------
class TestDeductions:
    @pytest.mark.asyncio
    async def test_a_deduction_settles_without_being_received(self, session, ws_id, test_user, account):
        """The client paid 2,850 and withheld 150 of tax. The invoice is
        paid; what arrived is 2,850; the accountant can see the 150."""
        inv = await an_invoice(session, ws_id, test_user.id)
        tx = await credit(session, ws_id, test_user.id, account, "2850.00")
        allocated = await svc.allocate(session, inv, tx.id)
        # `allocated_at` defaults to the wall clock while the summary below
        # asks about TODAY. Left to the clock, the row falls outside the
        # window the moment the real date passes TODAY, and the test starts
        # failing on a day that has nothing to do with deductions.
        allocated.allocated_at = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
        assert svc.derive_state(inv, TODAY) == "partial"
        await svc.deduct(session, inv, "withholding_tax", Decimal("150"), tax_kind=" IRRF ", note="1.5%", transaction_id=tx.id)
        await session.commit()
        assert svc.derive_state(inv, TODAY) == "paid"
        assert svc.allocated_total(inv) == Decimal("2850.00")
        assert svc.deducted_total(inv) == Decimal("150.00")
        assert svc.balance(inv) == Decimal("0.00")
        assert inv.deductions[0].tax_kind == "irrf"
        summary = await svc.aging_summary(session, ws_id, today=TODAY)
        assert summary["received_this_month"] == Decimal("2850.00")
        assert summary["outstanding"] == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_guards(self, session, ws_id, test_user):
        inv = await an_invoice(session, ws_id, test_user.id)
        cases: list[tuple[str, Decimal, uuid.UUID | None, str]] = [
            ("tip", Decimal("1"), None, "invalid_deduction_kind"),
            ("gateway_fee", Decimal("0"), None, "amount_not_positive"),
            ("gateway_fee", Decimal("3000.01"), None, "over_allocation"),
            ("gateway_fee", Decimal("1"), uuid.uuid4(), "transaction_not_found"),
        ]
        for kind, amount, transaction_id, code in cases:
            with pytest.raises(InvoiceError) as exc:
                await svc.deduct(session, inv, kind, amount, transaction_id=transaction_id)
            assert exc.value.code == code
        draft = await an_invoice(session, ws_id, test_user.id, as_draft=True)
        with pytest.raises(InvoiceError) as exc:
            await svc.deduct(session, draft, "gateway_fee", Decimal("1"))
        assert exc.value.code == "not_open"

    @pytest.mark.asyncio
    async def test_removing_a_deduction_reopens_the_balance_and_void_waits_for_it(self, session, ws_id, test_user):
        inv = await an_invoice(session, ws_id, test_user.id)
        d = await svc.deduct(session, inv, "gateway_fee", Decimal("3000"))
        assert svc.derive_state(inv, TODAY) == "paid"
        with pytest.raises(InvoiceError) as exc:
            await svc.void_invoice(session, inv)
        assert exc.value.code == "void_with_allocations"
        await svc.undeduct(session, inv, d.id)
        assert svc.balance(inv) == Decimal("3000.00")
        with pytest.raises(InvoiceError) as exc:
            await svc.undeduct(session, inv, d.id)
        assert exc.value.code == "deduction_not_found"
        await svc.void_invoice(session, inv)
        assert inv.status == "void"

    @pytest.mark.asyncio
    async def test_a_deduction_covers_installments_like_money_does(self, session, ws_id, test_user):
        inv = await an_invoice(session, ws_id, test_user.id, installments=THREE)
        await svc.deduct(session, inv, "other", Decimal("1000"), note="credit agreed by phone")
        assert [s["state"] for s in svc.installment_states(inv, today=date(2026, 10, 2))] == ["paid", "open", "open"]
        assert svc.derive_state(inv, date(2026, 10, 2)) == "partial"
        await session.commit()
        claims = await forecast.claims_in_range(session, ws_id, date(2026, 9, 1), date(2027, 1, 1))
        assert sorted(c.due_date for c in claims) == [date(2026, 11, 1), date(2026, 12, 1)]


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def client_payee(session: AsyncSession, ws_id, test_user) -> Payee:
    payee = Payee(id=uuid.uuid4(), user_id=test_user.id, workspace_id=ws_id, name="Cliente Alpha", source="manual")
    session.add(payee)
    await session.commit()
    return payee


@pytest_asyncio.fixture
async def viewer_headers(session: AsyncSession, client: AsyncClient, business_ws) -> dict:
    import bcrypt

    from app.models.user import User

    user = User(
        id=uuid.uuid4(), email="viewer-installments@example.com",
        hashed_password=bcrypt.hashpw(b"viewerpass123", bcrypt.gensalt()).decode(),
        is_active=True, is_superuser=False, is_verified=True,
    )
    session.add(user)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=uuid.UUID(business_ws["id"]), user_id=user.id, role="viewer"))
    await session.commit()
    resp = await client.post(
        "/api/auth/login",
        data={"username": "viewer-installments@example.com", "password": "viewerpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}", "X-Workspace-Id": business_ws["id"]}


@pytest.mark.asyncio
async def test_installments_and_deductions_over_http(client: AsyncClient, biz_headers, session, ws_id, test_user, account):
    resp = await client.post(
        "/api/invoices", headers=biz_headers,
        json={"total": "3000.00", "issue_date": str(ISSUE), "currency": "USD",
              "installments": [{"due_date": "2026-10-01", "amount": "1500.00", "label": "Upfront"},
                               {"due_date": "2026-12-01", "amount": "1500.00", "label": "On delivery"}]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["due_date"] == "2026-12-01"
    assert body["next_due_date"] == "2026-10-01"
    assert [(i["label"], i["state"], i["settled"]) for i in body["installments"]] == [
        ("Upfront", "open", "0.00"), ("On delivery", "open", "0.00"),
    ]
    invoice_id = body["id"]

    resp = await client.post("/api/invoices", headers=biz_headers, json={"total": "10", "installments": [{"due_date": "2026-10-01", "amount": "5"}, {"due_date": "2026-11-01", "amount": "6"}]})
    assert resp.status_code == 400 and resp.json()["detail"]["code"] == "installments_mismatch"

    # A deduction of 100 closes part of the first installment.
    resp = await client.post(f"/api/invoices/{invoice_id}/deductions", headers=biz_headers, json={"kind": "gateway_fee", "amount": "100.00", "note": "processor fee"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["amount_deducted"] == "100.00" and body["amount_paid"] == "0.00" and body["balance"] == "2900.00"
    assert body["installments"][0]["settled"] == "100.00" and body["installments"][0]["state"] in ("partial", "overdue")
    deduction_id = body["deductions"][0]["id"]
    assert body["deductions"][0]["kind"] == "gateway_fee"
    # The document the client reads carries it too, so its totals add up.
    doc = (await client.get(f"/api/invoices/{invoice_id}/document", headers=biz_headers)).json()
    assert doc["amount_deducted"] == "100.00" and doc["balance"] == "2900.00"
    assert doc["labels"]["deducted"] == "Deduções"  # the workspace writes in Portuguese

    resp = await client.post(f"/api/invoices/{invoice_id}/deductions", headers=biz_headers, json={"kind": "other", "amount": "5000"})
    assert resp.status_code == 400 and resp.json()["detail"]["code"] == "over_allocation"

    resp = await client.delete(f"/api/invoices/{invoice_id}/deductions/{deduction_id}", headers=biz_headers)
    assert resp.status_code == 200 and resp.json()["balance"] == "3000.00" and resp.json()["deductions"] == []
    assert (await client.delete(f"/api/invoices/{invoice_id}/deductions/{uuid.uuid4()}", headers=biz_headers)).status_code == 404


@pytest.mark.asyncio
async def test_gates(client: AsyncClient, biz_headers, viewer_headers, auth_headers):
    created = (await client.post("/api/invoices", headers=biz_headers, json={"total": "10"})).json()
    iid, fake = created["id"], str(uuid.uuid4())
    assert (await client.post(f"/api/invoices/{iid}/deductions", headers=viewer_headers, json={"kind": "other", "amount": "1"})).status_code == 403
    assert (await client.delete(f"/api/invoices/{iid}/deductions/{fake}", headers=viewer_headers)).status_code == 403
    other = await client.post("/api/workspaces", headers=auth_headers, json={"name": "Outra", "kind": "business", "self_membership": True})
    other_headers = {**auth_headers, "X-Workspace-Id": other.json()["id"]}
    assert (await client.post(f"/api/invoices/{iid}/deductions", headers=other_headers, json={"kind": "other", "amount": "1"})).status_code == 404


# ---------------------------------------------------------------------------
# Statement of account: how the invoice arrives at its balance
# ---------------------------------------------------------------------------
def _pdf_text(content: bytes) -> str:
    import io

    import pypdf

    return "\n".join(page.extract_text() for page in pypdf.PdfReader(io.BytesIO(content)).pages)


@pytest.mark.asyncio
async def test_the_statement_walks_from_the_total_to_the_balance(
    client: AsyncClient, biz_headers, session, ws_id, test_user, account
):
    """The invoice PDF is the document as issued and stays so. The
    statement is the one that says what happened since: each payment and
    deduction, then total, paid, deducted and what is left."""
    invoice = (await client.post(
        "/api/invoices", headers=biz_headers,
        json={"total": "3000.00", "issue_date": str(ISSUE), "currency": "USD",
              "installments": [{"due_date": "2026-10-01", "amount": "1500.00"},
                               {"due_date": "2026-12-01", "amount": "1500.00"}]},
    )).json()
    tx = await credit(session, ws_id, test_user.id, account, "1455.00", on=date(2026, 9, 20))
    resp = await client.post(
        f"/api/invoices/{invoice['id']}/allocations", headers=biz_headers,
        json={"transaction_id": str(tx.id)},
    )
    assert resp.status_code in (200, 201), resp.text
    resp = await client.post(
        f"/api/invoices/{invoice['id']}/deductions", headers=biz_headers,
        json={"kind": "withholding_tax", "tax_kind": "irrf", "amount": "45.00", "note": "internal note",
              "transaction_id": str(tx.id)},
    )
    assert resp.status_code == 201, resp.text

    resp = await client.get(f"/api/invoices/{invoice['id']}/statement", headers=biz_headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert "statement" in resp.headers["content-disposition"]
    text = _pdf_text(resp.content)
    # Titled as a statement, in the workspace's language (Portuguese here).
    assert "Extrato da fatura" in text
    # Each movement, named by kind and never by the bank's or our own text.
    assert "Pagamento recebido" in text and "USD 1,455.00" in text
    assert "Imposto retido (IRRF)" in text and "USD 45.00" in text
    assert "internal note" not in text and "PIX" not in text
    # And the walk from the total to the balance.
    assert "Deduções" in text and "USD 1,500.00" in text

    # The invoice itself is still the invoice.
    invoice_pdf = await client.get(f"/api/invoices/{invoice['id']}/pdf", headers=biz_headers)
    assert "Extrato da fatura" not in _pdf_text(invoice_pdf.content)


@pytest.mark.asyncio
async def test_an_untouched_invoice_states_its_whole_balance(client: AsyncClient, biz_headers):
    invoice = (await client.post("/api/invoices", headers=biz_headers, json={"total": "800.00", "currency": "USD"})).json()
    resp = await client.get(f"/api/invoices/{invoice['id']}/statement", headers=biz_headers)
    assert resp.status_code == 200
    text = _pdf_text(resp.content)
    assert "Saldo devedor" in text and "USD 800.00" in text


@pytest.mark.asyncio
async def test_no_statement_for_a_draft_or_a_bill_we_received(client: AsyncClient, biz_headers):
    draft = (await client.post("/api/invoices", headers=biz_headers, json={"total": "10.00", "as_draft": True})).json()
    resp = await client.get(f"/api/invoices/{draft['id']}/statement", headers=biz_headers)
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "statement_of_draft"

    bill = (await client.post("/api/invoices", headers=biz_headers, json={"total": "10.00", "direction": "payable"})).json()
    resp = await client.get(f"/api/invoices/{bill['id']}/statement", headers=biz_headers)
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "statement_not_ours"


@pytest.mark.asyncio
async def test_the_statement_stays_inside_its_workspace(client: AsyncClient, biz_headers, auth_headers):
    invoice = (await client.post("/api/invoices", headers=biz_headers, json={"total": "10.00"})).json()
    other = (await client.post(
        "/api/workspaces", headers=auth_headers,
        json={"name": "Other", "kind": "business", "self_membership": True},
    )).json()
    resp = await client.get(
        f"/api/invoices/{invoice['id']}/statement",
        headers={**auth_headers, "X-Workspace-Id": other["id"]},
    )
    assert resp.status_code == 404
