"""MCP tool wrappers — call them directly with seeded data and a fake
CallContext (skipping HTTP/JWT, which is covered by test_agents_jwt.py).

Tools that depend on services Securo already tests heavily (reports,
dashboard) just check that the wrapper returns a serializable shape, not
the full numeric correctness of those services.
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

# Importing this package registers all tools into mcp_server.registry.REGISTRY.
import mcp_server.tools  # noqa: F401
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def ctx(test_user) -> CallContext:
    return CallContext(user_id=test_user.id, conversation_id=uuid.uuid4())


# --- Registry shape --------------------------------------------------------

def test_registry_contains_v1_tools():
    """We promised these in the design — keep the contract."""
    expected = {
        "list_transactions",
        "list_accounts",
        "get_account_summary",
        "list_categories",
        "list_payees",
        "get_budget_vs_actual",
        "list_budgets",
        "get_net_worth",
        "get_income_expenses",
        "get_cash_flow",
        "get_dashboard_snapshot",
        "search_all",
        "aggregate",
        "propose_categorize",
        "propose_create_category",
        "propose_create_budget",
        "propose_create_payee_rule",
        "propose_create_transaction",
        "propose_create_recurring_transaction",
        "propose_update_recurring_transaction",
        "propose_cancel_recurring_transaction",
        "propose_create_goal",
        "search_knowledge_base",
        "list_recurring_transactions",
        "list_assets",
        "list_goals",
    }
    assert expected.issubset(set(REGISTRY.keys())), (
        f"missing: {expected - set(REGISTRY.keys())}"
    )


def test_proposal_tools_marked_is_proposal():
    for name in ("propose_categorize", "propose_create_category", "propose_create_budget", "propose_create_payee_rule"):
        spec = REGISTRY[name]
        assert spec.is_proposal, f"{name} should have is_proposal=True"


def test_each_tool_has_input_schema():
    for name, spec in REGISTRY.items():
        assert spec.parameters.get("type") == "object", f"{name} schema must be object"
        assert "properties" in spec.parameters


@pytest.mark.parametrize(
    "tool_name",
    [
        "propose_create_recurring_transaction",
        "propose_update_recurring_transaction",
    ],
    ids=["create", "update"],
)
def test_recurring_proposal_frequency_schema_includes_new_values(tool_name):
    advertised = set(
        REGISTRY[tool_name].parameters["properties"]["frequency"]["enum"]
    )
    assert {"biweekly", "semiannual"} <= advertised


# --- Read tools (with real seeded data) -----------------------------------

async def test_list_transactions_returns_seeded_data(
    session: AsyncSession, ctx: CallContext, test_transactions
):
    handler = REGISTRY["list_transactions"].handler
    result = await handler(session=session, ctx=ctx)
    assert isinstance(result, dict)
    assert "items" in result and "total" in result
    assert result["total"] >= len(test_transactions)
    sample = result["items"][0]
    for k in ("id", "date", "description", "amount", "currency"):
        assert k in sample


async def test_list_transactions_with_search_filter(
    session: AsyncSession, ctx: CallContext, test_transactions
):
    handler = REGISTRY["list_transactions"].handler
    result = await handler(session=session, ctx=ctx, search="UBER")
    assert all("UBER" in x["description"] for x in result["items"])


async def test_list_transactions_default_sort_is_purchase_date_desc(
    session: AsyncSession, ctx: CallContext, test_transactions
):
    """Regression: the default sort must be by purchase date desc so
    'what's my last transaction?' returns the most recent one.
    Previously we sorted by COALESCE(effective_bill_date, date), which
    floats credit-card transactions whose bill is due in the future to
    the top — pushing the actual latest transaction down."""
    handler = REGISTRY["list_transactions"].handler
    result = await handler(session=session, ctx=ctx, limit=5)
    items = result["items"]
    assert items, "fixture has transactions; default query must return them"
    # Each item's date >= the next item's date (desc).
    dates = [x["date"] for x in items if x["date"]]
    assert dates == sorted(dates, reverse=True), f"not sorted desc by purchase date: {dates}"
    assert result["sort_by"] == "transaction_date"


async def test_list_transactions_exposes_extra_fields(
    session: AsyncSession, ctx: CallContext, test_transactions, test_account, test_categories
):
    """Make sure the LLM gets enough context per row — names, not just
    ids — so it can answer "from which account?" without a second call."""
    handler = REGISTRY["list_transactions"].handler
    result = await handler(session=session, ctx=ctx, limit=5)
    sample = result["items"][0]
    for key in ("id", "date", "description", "amount", "currency", "type", "account_id", "account_name"):
        assert key in sample, f"missing key {key} in row"
    # account_name should resolve when category/account are seeded.
    assert any(x.get("account_name") == "Conta Corrente" for x in result["items"])


async def test_list_transactions_tx_type_filter(
    session: AsyncSession, ctx: CallContext, test_transactions
):
    handler = REGISTRY["list_transactions"].handler
    debits = await handler(session=session, ctx=ctx, tx_type="debit")
    credits = await handler(session=session, ctx=ctx, tx_type="credit")
    assert all(x["type"] == "debit" for x in debits["items"])
    assert all(x["type"] == "credit" for x in credits["items"])
    assert debits["total"] + credits["total"] >= len(debits["items"])  # sanity


async def test_list_transactions_currency_filter_matches_native(
    session: AsyncSession, ctx: CallContext, test_transactions
):
    """The Transaction.currency column defaults to USD in the fixture
    (independent of the account's BRL currency), so 'USD' should return
    everything and any narrower filter returns ≤ that count."""
    handler = REGISTRY["list_transactions"].handler
    usd = await handler(session=session, ctx=ctx, currency="USD")
    assert usd["total"] >= 1
    assert all(x["currency"] == "USD" for x in usd["items"])


async def test_list_transactions_currency_filter_returns_zero_for_unused(
    session: AsyncSession, ctx: CallContext, test_transactions
):
    """Regression for the 'do I have any EUR transactions?' bug — the
    answer should be a clean 0, not a 0 from text-searching 'EUR' in
    descriptions."""
    handler = REGISTRY["list_transactions"].handler
    eur = await handler(session=session, ctx=ctx, currency="EUR")
    assert eur["total"] == 0
    assert eur["items"] == []


async def test_list_transactions_currency_filter_is_case_insensitive(
    session: AsyncSession, ctx: CallContext, test_transactions
):
    handler = REGISTRY["list_transactions"].handler
    a = await handler(session=session, ctx=ctx, currency="usd")
    b = await handler(session=session, ctx=ctx, currency="USD")
    assert a["total"] == b["total"]


async def test_list_transactions_min_amount_filter(
    session: AsyncSession, ctx: CallContext, test_transactions
):
    handler = REGISTRY["list_transactions"].handler
    big = await handler(session=session, ctx=ctx, min_amount=100)
    # Fixture has transactions of 25.50, 45.00, 8000.00, 150.00, 39.90
    # Primary-currency stamping isn't computed in tests so this falls back
    # to native amount. Three rows >= 100: 8000, 150 (and zero with stamping
    # rules in our test setup we keep this lenient).
    assert big["total"] >= 1
    for it in big["items"]:
        # All returned amounts must be >= 100 (in their primary view).
        amt = it.get("amount_primary") or it.get("amount") or 0
        assert amt >= 100, f"row {it} slipped under min_amount"


async def test_list_transactions_max_amount_filter(
    session: AsyncSession, ctx: CallContext, test_transactions
):
    handler = REGISTRY["list_transactions"].handler
    small = await handler(session=session, ctx=ctx, max_amount=50)
    for it in small["items"]:
        amt = it.get("amount_primary") or it.get("amount") or 0
        assert amt <= 50


async def test_list_recurring_transactions_returns_empty_dict_with_no_data(
    session: AsyncSession, ctx: CallContext
):
    """When there are no recurring transactions the tool must still return
    a stable {items, total} shape, not raise."""
    handler = REGISTRY["list_recurring_transactions"].handler
    r = await handler(session=session, ctx=ctx)
    assert r == {"items": [], "total": 0}


async def test_list_assets_empty(session: AsyncSession, ctx: CallContext):
    handler = REGISTRY["list_assets"].handler
    r = await handler(session=session, ctx=ctx)
    assert r["total"] == 0


async def test_list_goals_empty(session: AsyncSession, ctx: CallContext):
    handler = REGISTRY["list_goals"].handler
    r = await handler(session=session, ctx=ctx)
    assert r["total"] == 0


async def test_list_budgets_empty(session: AsyncSession, ctx: CallContext):
    handler = REGISTRY["list_budgets"].handler
    r = await handler(session=session, ctx=ctx)
    assert r == {"items": [], "total": 0}


async def test_aggregate_payee_filter(
    session: AsyncSession, ctx: CallContext, test_transactions
):
    """The new payee_id filter on aggregate makes 'how much did I spend at X?'
    a single tool call instead of the agent's old 4-call dance."""
    handler = REGISTRY["aggregate"].handler
    r = await handler(session=session, ctx=ctx, payee_id=str(uuid.uuid4()))
    # Filtering by a non-existent payee returns no buckets — but the call
    # should still succeed (no error key).
    assert "error" not in r
    assert r["items"] == []


async def test_aggregate_currency_filter(
    session: AsyncSession, ctx: CallContext, test_transactions
):
    handler = REGISTRY["aggregate"].handler
    usd = await handler(session=session, ctx=ctx, currency="USD", group_by="category")
    assert "error" not in usd
    eur = await handler(session=session, ctx=ctx, currency="EUR", group_by="category")
    assert "error" not in eur and eur["items"] == []


async def test_list_transactions_account_types_filter(
    session: AsyncSession, ctx: CallContext, test_transactions, test_account
):
    """fixture's test_account is 'checking' — filtering on 'credit_card' returns nothing."""
    handler = REGISTRY["list_transactions"].handler
    cc = await handler(session=session, ctx=ctx, account_types=["credit_card"])
    assert cc["total"] == 0
    checking = await handler(session=session, ctx=ctx, account_types=["checking"])
    assert checking["total"] >= 1


async def test_list_accounts(session: AsyncSession, ctx: CallContext, test_account):
    handler = REGISTRY["list_accounts"].handler
    result = await handler(session=session, ctx=ctx)
    assert result["total"] >= 1
    found = next((a for a in result["items"] if a["id"] == str(test_account.id)), None)
    assert found is not None
    assert found["currency"] == "BRL"


async def test_get_account_summary(session: AsyncSession, ctx: CallContext, test_account, test_transactions):
    handler = REGISTRY["get_account_summary"].handler
    result = await handler(session=session, ctx=ctx, account_id=str(test_account.id))
    # Real service returns a dict with income/expense/etc. We just verify it's not an error.
    assert isinstance(result, dict)
    assert "error" not in result or result.get("error") is None


async def test_get_account_summary_unknown_account(session: AsyncSession, ctx: CallContext):
    handler = REGISTRY["get_account_summary"].handler
    result = await handler(session=session, ctx=ctx, account_id=str(uuid.uuid4()))
    assert result.get("error") == "account not found"


async def test_list_categories(session: AsyncSession, ctx: CallContext, test_categories):
    handler = REGISTRY["list_categories"].handler
    result = await handler(session=session, ctx=ctx)
    names = {c["name"] for c in result["items"]}
    assert "Alimentação" in names
    # Check the IDs round-trip as strings (LLM consumes via JSON).
    for c in result["items"]:
        uuid.UUID(c["id"])  # raises if not a UUID


async def test_list_payees_empty(session: AsyncSession, ctx: CallContext):
    handler = REGISTRY["list_payees"].handler
    result = await handler(session=session, ctx=ctx)
    assert result["total"] == 0
    assert result["items"] == []


async def test_get_dashboard_snapshot(session: AsyncSession, ctx: CallContext, test_transactions):
    handler = REGISTRY["get_dashboard_snapshot"].handler
    result = await handler(session=session, ctx=ctx)
    assert isinstance(result, dict)
    # Snapshot is a Pydantic model dumped to dict — at minimum should be non-empty.
    assert result


# --- aggregate -------------------------------------------------------------

async def test_aggregate_by_category(
    session: AsyncSession, ctx: CallContext, test_transactions, test_categories
):
    handler = REGISTRY["aggregate"].handler
    result = await handler(
        session=session, ctx=ctx,
        metric="sum", group_by="category", tx_type="expense",
    )
    assert "items" in result
    # We have UBER (Transporte) and IFOOD (Alimentação) as expenses.
    labels = {item.get("label") for item in result["items"]}
    assert "Transporte" in labels or "Alimentação" in labels


@pytest.mark.skip(reason="aggregate by month uses PostgreSQL to_char, not portable to SQLite test DB")
async def test_aggregate_by_month(
    session: AsyncSession, ctx: CallContext, test_transactions
):
    handler = REGISTRY["aggregate"].handler
    result = await handler(session=session, ctx=ctx, metric="count", group_by="month")
    assert "items" in result
    for item in result["items"]:
        if item["bucket"]:
            assert len(item["bucket"]) == 7 and item["bucket"][4] == "-"


async def test_aggregate_unknown_group_by(session: AsyncSession, ctx: CallContext):
    handler = REGISTRY["aggregate"].handler
    result = await handler(session=session, ctx=ctx, group_by="bogus")
    assert "error" in result


# --- search_all ------------------------------------------------------------

async def test_search_all(session: AsyncSession, ctx: CallContext, test_transactions):
    handler = REGISTRY["search_all"].handler
    result = await handler(session=session, ctx=ctx, query="UBER")
    assert result["total"] >= 1


# --- Proposal tools (no DB writes) ----------------------------------------

async def test_propose_categorize_returns_preview(
    session: AsyncSession, ctx: CallContext, test_transactions, test_categories
):
    handler = REGISTRY["propose_categorize"].handler
    target_cat = test_categories[0]  # Alimentação
    tx_ids = [str(t.id) for t in test_transactions[:2]]
    result = await handler(
        session=session, ctx=ctx,
        transaction_ids=tx_ids,
        category_id=str(target_cat.id),
    )
    assert result["kind"] == "categorize"
    assert result["target_category"]["id"] == str(target_cat.id)
    assert result["affected_count"] == 2
    assert "apply_endpoint" in result


async def test_propose_categorize_unknown_category(
    session: AsyncSession, ctx: CallContext, test_transactions
):
    handler = REGISTRY["propose_categorize"].handler
    result = await handler(
        session=session, ctx=ctx,
        transaction_ids=[str(test_transactions[0].id)],
        category_id=str(uuid.uuid4()),
    )
    assert result["error"] == "category not found"


async def test_propose_create_category_detects_collision(
    session: AsyncSession, ctx: CallContext, test_categories
):
    handler = REGISTRY["propose_create_category"].handler
    # Use the same name as an existing category — should flag collision.
    result = await handler(session=session, ctx=ctx, name=test_categories[0].name)
    assert result["kind"] == "create_category"
    assert result["name_collision"] is not None
    assert result["name_collision"]["name"] == test_categories[0].name


async def test_propose_create_category_no_collision(
    session: AsyncSession, ctx: CallContext, test_categories
):
    handler = REGISTRY["propose_create_category"].handler
    result = await handler(session=session, ctx=ctx, name="UniqueNewCategoryX9Z")
    assert result["name_collision"] is None
    assert result["proposed"]["name"] == "UniqueNewCategoryX9Z"


async def test_propose_create_budget(
    session: AsyncSession, ctx: CallContext, test_categories
):
    handler = REGISTRY["propose_create_budget"].handler
    result = await handler(
        session=session, ctx=ctx,
        category_id=str(test_categories[0].id),
        month="2026-05-15",
        amount=500.0,
        currency="BRL",
    )
    assert result["kind"] == "create_budget"
    assert result["proposed"]["amount"] == 500.0
    assert result["proposed"]["month"] == "2026-05-01"  # snapped to month start


async def test_propose_create_transaction_full(
    session: AsyncSession, ctx: CallContext, test_account, test_categories
):
    handler = REGISTRY["propose_create_transaction"].handler
    r = await handler(
        session=session, ctx=ctx,
        description="Almoço",
        amount=50.0,
        type="debit",
        account_id=str(test_account.id),
        category_id=str(test_categories[0].id),
    )
    assert r["kind"] == "create_transaction"
    p = r["proposed"]
    assert p["description"] == "Almoço"
    assert p["amount"] == 50.0
    assert p["type"] == "debit"
    assert p["account_id"] == str(test_account.id)
    assert p["category_name"] == test_categories[0].name
    assert p["currency"] == "BRL"  # inherited from account
    assert p["date"]  # default to today


async def test_propose_create_transaction_unknown_account(
    session: AsyncSession, ctx: CallContext
):
    handler = REGISTRY["propose_create_transaction"].handler
    r = await handler(
        session=session, ctx=ctx,
        description="x", amount=1.0, type="debit", account_id=str(uuid.uuid4()),
    )
    assert r["error"] == "account not found"


async def test_propose_create_recurring_monthly_requires_day(
    session: AsyncSession, ctx: CallContext, test_account
):
    handler = REGISTRY["propose_create_recurring_transaction"].handler
    r = await handler(
        session=session, ctx=ctx,
        description="Netflix", amount=55.0, type="debit",
        frequency="monthly", account_id=str(test_account.id),
    )
    assert "day_of_month" in r.get("error", "")


async def test_propose_create_recurring_semiannual_requires_day(
    session: AsyncSession, ctx: CallContext, test_account
):
    handler = REGISTRY["propose_create_recurring_transaction"].handler
    result = await handler(
        session=session,
        ctx=ctx,
        description="Insurance",
        amount=600.0,
        type="debit",
        frequency="semiannual",
        account_id=str(test_account.id),
    )
    assert "day_of_month" in result.get("error", "")


async def test_propose_create_recurring_monthly_full(
    session: AsyncSession, ctx: CallContext, test_account
):
    handler = REGISTRY["propose_create_recurring_transaction"].handler
    r = await handler(
        session=session, ctx=ctx,
        description="Netflix", amount=55.0, type="debit",
        frequency="monthly", day_of_month=10,
        weekend_adjustment="previous_friday",
        account_id=str(test_account.id),
    )
    assert r["kind"] == "create_recurring_transaction"
    assert r["proposed"]["day_of_month"] == 10
    assert r["proposed"]["frequency"] == "monthly"
    assert r["proposed"]["weekend_adjustment"] == "previous_friday"


async def test_propose_update_recurring_no_changes(
    session: AsyncSession, ctx: CallContext, test_user
):
    """If the LLM calls without any change fields, the tool should refuse
    with a clear error rather than build an empty 'changes' object."""
    from app.models.recurring_transaction import RecurringTransaction
    from decimal import Decimal
    from datetime import date

    rt = RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id,
        description="Salary", amount=Decimal("4000"), currency="BRL",
        type="credit", frequency="monthly", day_of_month=1,
        start_date=date(2026, 1, 1), next_occurrence=date(2026, 5, 1), is_active=True,
    )
    session.add(rt)
    await session.commit()

    handler = REGISTRY["propose_update_recurring_transaction"].handler
    r = await handler(session=session, ctx=ctx, recurring_id=str(rt.id))
    assert r["error"] == "no changes provided"


