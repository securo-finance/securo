#!/usr/bin/env python3
"""Seed stress-test data for performance benchmarking.

Must run inside the backend container (needs app module access):
    docker compose exec backend python scripts/seed_perf.py

Options:
    --scale FLOAT            Data volume multiplier (default: 1.0; use 0.1 for a quick smoke run)
    --email TEXT             Seed user email (default: test@securo.app)
    --password TEXT          Seed user password (default: Securo123!)
    --no-reset               Skip wiping existing data (default: wipe and re-seed)
    --start-date YYYY-MM-DD  Earliest date for seeded data (default: 2024-01-01)

At scale=1.0 seeds:
    8 accounts (4 credit cards) · 30 categories · 100 000 transactions
    3 asset wallet groups · 15 assets w/ daily values (10 grouped into wallets)
    FX rates for EUR + BRL · 20 recurring transactions
    (date range: --start-date through today)
"""
import argparse
import asyncio
import os
import random
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import NotRequired, TypedDict

# Allow running from repo root or backend root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.auth import UserManager
from app.core.database import async_session_maker
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_value import AssetValue
from app.models.budget import Budget
from app.models.category import Category
from app.models.goal import Goal
from app.models.group import Group, GroupMember
from app.models.group_settlement import GroupSettlement
from app.models.payee import Payee
from app.models.transaction_category_allocation import TransactionCategoryAllocation
from app.models.transaction_split import TransactionSplit
from app.models.fx_rate import FxRate
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.user import UserCreate
from app.services.workspace_service import create_personal_workspace_for_user
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.exceptions import UserAlreadyExists

# ---------------------------------------------------------------------------
# Seed templates
# ---------------------------------------------------------------------------

ACCOUNT_TEMPLATES = [
    {"name": "Main Checking", "type": "checking", "currency": "USD", "balance": Decimal("5000.00")},
    {"name": "Savings", "type": "savings", "currency": "USD", "balance": Decimal("25000.00")},
    {"name": "Visa Credit", "type": "credit_card", "currency": "USD", "balance": Decimal("0.00"), "credit_limit": Decimal("10000.00")},
    {"name": "Mastercard Platinum", "type": "credit_card", "currency": "USD", "balance": Decimal("0.00"), "credit_limit": Decimal("15000.00")},
    {"name": "Amex Gold", "type": "credit_card", "currency": "USD", "balance": Decimal("0.00"), "credit_limit": Decimal("20000.00")},
    {"name": "Euro Account", "type": "checking", "currency": "EUR", "balance": Decimal("3000.00")},
    {"name": "BRL Account", "type": "checking", "currency": "BRL", "balance": Decimal("15000.00")},
    {"name": "Euro Visa", "type": "credit_card", "currency": "EUR", "balance": Decimal("0.00"), "credit_limit": Decimal("8000.00")},
]

# (name, color, icon)  — first 22 are debit, last 8 are credit
CATEGORY_TEMPLATES = [
    ("Groceries", "#22c55e", "shopping-cart"),
    ("Rent", "#ef4444", "home"),
    ("Utilities", "#f97316", "zap"),
    ("Transport", "#3b82f6", "car"),
    ("Dining Out", "#ec4899", "utensils"),
    ("Entertainment", "#8b5cf6", "film"),
    ("Healthcare", "#06b6d4", "heart-pulse"),
    ("Insurance", "#64748b", "shield"),
    ("Education", "#f59e0b", "book"),
    ("Clothing", "#84cc16", "shirt"),
    ("Travel", "#0ea5e9", "plane"),
    ("Electronics", "#6366f1", "smartphone"),
    ("Sports", "#10b981", "dumbbell"),
    ("Personal Care", "#f43f5e", "sparkles"),
    ("Subscriptions", "#a855f7", "repeat"),
    ("Home Improvement", "#d97706", "wrench"),
    ("Gifts", "#e11d48", "gift"),
    ("Pets", "#65a30d", "paw-print"),
    ("Kids", "#0284c7", "baby"),
    ("Taxes", "#dc2626", "landmark"),
    ("Fees", "#475569", "circle-dollar-sign"),
    ("Miscellaneous", "#6b7280", "circle-help"),
    # Credit / income
    ("Salary", "#16a34a", "briefcase"),
    ("Freelance", "#15803d", "laptop"),
    ("Investment Income", "#166534", "trending-up"),
    ("Rental Income", "#14532d", "building"),
    ("Bonus", "#065f46", "star"),
    ("Refunds", "#0f766e", "undo"),
    ("Other Income", "#1e40af", "plus"),
    ("Gifts Received", "#0369a1", "gift"),
]
N_DEBIT_CATS = 22  # first N_DEBIT_CATS entries are debit categories

