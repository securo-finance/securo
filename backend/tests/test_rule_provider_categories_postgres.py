"""`list_provider_categories` on real PostgreSQL, where a JSON value has no
equality operator.

`transactions.raw_data` is a `JSON` column, not JSONB. Selecting the bare
value under `DISTINCT` compiles to `raw_data -> 'category'`, typed `json`, and
PostgreSQL fails while planning with `could not identify an equality operator
for type json` — always, whatever the rows hold, because the failure is about
the type, not the data. The rule editor's suggestion list answered 500 on
every PostgreSQL instance; SQLite runs the same statement happily, so the
SQLite suite never caught it (#353).

The labels are de-duplicated in Python instead, which also keeps a structured
value from being offered. A numeric code is offered in its text form, the same
string a saved rule matches against.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio

from app.models.account import Account
from app.models.transaction import Transaction
from app.services import rule_service

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused fixtures start in a fresh per-test PostgreSQL schema."""


async def test_provider_categories_do_not_ask_postgres_to_compare_json(
    session, test_user, test_workspace,
):
    """A numeric code beside repeated text labels neither breaks the query nor
    leaks into the suggestions, and comes back as the text a rule matches."""
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Provider categories",
        type="checking",
        balance=Decimal("1000"),
        currency="BRL",
    )

    def _row(description, raw_data, day):
        return Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=account.id,
            description=description,
            amount=Decimal("10.00"),
            date=date(2025, 3, day),
            type="debit",
            source="sync",
            raw_data=raw_data,
            created_at=datetime.now(timezone.utc),
        )

    session.add_all([
        account,
        _row("MARKET", {"category": "Groceries"}, 1),
        # A repeated label: without DISTINCT the de-duplication happens in
        # Python, and the suggestion must still appear once.
        _row("MARKET AGAIN", {"category": "Groceries"}, 2),
        # The provider sent a numeric code: a saved rule compares its text
        # form, so that is what the editor is offered.
        _row("NUMERIC CODE", {"category": 42}, 3),
        _row("NO CATEGORY KEY", {"other": "x"}, 4),
    ])
    await session.commit()

    categories = await rule_service.list_provider_categories(
        session, test_workspace.id
    )

    assert categories == ["42", "Groceries"]