async def test_propose_update_recurring_amount_change(
    session: AsyncSession, ctx: CallContext, test_user
):
    """'Update my salary to R$8,000' — change amount only, current values
    are echoed back so the user sees the diff."""
    from app.models.recurring_transaction import RecurringTransaction
    from decimal import Decimal
    from datetime import date

    rt = RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id,
        description="Salary", amount=Decimal("4000"), currency="BRL",
        type="credit", frequency="monthly", day_of_month=1,
        start_date=date(2026, 1, 1), next_occurrence=date(2026, 5, 1), is_active=True,
    )
    session.add(rt)
    await session.commit()

    handler = REGISTRY["propose_update_recurring_transaction"].handler
    r = await handler(session=session, ctx=ctx, recurring_id=str(rt.id), amount=8000)
    assert r["kind"] == "update_recurring_transaction"
    assert r["target"]["amount"] == 4000.0  # current value visible
    assert r["changes"] == {"amount": 8000.0}


async def test_propose_cancel_recurring_default_mode_is_deactivate(
    session: AsyncSession, ctx: CallContext, test_user
):
    from app.models.recurring_transaction import RecurringTransaction
    from decimal import Decimal
    from datetime import date

    rt = RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id,
        description="Spotify", amount=Decimal("23.90"), currency="BRL",
        type="debit", frequency="monthly", day_of_month=15,
        start_date=date(2026, 1, 1), next_occurrence=date(2026, 5, 15), is_active=True,
    )
    session.add(rt)
    await session.commit()

    handler = REGISTRY["propose_cancel_recurring_transaction"].handler
    r = await handler(session=session, ctx=ctx, recurring_id=str(rt.id))
    assert r["mode"] == "deactivate"
    assert r["target"]["description"] == "Spotify"