PAYEES = [
    "Amazon", "Walmart", "Target", "Costco", "Whole Foods", "Trader Joe's",
    "Netflix", "Spotify", "Apple", "Google", "Microsoft", "Adobe",
    "Shell", "BP", "Uber", "Lyft", "Delta Airlines", "United Airlines",
    "Marriott", "Hilton", "Home Depot", "IKEA", "CVS", "Walgreens",
    "Doctor Smith", "City Hospital", "LA Fitness", "Planet Fitness",
    "PG&E", "Comcast", "AT&T", "Verizon", "State Farm", "Geico",
]

# Wallet groups to create. Each asset below can reference one by the "group" key.
ASSET_GROUP_TEMPLATES = [
    {"name": "Stock Portfolio",  "icon": "trending-up", "color": "#22c55e"},
    {"name": "Real Estate",      "icon": "home",        "color": "#f97316"},
    {"name": "Collectibles",     "icon": "gem",         "color": "#a855f7"},
]

class AssetTemplate(TypedDict):
    name: str
    type: str
    currency: str
    purchase: Decimal
    base: Decimal
    daily_pct: Decimal
    group: NotRequired[str]


ASSET_TEMPLATES: list[AssetTemplate] = [
    {"name": "Primary Residence",   "type": "real_estate",  "currency": "USD", "purchase": Decimal("450000"), "base": Decimal("520000"), "daily_pct": Decimal("0.00110"),  "group": "Real Estate"},
    {"name": "Investment Property", "type": "real_estate",  "currency": "USD", "purchase": Decimal("280000"), "base": Decimal("310000"), "daily_pct": Decimal("0.00082"),  "group": "Real Estate"},
    {"name": "Tesla Model 3",       "type": "vehicle",      "currency": "USD", "purchase": Decimal("45000"),  "base": Decimal("28000"),  "daily_pct": Decimal("-0.00219")},
    {"name": "Rolex Watch",         "type": "valuable",     "currency": "USD", "purchase": Decimal("8000"),   "base": Decimal("9500"),   "daily_pct": Decimal("0.00055"),  "group": "Collectibles"},
    {"name": "S&P 500 Index",       "type": "investment",   "currency": "USD", "purchase": Decimal("50000"),  "base": Decimal("75000"),  "daily_pct": Decimal("0.00219"),  "group": "Stock Portfolio"},
    {"name": "Tech Stock Portfolio","type": "investment",   "currency": "USD", "purchase": Decimal("30000"),  "base": Decimal("42000"),  "daily_pct": Decimal("0.00274"),  "group": "Stock Portfolio"},
    {"name": "Bond Portfolio",      "type": "investment",   "currency": "USD", "purchase": Decimal("20000"),  "base": Decimal("22000"),  "daily_pct": Decimal("0.00082"),  "group": "Stock Portfolio"},
    {"name": "Crypto Portfolio",    "type": "investment",   "currency": "USD", "purchase": Decimal("10000"),  "base": Decimal("15000"),  "daily_pct": Decimal("0.00548")},
    {"name": "Vacation Cabin",      "type": "real_estate",  "currency": "USD", "purchase": Decimal("180000"), "base": Decimal("210000"), "daily_pct": Decimal("0.00137"),  "group": "Real Estate"},
    {"name": "BMW 5 Series",        "type": "vehicle",      "currency": "USD", "purchase": Decimal("55000"),  "base": Decimal("35000"),  "daily_pct": Decimal("-0.00247")},
    {"name": "Art Collection",      "type": "valuable",     "currency": "USD", "purchase": Decimal("15000"),  "base": Decimal("18000"),  "daily_pct": Decimal("0.00082"),  "group": "Collectibles"},
    {"name": "Gold Bars",           "type": "valuable",     "currency": "USD", "purchase": Decimal("25000"),  "base": Decimal("30000"),  "daily_pct": Decimal("0.00110"),  "group": "Collectibles"},
    {"name": "EU Real Estate",      "type": "real_estate",  "currency": "EUR", "purchase": Decimal("300000"), "base": Decimal("340000"), "daily_pct": Decimal("0.00110"),  "group": "Real Estate"},
    {"name": "Emerging Markets ETF","type": "investment",   "currency": "USD", "purchase": Decimal("15000"),  "base": Decimal("18000"),  "daily_pct": Decimal("0.00164"),  "group": "Stock Portfolio"},
    {"name": "Private Equity Fund", "type": "investment",   "currency": "USD", "purchase": Decimal("100000"), "base": Decimal("125000"), "daily_pct": Decimal("0.00192"),  "group": "Stock Portfolio"},
]

