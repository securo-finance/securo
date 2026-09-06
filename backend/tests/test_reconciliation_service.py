"""Money and promises, brought together against a real database.

The engine's own tests prove what it decides. These prove the half that
loads candidates and writes the result: including the direction that is
easy to forget: an invoice issued *after* the money arrived.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.invoice import Invoice, InvoiceAllocation
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.services import invoice_service, reconciliation_service

TODAY = date.today()


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
async def biz_headers(auth_headers, business_ws) -> dict:
    return {**auth_headers, "X-Workspace-Id": business_ws["id"]}


@pytest_asyncio.fixture
async def account(session: AsyncSession, business_ws, test_user) -> Account:
    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=uuid.UUID(business_ws["id"]),
        name="Conta PJ",
        type="checking",
        currency="USD",
        balance=Decimal("0"),
    )
    session.add(acc)
    await session.commit()
    return acc


@pytest_asyncio.fixture
async def client_payee(session: AsyncSession, business_ws, test_user) -> Payee:
    payee = Payee(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=uuid.UUID(business_ws["id"]),
        name="Alpha Tecnologia",
    )
    session.add(payee)
    await session.commit()
    return payee


async def a_transaction(
    session: AsyncSession,
    account: Account,
    test_user,
    *,
    amount: Decimal = Decimal("3000.00"),
    kind: str = "credit",
    when: date = TODAY,
    description: str = "PIX RECEBIDO ALPHA",
    payee_id: uuid.UUID | None = None,
    source: str = "sync",
) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        description=description,
        amount=amount,
        currency="USD",
        date=when,
        type=kind,
        source=source,
        payee_id=payee_id,
    )
    session.add(tx)
    await session.commit()
    return tx


async def an_invoice(
    client: AsyncClient,
    headers: dict,
    *,
    total: str = "3000.00",
    due: date | None = None,
    payee_id: uuid.UUID | None = None,
    direction: str = "receivable",
    as_draft: bool = False,
) -> dict:
    payload: dict = {
        "total": total,
        "due_date": str(due or TODAY),
        "direction": direction,
        "as_draft": as_draft,
    }
    if payee_id:
        payload["payee_id"] = str(payee_id)
    resp = await client.post("/api/invoices", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _load(session: AsyncSession, invoice_id: str) -> Invoice:
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(Invoice)
        .where(Invoice.id == uuid.UUID(invoice_id))
        .options(selectinload(Invoice.allocations), selectinload(Invoice.payee))
    )
    return result.unique().scalar_one()


# ---------------------------------------------------------------------------
# Money arriving against an invoice that already exists
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_clients_payment_settles_their_invoice_without_being_asked(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee, test_user
):
    """The tier that should carry the traffic: the payee is already on the
    transaction, so this is a lookup rather than a guess."""
    invoice = await an_invoice(client, biz_headers, payee_id=client_payee.id)
    tx = await a_transaction(session, account, test_user, payee_id=client_payee.id)

    applied = await reconciliation_service.match_incoming(
        session, account.workspace_id, [tx]
    )
    await session.commit()

    assert len(applied) == 1
    settled = await _load(session, invoice["id"])
    assert settled.allocations[0].transaction_id == tx.id
    assert settled.allocations[0].amount == Decimal("3000.00")
    # The rule that fired is recorded, not a generic "auto".
    assert settled.allocations[0].method == "same_client_exact"


@pytest.mark.asyncio
async def test_one_payment_never_settles_two_invoices(
    client: AsyncClient, biz_headers, session: AsyncSession, account, test_user
):
    """Re-matching money that already carries an allocation is how a
    ledger starts disagreeing with a bank.

    **One invoice, not two.** Two identical invoices are ambiguous, so a
    first pass over them links nothing and a second pass has nothing to
    re-link: the test would pass with the guard removed. One invoice makes
    the first pass succeed, which is the only state in which the guard
    has anything to do."""
    invoice = await an_invoice(client, biz_headers)
    tx = await a_transaction(session, account, test_user)

    first = await reconciliation_service.match_incoming(
        session, account.workspace_id, [tx]
    )
    await session.commit()
    again = await reconciliation_service.match_incoming(
        session, account.workspace_id, [tx]
    )
    await session.commit()

    # The first pass takes it; the second finds the money already spoken
    # for and leaves it alone.
    assert len(first) == 1 and again == []
    total = await session.execute(select(InvoiceAllocation))
    allocations = total.scalars().all()
    assert len(allocations) == 1
    assert str(allocations[0].invoice_id) == invoice["id"]


@pytest.mark.asyncio
async def test_half_the_money_is_not_taken_for_the_whole_invoice(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee, test_user
):
    """R$1.500 against a R$3.000 invoice is not evidence of anything. It
    could be an instalment, a different job, or a client paying the wrong
    amount, and the engine has no exact signal to tell those apart, so it
    takes none of them."""
    invoice = await an_invoice(client, biz_headers, payee_id=client_payee.id)
    half = await a_transaction(
        session, account, test_user, amount=Decimal("1500.00"), payee_id=client_payee.id
    )

    applied = await reconciliation_service.match_incoming(
        session, account.workspace_id, [half]
    )
    await session.commit()

    assert applied == []
    assert (await _load(session, invoice["id"])).allocations == []


@pytest.mark.asyncio
async def test_the_rest_of_an_instalment_matches_what_is_left(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee, test_user
):
    """Once a person books the first half, the second is exact again:
    because a candidate's amount is its **balance**, not its total. This
    is what makes a market that pays in parts reconcilable at all: each
    settlement narrows the target for the next one."""
    invoice = await an_invoice(client, biz_headers, payee_id=client_payee.id)
    first = await a_transaction(
        session, account, test_user, amount=Decimal("1500.00"), payee_id=client_payee.id
    )
    booked = await _load(session, invoice["id"])
    await invoice_service.allocate(
        session, booked, first.id, amount=Decimal("1500.00"), method="manual"
    )
    await session.commit()

    rest = await a_transaction(
        session, account, test_user, amount=Decimal("1500.00"), payee_id=client_payee.id
    )
    applied = await reconciliation_service.match_incoming(
        session, account.workspace_id, [rest]
    )
    await session.commit()

    assert len(applied) == 1
    settled = await _load(session, invoice["id"])
    assert sum(a.amount for a in settled.allocations) == Decimal("3000.00")
    assert {a.method for a in settled.allocations} == {"manual", "same_client_exact"}


@pytest.mark.asyncio
async def test_a_supplier_payment_settles_a_bill(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee, test_user
):
    """A payable is settled by money going out, and by the same
    strategies. The node is named for the object, not the direction."""
    bill = await an_invoice(
        client, biz_headers, direction="payable", payee_id=client_payee.id
    )
    payment = await a_transaction(
        session, account, test_user, kind="debit",
        description="PAGAMENTO ALPHA", payee_id=client_payee.id,
    )

    applied = await reconciliation_service.match_incoming(
        session, account.workspace_id, [payment]
    )
    await session.commit()

    assert len(applied) == 1
    settled = await _load(session, bill["id"])
    assert settled.allocations[0].transaction_id == payment.id


@pytest.mark.asyncio
async def test_money_leaving_never_settles_a_receivable(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee, test_user
):
    await an_invoice(client, biz_headers, payee_id=client_payee.id)
    outflow = await a_transaction(
        session, account, test_user, kind="debit", payee_id=client_payee.id
    )

    applied = await reconciliation_service.match_incoming(
        session, account.workspace_id, [outflow]
    )
    assert applied == []


@pytest.mark.asyncio
async def test_a_generated_placeholder_is_not_money(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee, test_user
):
    """It is a promise. Settling a debt with another debt is what the
    scope rule exists to prevent."""
    await an_invoice(client, biz_headers, payee_id=client_payee.id)
    placeholder = await a_transaction(
        session, account, test_user, payee_id=client_payee.id, source="recurring"
    )

    applied = await reconciliation_service.match_incoming(
        session, account.workspace_id, [placeholder]
    )
    assert applied == []


@pytest.mark.asyncio
async def test_an_invoice_in_another_workspace_is_never_a_candidate(
    client: AsyncClient, auth_headers, biz_headers, session: AsyncSession,
    account, test_user,
):
    """The workspace boundary is not a filter anyone may forget."""
    other = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Outro", "kind": "business", "self_membership": True},
    )
    other_headers = {**auth_headers, "X-Workspace-Id": other.json()["id"]}
    await an_invoice(client, other_headers)

    tx = await a_transaction(session, account, test_user)
    applied = await reconciliation_service.match_incoming(
        session, account.workspace_id, [tx]
    )
    assert applied == []


@pytest.mark.asyncio
async def test_a_payment_typed_in_by_hand_settles_its_invoice(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee, test_user
):
    """Through the endpoint, not the service. Someone who reconciles by
    typing the Pix in should not then have to go and link it: that is the
    manual work the feature exists to remove, and leaving this path out
    would remove it only for people whose bank happens to be connected."""
    invoice = await an_invoice(client, biz_headers, payee_id=client_payee.id)

    resp = await client.post(
        "/api/transactions",
        headers=biz_headers,
        json={
            "description": "PIX RECEBIDO ALPHA",
            "amount": "3000.00",
            "currency": "USD",
            "date": str(TODAY),
            "type": "credit",
            "account_id": str(account.id),
            "payee_id": str(client_payee.id),
        },
    )
    assert resp.status_code in (200, 201), resp.text

    settled = await _load(session, invoice["id"])
    assert len(settled.allocations) == 1
    assert settled.allocations[0].method == "same_client_exact"


# ---------------------------------------------------------------------------
# The direction that is easy to forget
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_invoice_issued_after_the_payment_finds_it(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee, test_user
):
    """The common Brazilian case: the client pays, and the nota follows
    days later. Nothing about the money changed, so nothing would ever
    re-examine it: this is the pass that does."""
    paid = await a_transaction(
        session, account, test_user,
        when=TODAY - timedelta(days=6), payee_id=client_payee.id,
    )

    invoice = await an_invoice(client, biz_headers, payee_id=client_payee.id)
    settled = await _load(session, invoice["id"])

    assert len(settled.allocations) == 1
    assert settled.allocations[0].transaction_id == paid.id


@pytest.mark.asyncio
async def test_money_from_a_payer_we_cannot_name_is_left_alone(
    client: AsyncClient, biz_headers, session: AsyncSession, account, test_user
):
    """Backwards, an exact amount is not enough. The money already had a
    life of its own (a refund, a transfer, another job), and claiming it
    for a document written afterwards is a guess. Forward, the same
    payment links, because there the promise came first."""
    await a_transaction(session, account, test_user, when=TODAY - timedelta(days=6))

    invoice = await an_invoice(client, biz_headers)
    assert (await _load(session, invoice["id"])).allocations == []


@pytest.mark.asyncio
async def test_two_payments_that_fit_equally_well_settle_nothing(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee, test_user
):
    """Taking the most recent of two identical payments would invent
    certainty exactly where the forward direction refuses to."""
    for days in (3, 6):
        await a_transaction(
            session, account, test_user,
            when=TODAY - timedelta(days=days), payee_id=client_payee.id,
        )

    invoice = await an_invoice(client, biz_headers, payee_id=client_payee.id)
    assert (await _load(session, invoice["id"])).allocations == []


@pytest.mark.asyncio
async def test_the_look_back_stops_at_the_window(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee, test_user
):
    """Bounded so issuing an invoice never scans a year of a busy
    account, and because money from six months ago is not this
    invoice's."""
    await a_transaction(
        session, account, test_user,
        when=TODAY - timedelta(days=200), payee_id=client_payee.id,
    )
    invoice = await an_invoice(client, biz_headers, payee_id=client_payee.id)
    loaded = await _load(session, invoice["id"])

    assert await reconciliation_service.match_for_invoice(session, loaded) is None


@pytest.mark.asyncio
async def test_a_draft_looks_back_at_nothing(
    client: AsyncClient, biz_headers, session: AsyncSession, account, client_payee, test_user
):
    """A draft is not owed yet. Settling one would claim a document that
    was never issued had been paid."""
    await a_transaction(session, account, test_user, payee_id=client_payee.id)
    draft = await an_invoice(
        client, biz_headers, payee_id=client_payee.id, as_draft=True
    )
    loaded = await _load(session, draft["id"])
    assert loaded.status == "draft"

    assert await reconciliation_service.match_for_invoice(session, loaded) is None