async def test_propose_create_goal(session: AsyncSession, ctx: CallContext):
    handler = REGISTRY["propose_create_goal"].handler
    r = await handler(
        session=session, ctx=ctx,
        name="Viagem para o Japão", target_amount=10000, deadline="2026-12-31",
    )
    assert r["kind"] == "create_goal"
    assert r["proposed"]["target_amount"] == 10000.0
    assert r["proposed"]["deadline"] == "2026-12-31"
    assert r["proposed"]["initial_amount"] == 0.0


async def test_propose_create_payee_rule_unknown_category(
    session: AsyncSession, ctx: CallContext
):
    handler = REGISTRY["propose_create_payee_rule"].handler
    result = await handler(
        session=session, ctx=ctx,
        match_pattern="UBER",
        category_id=str(uuid.uuid4()),
    )
    assert result["error"] == "category not found"


# --- search_knowledge_base ------------------------------------------------

async def test_search_knowledge_base_requires_agent_id(
    session: AsyncSession, test_user
):
    """Without agent_id in context, the tool refuses (covers a misconfigured caller)."""
    handler = REGISTRY["search_knowledge_base"].handler
    bare_ctx = CallContext(user_id=test_user.id)  # no agent_id
    result = await handler(session=session, ctx=bare_ctx, query="anything")
    assert "error" in result
    assert "agent_id" in result["error"]