RECURRING_DESCRIPTIONS = [
    "Netflix", "Spotify", "Rent Payment", "Gym Membership", "Car Insurance",
    "Phone Bill", "Internet", "Electric Bill", "Water Bill", "Gas Bill",
    "Amazon Prime", "iCloud Storage", "LinkedIn Premium", "YouTube Premium", "Adobe Creative Cloud",
    "Mortgage", "Student Loan", "Car Payment", "Health Insurance", "Life Insurance",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_date_range(days: int) -> list[date]:
    today = date.today()
    return [today - timedelta(days=d) for d in range(days, -1, -1)]


async def _wipe_user_data(session, user_id: uuid.UUID) -> None:
    await session.execute(delete(RecurringTransaction).where(RecurringTransaction.user_id == user_id))
    await session.execute(
        delete(AssetValue).where(
            AssetValue.asset_id.in_(select(Asset.id).where(Asset.user_id == user_id))
        )
    )
    await session.execute(delete(Asset).where(Asset.user_id == user_id))
    await session.execute(delete(AssetGroup).where(AssetGroup.user_id == user_id))
    await session.execute(delete(Budget).where(Budget.user_id == user_id))
    await session.execute(delete(Goal).where(Goal.user_id == user_id))
    user_tx_ids = select(Transaction.id).where(Transaction.user_id == user_id)
    await session.execute(delete(TransactionCategoryAllocation).where(
        TransactionCategoryAllocation.transaction_id.in_(user_tx_ids)))
    await session.execute(delete(TransactionSplit).where(TransactionSplit.transaction_id.in_(user_tx_ids)))
    user_group_ids = select(Group.id).where(Group.user_id == user_id)
    await session.execute(delete(GroupSettlement).where(GroupSettlement.group_id.in_(user_group_ids)))
    await session.execute(delete(GroupMember).where(GroupMember.group_id.in_(user_group_ids)))
    await session.execute(delete(Group).where(Group.user_id == user_id))
    await session.execute(delete(Transaction).where(Transaction.user_id == user_id))
    await session.execute(delete(Payee).where(Payee.user_id == user_id))
    await session.execute(delete(Category).where(Category.user_id == user_id))
    await session.execute(delete(Account).where(Account.user_id == user_id))
    await session.commit()


# ---------------------------------------------------------------------------
# Feature data: budgets, goals, payees, groups, category splits
# ---------------------------------------------------------------------------

BUDGET_MONTHS = 12
BUDGET_AMOUNTS = {
    "Rent": Decimal("1500"), "Groceries": Decimal("600"), "Utilities": Decimal("250"),
    "Transport": Decimal("300"), "Dining Out": Decimal("400"), "Entertainment": Decimal("150"),
    "Healthcare": Decimal("200"), "Subscriptions": Decimal("120"), "Clothing": Decimal("150"),
    "Travel": Decimal("500"),
}

GOAL_TEMPLATES: list[dict] = [
    {"name": "Emergency fund", "target_amount": Decimal("15000"), "current_amount": Decimal("6200"),
     "initial_amount": Decimal("1000"), "tracking_type": "manual", "target_date": date(2027, 6, 30),
     "icon": "shield", "color": "#22c55e"},
    {"name": "Japan trip", "target_amount": Decimal("8000"), "current_amount": Decimal("0"),
     "initial_amount": Decimal("0"), "tracking_type": "account", "account": "Savings",
     "target_date": date(2027, 3, 1), "icon": "plane", "color": "#0ea5e9"},
    {"name": "New laptop", "target_amount": Decimal("3000"), "current_amount": Decimal("3000"),
     "initial_amount": Decimal("0"), "tracking_type": "manual", "status": "completed",
     "icon": "smartphone", "color": "#6366f1"},
    {"name": "Car down payment", "target_amount": Decimal("20000"), "current_amount": Decimal("4500"),
     "initial_amount": Decimal("0"), "tracking_type": "manual", "target_date": date(2028, 1, 1),
     "icon": "car", "color": "#f97316"},
]

# (group name, member names besides "Me")
GROUP_TEMPLATES = [
    ("Roommates", ["Ana", "Bruno"]),
    ("Trip to Lisbon", ["Carla", "Diego", "Elisa"]),
]

# Volume at scale=1.0; multiplied by --scale like the other counts.
GROUP_SPLIT_TX_AT_SCALE_1 = 300
CATEGORY_SPLIT_TX_AT_SCALE_1 = 2500
GROUP_SPLIT_MIN_AMOUNT = Decimal("30")
CATEGORY_SPLIT_MIN_AMOUNT = Decimal("40")
CATEGORY_SPLIT_RATIOS = [0.25, 0.4, 0.5, 0.6, 0.75]


def _month_starts(today: date, n: int) -> list[date]:
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append(date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


async def _seed_budgets(session, uid, wid, categories_by_name: dict, today: date) -> int:
    rows = 0
    for name, amount in BUDGET_AMOUNTS.items():
        cat = categories_by_name.get(name)
        if cat is None:
            continue
        for month in _month_starts(today, BUDGET_MONTHS):
            session.add(Budget(user_id=uid, workspace_id=wid, category_id=cat.id, amount=amount,
                               month=month, is_recurring=False, currency="USD"))
            rows += 1
    await session.flush()
    return rows


async def _seed_goals(session, uid, wid, accounts_by_name: dict) -> int:
    rows = 0
    for i, tmpl in enumerate(GOAL_TEMPLATES):
        fields = dict(tmpl)
        account_name = fields.pop("account", None)
        account = accounts_by_name.get(account_name) if account_name else None
        if account_name and account is None:
            continue
        session.add(Goal(user_id=uid, workspace_id=wid, currency="USD", position=i,
                         account_id=account.id if account else None, **fields))
        rows += 1
    await session.flush()
    return rows


async def _seed_payees(session, uid, wid, payee_names: list[str]) -> int:
    for name in sorted(set(payee_names)):
        payee = Payee(user_id=uid, workspace_id=wid, name=name, source="manual")
        session.add(payee)
        await session.flush()
        await session.execute(
            update(Transaction)
            .where(Transaction.user_id == uid, Transaction.payee == name)
            .values(payee_id=payee.id)
        )
    return len(set(payee_names))


async def _seed_groups(session, uid, wid, rng: random.Random, scale: float) -> int:
    groups: list[tuple[Group, list[GroupMember]]] = []
    for gname, others in GROUP_TEMPLATES:
        group = Group(user_id=uid, workspace_id=wid, name=gname, kind="social", default_currency="USD")
        session.add(group)
        await session.flush()
        members = [GroupMember(group_id=group.id, workspace_id=wid, name="Me", is_self=True)]
        members += [GroupMember(group_id=group.id, workspace_id=wid, name=o) for o in others]
        session.add_all(members)
        await session.flush()
        groups.append((group, members))

    candidates = (await session.execute(
        select(Transaction).where(
            Transaction.user_id == uid,
            Transaction.type == "debit",
            Transaction.amount >= GROUP_SPLIT_MIN_AMOUNT,
            Transaction.transfer_pair_id.is_(None),
        ).order_by(Transaction.date)
    )).scalars().all()
    n_split = min(len(candidates), max(1, int(GROUP_SPLIT_TX_AT_SCALE_1 * scale)))
    for tx in rng.sample(candidates, n_split):
        _, members = rng.choice(groups)
        k = len(members)
        base = (tx.amount / k).quantize(Decimal("0.01"))
        for idx, member in enumerate(members):
            share = base if idx < k - 1 else tx.amount - base * (k - 1)
            session.add(TransactionSplit(transaction_id=tx.id, workspace_id=wid,
                                         group_member_id=member.id, share_amount=share,
                                         share_type="equal"))

    group, members = groups[0]
    session.add_all([
        GroupSettlement(group_id=group.id, workspace_id=wid, from_member_id=members[1].id,
                        to_member_id=members[0].id, amount=Decimal("85.50"), currency="USD",
                        date=date(2026, 9, 15), notes="Rent share"),
        GroupSettlement(group_id=group.id, workspace_id=wid, from_member_id=members[0].id,
                        to_member_id=members[-1].id, amount=Decimal("40.00"), currency="USD",
                        date=date(2026, 10, 2), notes="Groceries"),
    ])
    await session.flush()
    return n_split


async def _seed_category_splits(session, uid, wid, rng: random.Random, scale: float, debit_cats: list[Category]) -> int:
    candidates = (await session.execute(
        select(Transaction).where(
            Transaction.user_id == uid,
            Transaction.type == "debit",
            Transaction.amount >= CATEGORY_SPLIT_MIN_AMOUNT,
            Transaction.transfer_pair_id.is_(None),
        ).order_by(Transaction.date)
    )).scalars().all()
    n_split = min(len(candidates), max(1, int(CATEGORY_SPLIT_TX_AT_SCALE_1 * scale)))
    for tx in rng.sample(candidates, n_split):
        first_cat, second_cat = rng.sample(debit_cats, 2)
        first = (tx.amount * Decimal(str(rng.choice(CATEGORY_SPLIT_RATIOS)))).quantize(Decimal("0.01"))
        session.add(TransactionCategoryAllocation(transaction_id=tx.id, workspace_id=wid,
                                                  category_id=first_cat.id, amount=first, position=0))
        session.add(TransactionCategoryAllocation(transaction_id=tx.id, workspace_id=wid,
                                                  category_id=second_cat.id, amount=tx.amount - first,
                                                  notes="Split demo", position=1))
    await session.flush()
    return n_split


async def _seed_feature_data(session, uid, wid, rng: random.Random, scale: float, today: date,
                             accounts: list[Account], categories: list[Category],
                             debit_cats: list[Category]) -> None:
    categories_by_name = {c.name: c for c in categories}
    accounts_by_name = {a.name: a for a in accounts}
    n_budgets = await _seed_budgets(session, uid, wid, categories_by_name, today)
    print(f"  {n_budgets} budgets")
    n_goals = await _seed_goals(session, uid, wid, accounts_by_name)
    print(f"  {n_goals} goals")
    n_payees = await _seed_payees(session, uid, wid, PAYEES)
    print(f"  {n_payees} payees")
    n_group_txs = await _seed_groups(session, uid, wid, rng, scale)
    print(f"  {len(GROUP_TEMPLATES)} groups, {n_group_txs} group split transactions, 2 settlements")
    n_cat_splits = await _seed_category_splits(session, uid, wid, rng, scale, debit_cats)
    print(f"  {n_cat_splits} category-split transactions")


# ---------------------------------------------------------------------------
# Main seeder
# ---------------------------------------------------------------------------

async def seed(
    email: str,
    password: str,
    scale: float,
    reset: bool,
    start_date: date,
    n_accounts: int,
    n_categories: int,
    n_assets: int,
) -> None:
    rng = random.Random(42)
    today = date.today()

    async with async_session_maker() as session:

        # ── 1. User ──────────────────────────────────────────────────────────
        user = await session.scalar(select(User).where(User.email == email))

        if user and not reset:
            tx_count = await session.scalar(
                select(Transaction).where(Transaction.user_id == user.id).limit(1)
            )
            if tx_count is not None:
                print(f"User {email} already has data and --no-reset is set. Nothing to do.")
                return
        elif user and reset:
            print(f"Wiping existing data for {email} …")
            await _wipe_user_data(session, user.id)

        if not user:
            print(f"Creating user {email} …")
            user_db = SQLAlchemyUserDatabase(session, User)
            manager = UserManager(user_db)
            try:
                user = await manager.create(UserCreate(email=email, password=password))
            except UserAlreadyExists:
                user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                raise RuntimeError(f"Failed to create or look up user {email}")
            await session.commit()
            print(f"  Created {user.id}")
        else:
            print(f"Using existing user {user.id}")

        uid = user.id

        # Ensure the user has a personal workspace (on_after_register skips this
        # for programmatic calls where request=None, so we create it here).
        workspace = await create_personal_workspace_for_user(session, user, commit=True)
        wid = workspace.id

        # ── 2. Accounts ──────────────────────────────────────────────────────
        print("Creating accounts …")
        accounts: list[Account] = []
        for tmpl in ACCOUNT_TEMPLATES[:n_accounts]:
            acc = Account(
                user_id=uid,
                workspace_id=wid,
                name=tmpl["name"],
                type=tmpl["type"],
                currency=tmpl["currency"],
                balance=tmpl["balance"],
                credit_limit=tmpl.get("credit_limit"),
            )
            session.add(acc)
            accounts.append(acc)
        await session.flush()
        print(f"  {len(accounts)} accounts")

        # ── 3. Categories ────────────────────────────────────────────────────
        print("Creating categories …")
        categories: list[Category] = []
        for name, color, icon in CATEGORY_TEMPLATES[:n_categories]:
            cat = Category(user_id=uid, workspace_id=wid, name=name, color=color, icon=icon)
            session.add(cat)
            categories.append(cat)
        await session.flush()
        debit_cats = categories[:N_DEBIT_CATS]
        credit_cats = categories[N_DEBIT_CATS:] or debit_cats  # fallback if slice has no credit cats
        print(f"  {len(categories)} categories")

        # ── 4. FX Rates ──────────────────────────────────────────────────────
        n_days = max(1, (today - start_date).days)
        print(f"Seeding FX rates for {n_days} days ({start_date} → {today}) …")
        all_dates = _make_date_range(n_days)

        # rates are USD-quoted: how many of quote_currency per 1 USD
        eur_rate = Decimal("0.9200")   # ~0.92 EUR per USD
        brl_rate = Decimal("5.0000")   # ~5.0 BRL per USD
        fx_rows = []
        for d in all_dates:
            eur_rate = max(
                Decimal("0.75"),
                min(Decimal("1.05"), eur_rate + Decimal(str(round(rng.gauss(0, 0.002), 6)))),
            )
            brl_rate = max(
                Decimal("4.00"),
                min(Decimal("6.50"), brl_rate + Decimal(str(round(rng.gauss(0, 0.02), 6)))),
            )
            fx_rows.append({"base_currency": "USD", "quote_currency": "EUR", "date": d,
                             "rate": eur_rate.quantize(Decimal("0.000001")), "source": "seed"})
            fx_rows.append({"base_currency": "USD", "quote_currency": "BRL", "date": d,
                             "rate": brl_rate.quantize(Decimal("0.000001")), "source": "seed"})

        for i in range(0, len(fx_rows), 2000):
            stmt = pg_insert(FxRate).values(fx_rows[i : i + 2000])
            stmt = stmt.on_conflict_do_update(
                constraint="uq_fx_rate_base_quote_date",
                set_={"rate": stmt.excluded.rate, "source": stmt.excluded.source},
            )
            await session.execute(stmt)
        await session.commit()
        print(f"  {len(fx_rows)} FX rate rows")

        # ── 5. Transactions ──────────────────────────────────────────────────
        n_tx = max(1, int(100_000 * scale))
        print(f"Inserting {n_tx:,} transactions …")
        tx_rows = []

        for _ in range(n_tx):
            acc = rng.choice(accounts)
            is_debit = rng.random() < 0.75
            cat = rng.choice(debit_cats if is_debit else credit_cats)
            tx_date = start_date + timedelta(days=rng.randint(0, n_days))
            if is_debit:
                amount = Decimal(str(round(abs(rng.lognormvariate(3.5, 1.2)), 2)))
                amount = min(amount, Decimal("5000.00"))
            else:
                amount = Decimal(str(round(abs(rng.lognormvariate(6.5, 0.8)), 2)))

            tx_rows.append({
                "id": uuid.uuid4(),
                "user_id": uid,
                "workspace_id": wid,
                "account_id": acc.id,
                "category_id": cat.id,
                "description": rng.choice(PAYEES),
                "amount": amount,
                "currency": acc.currency,
                "date": tx_date,
                "effective_date": tx_date,
                "type": "debit" if is_debit else "credit",
                "source": "manual",
                "status": "posted",
                "payee": rng.choice(PAYEES),
            })

        chunk = 1000  # asyncpg limit: 32767 params / ~17 cols per tx ≈ 1900 max; 2000 overflows
        for i in range(0, len(tx_rows), chunk):
            await session.execute(pg_insert(Transaction).values(tx_rows[i : i + chunk]))
            done = min(i + chunk, n_tx)
            print(f"  {done:,}/{n_tx:,}", end="\r", flush=True)
        await session.commit()
        print(f"  {n_tx:,} transactions done        ")

        # ── 6. Asset Groups (wallets) ────────────────────────────────────────
        print("Creating asset groups (wallets) …")
        asset_groups: dict[str, AssetGroup] = {}
        for i, gtmpl in enumerate(ASSET_GROUP_TEMPLATES):
            grp = AssetGroup(
                user_id=uid,
                workspace_id=wid,
                name=gtmpl["name"],
                icon=gtmpl["icon"],
                color=gtmpl["color"],
                position=i,
                source="manual",
            )
            session.add(grp)
            asset_groups[gtmpl["name"]] = grp
        await session.flush()
        print(f"  {len(asset_groups)} asset groups")

        # ── 7. Assets + AssetValues ──────────────────────────────────────────
        print(f"Creating {len(ASSET_TEMPLATES[:n_assets])} assets with {n_days + 1} daily values each …")
        asset_days = _make_date_range(n_days)

        for tmpl in ASSET_TEMPLATES[:n_assets]:
            grp = asset_groups.get(tmpl.get("group", ""))
            asset = Asset(
                user_id=uid,
                workspace_id=wid,
                name=tmpl["name"],
                type=tmpl["type"],
                currency=tmpl["currency"],
                purchase_price=tmpl["purchase"],
                purchase_date=asset_days[0],
                valuation_method="manual",
                group_id=grp.id if grp else None,
            )
            session.add(asset)
            await session.flush()

            daily_pct = tmpl["daily_pct"]
            value_rows = []
            # Walk backwards so oldest value starts at base and grows to today
            value_sequence = []
            v = tmpl["base"]
            for _ in asset_days:
                noise = Decimal(str(round(rng.gauss(0, float(v) * 0.004), 2)))
                value_sequence.append(max(Decimal("1.00"), (v + noise).quantize(Decimal("0.01"))))
                v = v * (1 + daily_pct)

            for d, v in zip(asset_days, value_sequence):
                value_rows.append({
                    "id": uuid.uuid4(),
                    "asset_id": asset.id,
                    "workspace_id": wid,
                    "amount": v,
                    "date": d,
                    "source": "manual",
                })

            for i in range(0, len(value_rows), 2000):
                await session.execute(pg_insert(AssetValue).values(value_rows[i : i + 2000]))

        await session.commit()
        total_av = len(ASSET_TEMPLATES[:n_assets]) * (n_days + 1)
        print(f"  {len(ASSET_TEMPLATES)} assets, {total_av:,} asset values")

        # ── 8. Recurring transactions ────────────────────────────────────────
        n_rec = max(1, int(20 * scale))
        print(f"Creating {n_rec} recurring transactions …")
        for i, desc in enumerate(RECURRING_DESCRIPTIONS[:n_rec]):
            acc = accounts[i % len(accounts)]
            cat = debit_cats[i % len(debit_cats)]
            session.add(RecurringTransaction(
                user_id=uid,
                workspace_id=wid,
                account_id=acc.id,
                category_id=cat.id,
                description=desc,
                amount=Decimal(str(round(rng.uniform(10.0, 500.0), 2))),
                currency=acc.currency,
                type="debit",
                frequency="monthly",
                day_of_month=rng.randint(1, 28),
                start_date=today - timedelta(days=365),
                next_occurrence=today + timedelta(days=rng.randint(1, 30)),
                is_active=True,
            ))
        await session.commit()
        print(f"  {n_rec} recurring transactions")

        # ── 9. Feature data (budgets, goals, payees, groups, splits) ─────────
        print("Creating feature data (budgets, goals, payees, groups, splits) …")
        await _seed_feature_data(session, uid, wid, rng, scale, today, accounts, categories, debit_cats)
        await session.commit()

    print("\nSeed complete.")
    print(f"  Login : {email}")
    print(f"  Passwd: {password}")


# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Seed performance stress-test data")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="Volume multiplier (default 1.0; 0.1 for quick smoke run)")
    parser.add_argument("--email", default="test@securo.app")
    parser.add_argument("--password", default="Securo123!")
    parser.add_argument("--no-reset", dest="reset", action="store_false", default=True,
                        help="Skip wiping existing seed data for this user")
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2024, 1, 1),
                        metavar="YYYY-MM-DD", help="Earliest date for seeded data (default: 2024-01-01)")
    parser.add_argument("--accounts", type=int, default=len(ACCOUNT_TEMPLATES),
                        help=f"Number of accounts to create (default: {len(ACCOUNT_TEMPLATES)}, max: {len(ACCOUNT_TEMPLATES)})")
    parser.add_argument("--categories", type=int, default=len(CATEGORY_TEMPLATES),
                        help=f"Number of categories to create (default: {len(CATEGORY_TEMPLATES)}, max: {len(CATEGORY_TEMPLATES)})")
    parser.add_argument("--assets", type=int, default=len(ASSET_TEMPLATES),
                        help=f"Number of assets to create (default: {len(ASSET_TEMPLATES)}, max: {len(ASSET_TEMPLATES)})")
    args = parser.parse_args()
    asyncio.run(seed(
        args.email, args.password, args.scale, args.reset, args.start_date,
        args.accounts, args.categories, args.assets,
    ))


if __name__ == "__main__":
    main()
