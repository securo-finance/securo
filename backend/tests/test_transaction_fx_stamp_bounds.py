"""The conversion the backend works out itself is bounded too (issue #422).

#1730 bounded `amount_primary` when it is typed or derived from the pair the
client sent. With neither, the backend converts the amount at the stored rate,
and 2e12 USD at 5 BRL is 1e13 BRL, past `Numeric(15, 2)`. On Postgres that
was a 500 at flush; it is a 422 now, on create, on an edit of the amount and on
import, and the restamp that follows a change of primary currency leaves such a
row "not converted" instead of failing.
"""
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.user import User
from app.core.money import MoneyOutOfRange
from app.services.fx_rate_service import stamp_primary_amount
from app.services.import_service import _cents
from app.services.primary_restamp_service import RestampStats, _converted

TOO_BIG = "2000000000000"  # 2e12 USD is 1e13 BRL at 5
LIMIT_MESSAGE = "amount_primary must be at most 9999999999999.99"


@pytest.fixture
async def usd_account(
    session: AsyncSession, test_user: User, test_account: Account
) -> Account:
    """A USD account for the test user, whose primary currency is BRL."""
    assert test_user.primary_currency == "BRL"
    session.add(
        FxRate(
            base_currency="USD", quote_currency="BRL", date=app_today(),
            rate=Decimal("5"), source="test",
        )
    )
    account = Account(
        user_id=test_user.id,
        workspace_id=test_account.workspace_id,
        name="Dollar account",
        type="checking",
        balance=Decimal("0"),
        currency="USD",
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


def _new(account: Account, amount: str, description: str = "Wire") -> dict:
    return {
        "description": description,
        "amount": amount,
        "date": app_today().isoformat(),
        "type": "debit",
        "account_id": str(account.id),
    }


async def _stored(session: AsyncSession, description: str):
    return await session.scalar(
        select(Transaction)
        .where(Transaction.description == description)
        .execution_options(populate_existing=True)
    )


@pytest.mark.asyncio
async def test_create_refuses_a_conversion_past_the_column(
    client: AsyncClient, auth_headers, session: AsyncSession, usd_account: Account
):
    resp = await client.post(
        "/api/transactions",
        headers=auth_headers,
        json=_new(usd_account, TOO_BIG, "refused"),
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == LIMIT_MESSAGE
    assert await _stored(session, "refused") is None


@pytest.mark.asyncio
async def test_a_conversion_inside_the_column_is_stamped_as_before(
    client: AsyncClient, auth_headers, usd_account: Account
):
    resp = await client.post(
        "/api/transactions", headers=auth_headers, json=_new(usd_account, "1000")
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert Decimal(str(body["amount_primary"])) == Decimal("5000.00")
    assert Decimal(str(body["fx_rate_used"])) == Decimal("5")


@pytest.mark.asyncio
async def test_an_edit_of_the_amount_refuses_a_conversion_past_the_column(
    client: AsyncClient, auth_headers, session: AsyncSession, usd_account: Account
):
    created = await client.post(
        "/api/transactions", headers=auth_headers, json=_new(usd_account, "1000", "edited")
    )
    assert created.status_code == 201, created.text

    resp = await client.patch(
        f"/api/transactions/{created.json()['id']}",
        headers=auth_headers,
        json={"amount": TOO_BIG},
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == LIMIT_MESSAGE
    row = await _stored(session, "edited")
    assert row.amount == Decimal("1000.00")
    assert row.amount_primary == Decimal("5000.00")


@pytest.mark.asyncio
@pytest.mark.parametrize("fx_rate", [None, "5"])
async def test_import_refuses_a_conversion_past_the_column(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    usd_account: Account,
    fx_rate,
):
    """Both import paths: the stored rate, and the rate the file carries."""
    fits = {"description": "imported ok", "amount": "10", "date": app_today().isoformat(),
            "type": "debit", "currency": "USD"}
    too_big = {**fits, "description": "imported big", "amount": TOO_BIG}
    if fx_rate:
        fits["fx_rate"] = too_big["fx_rate"] = fx_rate

    resp = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={"account_id": str(usd_account.id), "transactions": [fits, too_big]},
    )

    assert resp.status_code == 422, resp.text
    assert "amount_primary must be at most" in resp.json()["detail"]
    # The import commits once, so the row that fits was not kept either.
    assert await _stored(session, "imported ok") is None


@pytest.mark.asyncio
async def test_a_restamp_leaves_a_conversion_past_the_column_unconverted(
    session: AsyncSession, usd_account: Account
):
    stats = RestampStats(currency="BRL")

    too_big = await _converted(session, stats, Decimal(TOO_BIG), "USD", app_today())
    fits = await _converted(session, stats, Decimal("1000"), "USD", app_today())

    assert too_big == (None, None)
    assert stats.unconverted == 1
    assert fits[0] == Decimal("5000.00")


# Transfers between currencies convert the amount onto the other leg with the
# stored rate. That leg's `amount` is bounded the same way.
AMOUNT_LIMIT = "amount must be at most 9999999999999.99"


async def _account(session: AsyncSession, like: Account, currency: str, name: str) -> Account:
    account = Account(
        user_id=like.user_id,
        workspace_id=like.workspace_id,
        name=name,
        type="checking",
        balance=Decimal("0"),
        currency=currency,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


@pytest.fixture
async def jpy_account(session: AsyncSession, usd_account: Account) -> Account:
    """BRL to JPY is 30 here, so 2e12 BRL lands as 6e13 JPY. The BRL leg is
    in the primary currency and fits, so only the converted leg overflows."""
    session.add(
        FxRate(
            base_currency="USD", quote_currency="JPY", date=app_today(),
            rate=Decimal("150"), source="test",
        )
    )
    await session.commit()
    return await _account(session, usd_account, "JPY", "Yen account")


async def _rows(session: AsyncSession, description: str) -> list[Transaction]:
    result = await session.scalars(
        select(Transaction)
        .where(Transaction.description == description)
        .execution_options(populate_existing=True)
    )
    return list(result.all())


@pytest.mark.asyncio
async def test_a_transfer_refuses_a_converted_leg_past_the_column(
    client: AsyncClient, auth_headers, session: AsyncSession, usd_account: Account
):
    brl = await _account(session, usd_account, "BRL", "Real account")

    resp = await client.post(
        "/api/transactions/transfer",
        headers=auth_headers,
        json={
            "from_account_id": str(usd_account.id),
            "to_account_id": str(brl.id),
            "amount": TOO_BIG,
            "date": app_today().isoformat(),
            "description": "big transfer",
        },
    )

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == AMOUNT_LIMIT
    assert await _rows(session, "big transfer") == []


@pytest.mark.asyncio
async def test_a_counterpart_refuses_a_converted_amount_past_the_column(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_account: Account,
    jpy_account: Account,
):
    assert test_account.currency == "BRL"
    anchor = await client.post(
        "/api/transactions", headers=auth_headers, json=_new(test_account, TOO_BIG, "anchor")
    )
    assert anchor.status_code == 201, anchor.text

    resp = await client.post(
        f"/api/transactions/{anchor.json()['id']}/create-counterpart",
        headers=auth_headers,
        json={"to_account_id": str(jpy_account.id)},
    )

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == AMOUNT_LIMIT
    rows = await _rows(session, "anchor")
    assert len(rows) == 1 and rows[0].transfer_pair_id is None


@pytest.mark.asyncio
async def test_an_edit_of_one_leg_refuses_a_converted_pair_past_the_column(
    client: AsyncClient,
    auth_headers,
    session: AsyncSession,
    test_account: Account,
    jpy_account: Account,
):
    created = await client.post(
        "/api/transactions/transfer",
        headers=auth_headers,
        json={
            "from_account_id": str(test_account.id),
            "to_account_id": str(jpy_account.id),
            "amount": "100",
            "date": app_today().isoformat(),
            "description": "pair",
        },
    )
    assert created.status_code == 201, created.text
    assert Decimal(str(created.json()["credit"]["amount"])) == Decimal("3000.00")

    resp = await client.patch(
        f"/api/transactions/{created.json()['debit']['id']}",
        headers=auth_headers,
        json={"amount": TOO_BIG},
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == AMOUNT_LIMIT
    amounts = sorted(row.amount for row in await _rows(session, "pair"))
    assert amounts == [Decimal("100.00"), Decimal("3000.00")]


@pytest.mark.asyncio
async def test_a_stamp_whose_account_leg_overflows_leaves_the_row_as_it_was(
    session: AsyncSession, jpy_account: Account
):
    """1e11 USD fits as 5e11 BRL but not as 1.5e13 JPY on its yen account. The
    primary stamp is put back, so the healer finds the row again next run
    instead of a half-stamped row it no longer selects."""
    tx = Transaction(
        user_id=jpy_account.user_id,
        workspace_id=jpy_account.workspace_id,
        account_id=jpy_account.id,
        description="half stamped",
        amount=Decimal("100000000000"),
        currency="USD",
        date=app_today(),
        type="debit",
        source="manual",
    )
    session.add(tx)
    await session.flush()

    with pytest.raises(MoneyOutOfRange, match="amount_account"):
        await stamp_primary_amount(session, tx.user_id, tx, allow_fetch=False)

    assert tx.amount_primary is None
    assert tx.fx_rate_used is None
    assert tx.amount_account is None


def test_an_imported_conversion_is_rounded_before_it_is_bounded():
    assert _cents(Decimal("9999999999999.9918")) == Decimal("9999999999999.99")
    assert _cents(Decimal("1.005")) == Decimal("1.01")