# --- _can_apply gate -------------------------------------------------------
# Authorization-critical: writes must only happen when the caller is external
# AND set apply=True. Internal callers never write through propose_* tools.

def test_can_apply_truth_table(test_user):
    from mcp_server.tools.proposals import _can_apply

    internal = CallContext(user_id=test_user.id, external=False)
    external = CallContext(user_id=test_user.id, external=True)

    assert _can_apply(internal, apply=False) is False
    assert _can_apply(internal, apply=True) is False  # internal+apply still blocked
    assert _can_apply(external, apply=False) is False
    assert _can_apply(external, apply=True) is True


async def test_propose_categorize_internal_apply_does_not_write(
    session: AsyncSession, test_user, test_transactions, test_categories
):
    """Internal callers (Securo's own runtime) must never mutate, even if
    apply=True is somehow passed through."""
    from sqlalchemy import select
    from app.models.transaction import Transaction

    handler = REGISTRY["propose_categorize"].handler
    internal_ctx = CallContext(user_id=test_user.id, external=False)
    tx = test_transactions[3]  # uncategorized PIX RECEBIDO
    target = test_categories[0]
    assert tx.category_id is None

    result = await handler(
        session=session, ctx=internal_ctx,
        transaction_ids=[str(tx.id)],
        category_id=str(target.id),
        apply=True,
    )
    assert "applied" not in result  # preview only

    refreshed = (await session.execute(
        select(Transaction).where(Transaction.id == tx.id)
    )).scalar_one()
    assert refreshed.category_id is None


async def test_propose_categorize_external_no_apply_returns_preview(
    session: AsyncSession, test_user, test_transactions, test_categories
):
    """External caller without apply=True still gets a preview, no write."""
    from sqlalchemy import select
    from app.models.transaction import Transaction

    handler = REGISTRY["propose_categorize"].handler
    external_ctx = CallContext(user_id=test_user.id, external=True)
    tx = test_transactions[3]
    target = test_categories[0]

    result = await handler(
        session=session, ctx=external_ctx,
        transaction_ids=[str(tx.id)],
        category_id=str(target.id),
        apply=False,
    )
    assert "applied" not in result

    refreshed = (await session.execute(
        select(Transaction).where(Transaction.id == tx.id)
    )).scalar_one()
    assert refreshed.category_id is None


async def test_propose_categorize_external_apply_writes(
    session: AsyncSession, test_user, test_transactions, test_categories
):
    """External caller with apply=True commits the change."""
    from sqlalchemy import select
    from app.models.transaction import Transaction

    handler = REGISTRY["propose_categorize"].handler
    external_ctx = CallContext(user_id=test_user.id, external=True)
    tx = test_transactions[3]
    target = test_categories[0]

    result = await handler(
        session=session, ctx=external_ctx,
        transaction_ids=[str(tx.id)],
        category_id=str(target.id),
        apply=True,
    )
    assert result.get("applied") is True
    assert result.get("updated_count") == 1

    refreshed = (await session.execute(
        select(Transaction).where(Transaction.id == tx.id)
    )).scalar_one()
    assert refreshed.category_id == target.id


# --- Apply mode for the remaining propose_* tools --------------------------
# One happy-path test per tool to catch regressions in the per-service write
# wiring. The _can_apply gate is shared across all of them and is already
# exercised by test_can_apply_truth_table + the propose_categorize trio.

def test_every_propose_tool_advertises_apply_parameter():
    """Schema contract: every propose_* tool must expose `apply` in its
    JSON Schema so external clients can opt into write mode."""
    for name, spec in REGISTRY.items():
        if not name.startswith("propose_"):
            continue
        assert "apply" in spec.parameters["properties"], (
            f"{name} is missing the `apply` parameter"
        )
        assert spec.parameters["properties"]["apply"]["type"] == "boolean"
        assert spec.parameters["properties"]["apply"].get("default") is False


async def test_propose_create_category_external_apply_writes(
    session: AsyncSession, test_user
):
    from sqlalchemy import select
    from app.models.category import Category

    handler = REGISTRY["propose_create_category"].handler
    ctx = CallContext(user_id=test_user.id, external=True)

    result = await handler(
        session=session, ctx=ctx,
        name="Doações", icon="heart", color="#EF4444",
        apply=True,
    )
    assert result.get("applied") is True
    new_id = uuid.UUID(result["id"])

    row = (await session.execute(
        select(Category).where(Category.id == new_id, Category.user_id == test_user.id)
    )).scalar_one()
    assert row.name == "Doações"
    assert row.icon == "heart"


async def test_propose_create_category_external_apply_blocks_collision(
    session: AsyncSession, test_user, test_categories
):
    """When the name collides with an existing category, apply must NOT
    create a duplicate — the preview already flagged it."""
    from sqlalchemy import select, func
    from app.models.category import Category

    handler = REGISTRY["propose_create_category"].handler
    ctx = CallContext(user_id=test_user.id, external=True)
    existing = test_categories[0]

    before = (await session.execute(
        select(func.count()).select_from(Category).where(Category.user_id == test_user.id)
    )).scalar_one()

    result = await handler(
        session=session, ctx=ctx,
        name=existing.name,
        apply=True,
    )
    assert "applied" not in result
    assert "error" in result

    after = (await session.execute(
        select(func.count()).select_from(Category).where(Category.user_id == test_user.id)
    )).scalar_one()
    assert after == before


async def test_propose_create_budget_external_apply_writes(
    session: AsyncSession, test_user, test_categories
):
    from datetime import date
    from sqlalchemy import select
    from app.models.budget import Budget

    handler = REGISTRY["propose_create_budget"].handler
    ctx = CallContext(user_id=test_user.id, external=True)
    cat = test_categories[0]

    result = await handler(
        session=session, ctx=ctx,
        category_id=str(cat.id),
        month=date.today().replace(day=1).isoformat(),
        amount=500.0,
        apply=True,
    )
    assert result.get("applied") is True

    row = (await session.execute(
        select(Budget).where(Budget.id == uuid.UUID(result["id"]))
    )).scalar_one()
    assert row.category_id == cat.id
    assert float(row.amount) == 500.0


async def test_propose_create_transaction_external_apply_writes(
    session: AsyncSession, test_user, test_account, test_categories
):
    from sqlalchemy import select
    from app.models.transaction import Transaction

    handler = REGISTRY["propose_create_transaction"].handler
    ctx = CallContext(user_id=test_user.id, external=True)

    result = await handler(
        session=session, ctx=ctx,
        description="Sushi delivery",
        amount=78.50,
        type="debit",
        account_id=str(test_account.id),
        category_id=str(test_categories[0].id),
        apply=True,
    )
    assert result.get("applied") is True

    row = (await session.execute(
        select(Transaction).where(Transaction.id == uuid.UUID(result["id"]))
    )).scalar_one()
    assert row.description == "Sushi delivery"
    assert float(row.amount) == 78.50
    assert row.account_id == test_account.id


async def test_propose_create_recurring_transaction_external_apply_writes(
    session: AsyncSession, test_user, test_account
):
    from sqlalchemy import select
    from app.models.recurring_transaction import RecurringTransaction

    handler = REGISTRY["propose_create_recurring_transaction"].handler
    ctx = CallContext(user_id=test_user.id, external=True)

    result = await handler(
        session=session, ctx=ctx,
        description="Netflix",
        amount=55.90,
        type="debit",
        frequency="monthly",
        day_of_month=10,
        account_id=str(test_account.id),
        weekend_adjustment="next_monday",
        apply=True,
    )
    assert result.get("applied") is True

    row = (await session.execute(
        select(RecurringTransaction).where(
            RecurringTransaction.id == uuid.UUID(result["id"])
        )
    )).scalar_one()
    assert row.description == "Netflix"
    assert row.frequency == "monthly"
    assert row.day_of_month == 10
    assert row.weekend_adjustment == "next_monday"


async def test_propose_update_recurring_transaction_external_apply_writes(
    session: AsyncSession, test_user, test_account
):
    """Seed a recurring tx directly, then drive an update via apply=True."""
    from datetime import date
    from decimal import Decimal
    from app.models.recurring_transaction import RecurringTransaction

    rt = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=test_account.id,
        description="Spotify",
        amount=Decimal("21.90"),
        currency="BRL",
        type="debit",
        frequency="monthly",
        day_of_month=15,
        start_date=date.today(),
        next_occurrence=date.today(),
        is_active=True,
    )
    session.add(rt)
    await session.commit()

    handler = REGISTRY["propose_update_recurring_transaction"].handler
    ctx = CallContext(user_id=test_user.id, external=True)

    result = await handler(
        session=session, ctx=ctx,
        recurring_id=str(rt.id),
        amount=27.90,
        weekend_adjustment="previous_friday",
        apply=True,
    )
    assert result.get("applied") is True

    await session.refresh(rt)
    assert float(rt.amount) == 27.90
    assert rt.weekend_adjustment == "previous_friday"


async def test_propose_cancel_recurring_transaction_deactivate_apply(
    session: AsyncSession, test_user, test_account
):
    """The default (and recommended) cancel mode flips is_active to False
    rather than deleting the row."""
    from datetime import date
    from decimal import Decimal
    from app.models.recurring_transaction import RecurringTransaction

    rt = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=test_account.id,
        description="Old subscription",
        amount=Decimal("10.00"),
        currency="BRL",
        type="debit",
        frequency="monthly",
        day_of_month=1,
        start_date=date.today(),
        next_occurrence=date.today(),
        is_active=True,
    )
    session.add(rt)
    await session.commit()

    handler = REGISTRY["propose_cancel_recurring_transaction"].handler
    ctx = CallContext(user_id=test_user.id, external=True)

    result = await handler(
        session=session, ctx=ctx,
        recurring_id=str(rt.id),
        mode="deactivate",
        apply=True,
    )
    assert result.get("applied") is True
    assert result.get("is_active") is False

    await session.refresh(rt)
    assert rt.is_active is False


async def test_propose_cancel_recurring_transaction_delete_apply(
    session: AsyncSession, test_user, test_account
):
    """The 'delete' mode removes the row entirely."""
    from datetime import date
    from decimal import Decimal
    from sqlalchemy import select
    from app.models.recurring_transaction import RecurringTransaction

    rt = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        account_id=test_account.id,
        description="Throwaway",
        amount=Decimal("5.00"),
        currency="BRL",
        type="debit",
        frequency="monthly",
        day_of_month=1,
        start_date=date.today(),
        next_occurrence=date.today(),
        is_active=True,
    )
    session.add(rt)
    await session.commit()
    rt_id = rt.id

    handler = REGISTRY["propose_cancel_recurring_transaction"].handler
    ctx = CallContext(user_id=test_user.id, external=True)

    result = await handler(
        session=session, ctx=ctx,
        recurring_id=str(rt_id),
        mode="delete",
        apply=True,
    )
    assert result.get("applied") is True
    assert result.get("deleted") is True

    gone = (await session.execute(
        select(RecurringTransaction).where(RecurringTransaction.id == rt_id)
    )).scalar_one_or_none()
    assert gone is None


async def test_propose_create_goal_external_apply_writes(
    session: AsyncSession, test_user
):
    from sqlalchemy import select
    from app.models.goal import Goal

    handler = REGISTRY["propose_create_goal"].handler
    ctx = CallContext(user_id=test_user.id, external=True)

    result = await handler(
        session=session, ctx=ctx,
        name="Travel fund",
        target_amount=10000.0,
        currency="BRL",
        apply=True,
    )
    assert result.get("applied") is True

    row = (await session.execute(
        select(Goal).where(Goal.id == uuid.UUID(result["id"]))
    )).scalar_one()
    assert row.name == "Travel fund"
    assert float(row.target_amount) == 10000.0


async def test_propose_create_payee_rule_external_apply_writes(
    session: AsyncSession, test_user, test_categories
):
    """The rule wrapper translates (match_pattern, category_id) into the
    conditions/actions shape RuleCreate expects."""
    from sqlalchemy import select
    from app.models.rule import Rule

    handler = REGISTRY["propose_create_payee_rule"].handler
    ctx = CallContext(user_id=test_user.id, external=True)
    cat = test_categories[0]

    result = await handler(
        session=session, ctx=ctx,
        match_pattern="UBER",
        category_id=str(cat.id),
        apply=True,
    )
    assert result.get("applied") is True

    row = (await session.execute(
        select(Rule).where(Rule.id == uuid.UUID(result["id"]))
    )).scalar_one()
    assert any(c.get("value") == "UBER" for c in row.conditions)
    assert any(a.get("value") == str(cat.id) for a in row.actions)
