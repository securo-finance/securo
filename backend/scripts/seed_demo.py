#!/usr/bin/env python3
"""Seed showcase data for the public demo.

Unlike scripts/seed_perf.py (which generates large random datasets for
benchmarking), this script produces a small, realistic, browse-friendly
dataset: a handful of accounts, six months of believable transactions,
real-ticker stock holdings, recurring bills, and two in-progress goals.

Intended to be invoked from reset_demo.sh on an hourly cron.

Run inside the backend container:
    docker compose exec backend python scripts/seed_demo.py
"""
import argparse
import asyncio
import os
import random
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.auth import UserManager
from app.core.database import async_session_maker
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.budget import Budget
from app.models.category import Category
from app.models.fx_rate import FxRate
from app.models.goal import Goal
from app.models.group import Group, GroupMember
from app.models.recurring_transaction import RecurringTransaction
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.transaction_split import TransactionSplit
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.schemas.user import UserCreate
from app.services.workspace_service import create_personal_workspace_for_user
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.exceptions import UserAlreadyExists


# ---------------------------------------------------------------------------
# Showcase templates — Brazilian context (BRL primary), small + recognizable
# ---------------------------------------------------------------------------

# Logo URLs are pre-baked using the same Google favicon service the
# app's market-price provider uses, so demo assets look real even with
# the live refresh task short-circuited by DEMO_MODE.
def _logo(domain: str) -> str:
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=128"


# `opening` is seeded as a one-time deposit transaction at start so the
# computed current-balance reflects the intended starting amount even on
# accounts with little/no other activity (the app calculates balances
# from transactions, not from the Account.balance field).
ACCOUNTS = [
    {"name": "Conta Nubank",        "type": "checking",    "currency": "BRL", "opening": Decimal("0.00")},
    {"name": "Poupança Itaú",       "type": "savings",     "currency": "BRL", "opening": Decimal("6000.00")},
    {"name": "Cartão Nubank",       "type": "credit_card", "currency": "BRL", "opening": Decimal("0.00"), "credit_limit": Decimal("3000.00")},
    {"name": "Carteira USD",        "type": "checking",    "currency": "USD", "opening": Decimal("280.00")},
]

# Icon names must be valid keys in frontend/src/lib/category-icons.ts
# ICON_MAP — anything else renders as the "?" fallback.
CATEGORIES = [
    ("Alimentação",   "#22c55e", "shopping-cart",     "debit"),
    ("Aluguel",       "#ef4444", "house",             "debit"),
    ("Contas",        "#f97316", "lightbulb",         "debit"),
    ("Transporte",    "#3b82f6", "car",               "debit"),
    ("Restaurantes",  "#ec4899", "utensils-crossed",  "debit"),
    ("Lazer",         "#8b5cf6", "film",              "debit"),
    ("Saúde",         "#06b6d4", "heart",             "debit"),
    ("Assinaturas",   "#a855f7", "credit-card",       "debit"),
    ("Compras",       "#84cc16", "shirt",             "debit"),
    ("Viagem",        "#0ea5e9", "plane",             "debit"),
    ("Presentes",     "#e11d48", "gift",              "debit"),
    ("Tarifas",       "#475569", "receipt",           "debit"),
    ("Outros",        "#6b7280", "circle-help",       "debit"),
    ("Salário",       "#16a34a", "briefcase",         "credit"),
    ("Freelance",     "#15803d", "graduation-cap",    "credit"),
    ("Reembolsos",    "#0f766e", "arrow-left-right",  "credit"),
    ("Dividendos",    "#166534", "trending-up",       "credit"),
    ("Bônus",         "#065f46", "sparkles",          "credit"),
]

# Brazilian merchants mapped to each category. Amounts are in BRL except
# the Viagem (Travel) bucket which leans on the USD account abroad.
# Amounts scaled to a typical Brazilian middle-class profile: ~R$3,300
# monthly salary, ~R$950 rent, modest discretionary spending. Real
# subscription prices (Netflix R$39.90 etc.) are kept exact.
DEBIT_MERCHANTS: dict[str, list[tuple[str, float, float]]] = {
    "Alimentação":   [("Pão de Açúcar", 60, 180), ("Carrefour", 75, 215), ("Hortifruti", 20, 60), ("Extra", 50, 130)],
    "Aluguel":       [("Aluguel", 950, 950)],
    "Contas":        [("Enel", 60, 110), ("Vivo", 50, 50), ("Net Claro", 80, 80), ("Sabesp", 30, 50)],
    "Transporte":    [("Uber", 5, 22), ("99", 4, 15), ("Ipiranga", 60, 110), ("Shell", 70, 120)],
    "Restaurantes":  [("iFood", 13, 40), ("Outback", 50, 95), ("Coco Bambu", 60, 120), ("Starbucks", 7, 16)],
    "Lazer":         [("Cinemark", 15, 30), ("Steam", 20, 75), ("Ingresso.com", 27, 60)],
    "Saúde":         [("Drogasil", 12, 40), ("Drogaria São Paulo", 10, 35), ("Consulta Médica", 75, 160)],
    "Assinaturas":   [("Netflix", 39.90, 39.90), ("Spotify", 21.90, 21.90), ("Disney+", 33.90, 33.90), ("Amazon Prime", 14.90, 14.90)],
    "Compras":       [("Mercado Livre", 15, 160), ("Magazine Luiza", 40, 225), ("Amazon", 20, 180), ("Renner", 30, 140)],
    "Viagem":        [("Latam", 160, 600), ("Booking", 105, 305), ("Decolar", 95, 365)],
    "Presentes":     [("Elo7", 25, 80), ("Amazon Presentes", 20, 95)],
    "Tarifas":       [("Tarifa Nubank", 9, 19), ("IOF", 4, 12)],
    "Outros":        [("Apple Store", 30, 160), ("Correios", 6, 20), ("Kalunga", 10, 60)],
}

CREDIT_MERCHANTS: dict[str, list[tuple[str, float, float]]] = {
    "Salário":     [("Empresa Folha Pagamento", 3300, 3300)],
    "Freelance":   [("Pagamento Freelance", 400, 1400)],
    "Reembolsos":  [("Reembolso Mercado Livre", 15, 75)],
    "Dividendos":  [("Dividendo ITUB4", 12, 30), ("Dividendo PETR4", 40, 95), ("Dividendo MGLU3", 4, 10)],
    "Bônus":       [("PLR", 1400, 1400)],
}

# Market-priced holdings — mix of B3 (Brazilian) tickers and one US stock
# so the FX side of the product also shows up. last_price + logo_url are
# pre-baked so the UI matches what a fresh yfinance fetch would produce.
TICKER_ASSETS = [
    {"name": "Itaú Unibanco",     "ticker": "ITUB4.SA", "units": Decimal("250"),    "last_price": Decimal("32.40"),    "currency": "BRL", "daily_pct": Decimal("0.00100"), "logo": _logo("itau.com.br")},
    {"name": "Petrobras",         "ticker": "PETR4.SA", "units": Decimal("150"),    "last_price": Decimal("38.20"),    "currency": "BRL", "daily_pct": Decimal("0.00080"), "logo": _logo("petrobras.com.br")},
    {"name": "Magazine Luiza",    "ticker": "MGLU3.SA", "units": Decimal("400"),    "last_price": Decimal("8.95"),     "currency": "BRL", "daily_pct": Decimal("0.00200"), "logo": _logo("magazineluiza.com.br")},
    {"name": "Apple Inc.",        "ticker": "AAPL",     "units": Decimal("3"),      "last_price": Decimal("228.50"),   "currency": "USD", "daily_pct": Decimal("0.00150"), "logo": _logo("apple.com")},
    {"name": "Bitcoin",           "ticker": "BTC-USD",  "units": Decimal("0.02"),   "last_price": Decimal("92500.00"), "currency": "USD", "daily_pct": Decimal("0.00400"), "logo": None},
]

MANUAL_ASSETS = [
    {"name": "Apartamento próprio", "type": "real_estate", "currency": "BRL",
     "purchase": Decimal("260000"), "current": Decimal("320000"), "daily_pct": Decimal("0.00040")},
]

RECURRING = [
    ("Netflix",   Decimal("39.90"),  "debit",  "Assinaturas"),
    ("Spotify",   Decimal("21.90"),  "debit",  "Assinaturas"),
    ("Aluguel",   Decimal("950.00"), "debit",  "Aluguel"),
    ("Smart Fit", Decimal("99.90"),  "debit",  "Saúde"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _date_range(start: date, end: date) -> list[date]:
    days = (end - start).days
    return [start + timedelta(days=i) for i in range(days + 1)]


async def _wipe(session, user_id: uuid.UUID) -> None:
    """Wipe everything we seed (idempotent — safe to re-run)."""
    await session.execute(delete(Goal).where(Goal.user_id == user_id))
    await session.execute(delete(Budget).where(Budget.user_id == user_id))
    await session.execute(delete(Rule).where(Rule.user_id == user_id))
    await session.execute(delete(RecurringTransaction).where(RecurringTransaction.user_id == user_id))
    await session.execute(
        delete(AssetValue).where(
            AssetValue.asset_id.in_(select(Asset.id).where(Asset.user_id == user_id))
        )
    )
    await session.execute(delete(Asset).where(Asset.user_id == user_id))
    # TransactionSplit + Transaction first (FK from splits → transactions).
    # Splits cascade on transaction delete, so deleting transactions is
    # enough — but groups + members need to go before transactions in
    # case any split's group_member_id FK is RESTRICT.
    await session.execute(delete(Transaction).where(Transaction.user_id == user_id))
    await session.execute(delete(Group).where(Group.user_id == user_id))
    await session.execute(delete(Category).where(Category.user_id == user_id))
    await session.execute(delete(Account).where(Account.user_id == user_id))
    await session.commit()


async def _resolve_demo_workspace(session, user: User) -> uuid.UUID:
    """The workspace every seeded row belongs to.

    Mirrors app.core.workspace_autostamp._resolve_workspace_for_user: the
    user's oldest non-archived workspace. Falls back to creating the personal
    workspace on a fresh database.
    """
    row = (await session.execute(
        select(Workspace.id)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == user.id,
            Workspace.is_archived.is_(False),
        )
        .order_by(Workspace.created_at.asc())
        .limit(1)
    )).first()
    if row:
        # A visitor may have renamed it; put it back so the demo reads right.
        await session.execute(
            Workspace.__table__.update()
            .where(Workspace.id == row[0])
            .values(name="Pessoal", kind="personal")
        )
        return row[0]
    workspace = await create_personal_workspace_for_user(session, user)
    return workspace.id


# ---------------------------------------------------------------------------
# Main seeder
# ---------------------------------------------------------------------------

async def seed(email: str, password: str, months: int) -> None:
    rng = random.Random(42)
    today = date.today()
    start = today - timedelta(days=30 * months)

    async with async_session_maker() as session:
        # 1. User -----------------------------------------------------------
        user = await session.scalar(select(User).where(User.email == email))
        if user:
            print(f"Wiping existing data for {email} …")
            await _wipe(session, user.id)
        else:
            print(f"Creating user {email} …")
            user_db = SQLAlchemyUserDatabase(session, User)
            manager = UserManager(user_db)
            try:
                user = await manager.create(UserCreate(email=email, password=password))
            except UserAlreadyExists:
                user = await session.scalar(select(User).where(User.email == email))
            await session.commit()

        # Force Brazilian preferences regardless of whether the user
        # already existed — keeps demo behaviour stable across resets.
        user.preferences = {
            "language": "pt-BR",
            "date_format": "DD/MM/YYYY",
            "timezone": "America/Sao_Paulo",
            "currency_display": "BRL",
        }
        await session.commit()

        uid = user.id
        print(f"  user {uid}")

        # 1b. Personal workspace ---------------------------------------------
        # Every financial entity (Account, Category, Transaction, ...) has a
        # NOT NULL workspace_id, and this script now passes it explicitly on
        # every insert. It used to rely on the autostamp listener for ORM
        # `session.add()` rows while passing `wsid` only to the bulk
        # pg_insert() calls, and the two resolve the workspace differently:
        # the listener takes the user's OLDEST non-archived workspace, while
        # create_personal_workspace_for_user() has no ORDER BY at all. On a
        # single-workspace database they agree, so this looked fine. On the
        # public demo, where visitors create workspaces that are never
        # cleaned up, they diverged and accounts landed in one workspace
        # while transactions went to another, leaving the dashboard empty.
        #
        # Pick the same workspace the listener would, so the two can never
        # disagree again even if an insert is added without workspace_id.
        wsid = await _resolve_demo_workspace(session, user)
        await session.commit()
        print(f"  workspace {wsid}")

        # Visitors on the public demo can create workspaces, and nothing ever
        # removed them: they pile up in the switcher and, worse, the oldest of
        # them can win the autostamp lookup. Archive everything except the one
        # we seed so each reset returns the demo to a single clean workspace.
        stray = (await session.execute(
            select(Workspace.id)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == uid, Workspace.id != wsid,
                   Workspace.is_archived.is_(False))
        )).scalars().all()
        if stray:
            await session.execute(
                Workspace.__table__.update()
                .where(Workspace.id.in_(stray))
                .values(is_archived=True)
            )
            await session.commit()
            print(f"  {len(stray)} stray workspaces archived")

        # 2. Accounts --------------------------------------------------------
        accounts: list[Account] = []
        openings: list[tuple[Account, Decimal]] = []
        for tmpl in ACCOUNTS:
            acc = Account(
                user_id=uid,
                workspace_id=wsid,
                name=tmpl["name"],
                type=tmpl["type"],
                currency=tmpl["currency"],
                balance=Decimal("0.00"),
                credit_limit=tmpl.get("credit_limit"),
            )
            session.add(acc)
            accounts.append(acc)
            openings.append((acc, tmpl["opening"]))
        await session.flush()
        print(f"  {len(accounts)} accounts")

        # Aliases for readability below. Conta Nubank is the primary BRL
        # checking; Cartão Nubank carries credit-card-style spending
        # (subscriptions, dining, shopping); Carteira USD handles travel.
        brl_checking = accounts[0]
        brl_savings  = accounts[1]  # noqa: F841 (kept for clarity / future use)
        brl_credit   = accounts[2]
        usd_checking = accounts[3]  # noqa: F841 (kept for clarity / future use)

        # 3. Categories ------------------------------------------------------
        cat_by_name: dict[str, Category] = {}
        for name, color, icon, _kind in CATEGORIES:
            c = Category(user_id=uid, workspace_id=wsid, name=name, color=color, icon=icon)
            session.add(c)
            cat_by_name[name] = c
        await session.flush()
        print(f"  {len(cat_by_name)} categories")

        # 4. FX rates --------------------------------------------------------
        dates = _date_range(start, today)
        eur_rate = Decimal("0.9200")
        brl_rate = Decimal("5.0000")
        fx_rows = []
        for d in dates:
            eur_rate = max(Decimal("0.85"), min(Decimal("1.00"),
                eur_rate + Decimal(str(round(rng.gauss(0, 0.002), 6)))))
            brl_rate = max(Decimal("4.50"), min(Decimal("6.00"),
                brl_rate + Decimal(str(round(rng.gauss(0, 0.02), 6)))))
            for quote, rate in (("EUR", eur_rate), ("BRL", brl_rate)):
                fx_rows.append({
                    "base_currency": "USD", "quote_currency": quote, "date": d,
                    "rate": rate.quantize(Decimal("0.000001")), "source": "seed",
                })
        for i in range(0, len(fx_rows), 2000):
            stmt = pg_insert(FxRate).values(fx_rows[i : i + 2000])
            stmt = stmt.on_conflict_do_update(
                constraint="uq_fx_rate_base_quote_date",
                set_={"rate": stmt.excluded.rate, "source": stmt.excluded.source},
            )
            await session.execute(stmt)
        await session.commit()
        print(f"  {len(fx_rows)} FX rate rows")

        # 5. Transactions ----------------------------------------------------
        tx_rows: list[dict] = []

        # Opening-balance deposits so each account's calculated balance
        # reflects the intended starting amount. Uses the "Reembolsos"
        # credit category — slightly off-label but keeps the seed
        # self-contained.
        opening_cat = cat_by_name["Reembolsos"]
        for acc, amt in openings:
            if amt > 0:
                tx_rows.append({
                    "id": uuid.uuid4(),
                    "user_id": uid,
                    "workspace_id": wsid,
                    "account_id": acc.id,
                    "category_id": opening_cat.id,
                    "description": "Saldo Inicial",
                    "payee": "Saldo Inicial",
                    "amount": amt,
                    "currency": acc.currency,
                    "date": start,
                    "effective_date": start,
                    "type": "credit",
                    "source": "manual",
                    "status": "posted",
                })

        def _add(account: Account, day: date, payee: str, cat_name: str,
                 amount: Decimal, kind: str) -> None:
            tx_rows.append({
                "id": uuid.uuid4(),
                "user_id": uid,
                "workspace_id": wsid,
                "account_id": account.id,
                "category_id": cat_by_name[cat_name].id,
                "description": payee,
                "payee": payee,
                "amount": amount,
                "currency": account.currency,
                "date": day,
                "effective_date": day,
                "type": kind,
                "source": "manual",
                "status": "posted",
            })

        # Salário no dia 5 de cada mês (típico no Brasil).
        salary_day = start.replace(day=5)
        while salary_day <= today:
            _add(brl_checking, salary_day, "Empresa Folha Pagamento", "Salário",
                 Decimal("3300.00"), "credit")
            year = salary_day.year + (1 if salary_day.month == 12 else 0)
            month = 1 if salary_day.month == 12 else salary_day.month + 1
            try:
                salary_day = salary_day.replace(year=year, month=month)
            except ValueError:
                break

        # Aluguel no dia 10.
        rent_day = start.replace(day=10)
        while rent_day <= today:
            _add(brl_checking, rent_day, "Aluguel", "Aluguel",
                 Decimal("950.00"), "debit")
            year = rent_day.year + (1 if rent_day.month == 12 else 0)
            month = 1 if rent_day.month == 12 else rent_day.month + 1
            try:
                rent_day = rent_day.replace(year=year, month=month)
            except ValueError:
                break

        # Assinaturas recorrentes em dias fixos.
        sub_day = start.replace(day=15)
        while sub_day <= today:
            for payee, amt, cat in [
                ("Netflix",      Decimal("39.90"), "Assinaturas"),
                ("Spotify",      Decimal("21.90"), "Assinaturas"),
                ("Disney+",      Decimal("33.90"), "Assinaturas"),
                ("Amazon Prime", Decimal("14.90"), "Assinaturas"),
            ]:
                _add(brl_credit, sub_day, payee, cat, amt, "debit")
            year = sub_day.year + (1 if sub_day.month == 12 else 0)
            month = 1 if sub_day.month == 12 else sub_day.month + 1
            try:
                sub_day = sub_day.replace(year=year, month=month)
            except ValueError:
                break

        # ~50 transações variáveis por mês.
        n_random = 50 * months
        cc_categories = {"Compras", "Restaurantes", "Lazer", "Assinaturas"}
        for _ in range(n_random):
            day = start + timedelta(days=rng.randint(0, max(1, (today - start).days)))
            # 90% débito, 10% crédito (reembolsos / freelance / dividendos)
            if rng.random() < 0.9:
                cat_name = rng.choice(list(DEBIT_MERCHANTS.keys()))
                payee, lo, hi = rng.choice(DEBIT_MERCHANTS[cat_name])
                acc = brl_credit if cat_name in cc_categories else brl_checking
                amount = Decimal(str(round(rng.uniform(lo, hi), 2)))
                _add(acc, day, payee, cat_name, amount, "debit")
            else:
                cat_name = rng.choice(list(CREDIT_MERCHANTS.keys()))
                payee, lo, hi = rng.choice(CREDIT_MERCHANTS[cat_name])
                amount = Decimal(str(round(rng.uniform(lo, hi), 2)))
                _add(brl_checking, day, payee, cat_name, amount, "credit")

        for i in range(0, len(tx_rows), 2000):
            await session.execute(pg_insert(Transaction).values(tx_rows[i : i + 2000]))
        await session.commit()
        print(f"  {len(tx_rows)} transactions")

        # 6. Assets ----------------------------------------------------------
        n_days = len(dates) - 1

        # Market-priced (real tickers). Demo mode skips the refresh task so
        # last_price stays at the seeded value — that's intentional.
        for tmpl in TICKER_ASSETS:
            base_total = (tmpl["last_price"] * tmpl["units"]) / (
                Decimal("1") + tmpl["daily_pct"] * n_days
            )
            asset = Asset(
                user_id=uid,
                workspace_id=wsid,
                name=tmpl["name"],
                type="investment",
                currency=tmpl["currency"],
                units=tmpl["units"],
                valuation_method="market_price",
                ticker=tmpl["ticker"],
                last_price=tmpl["last_price"],
                last_price_at=datetime.now(timezone.utc),
                purchase_date=dates[0],
                purchase_price=base_total.quantize(Decimal("0.01")),
                logo_url=tmpl.get("logo"),
            )
            session.add(asset)
            await session.flush()

            value_rows = []
            v = base_total
            for d in dates:
                noise = Decimal(str(round(rng.gauss(0, float(v) * 0.006), 2)))
                amount = max(Decimal("1.00"), (v + noise).quantize(Decimal("0.01")))
                value_rows.append({
                    "id": uuid.uuid4(),
                    "asset_id": asset.id,
                    "workspace_id": wsid,
                    "amount": amount,
                    "date": d,
                    "source": "seed",
                })
                v = v * (Decimal("1") + tmpl["daily_pct"])
            for i in range(0, len(value_rows), 2000):
                await session.execute(pg_insert(AssetValue).values(value_rows[i : i + 2000]))

        # Manual assets (real estate, etc.)
        for tmpl in MANUAL_ASSETS:
            asset = Asset(
                user_id=uid,
                workspace_id=wsid,
                name=tmpl["name"],
                type=tmpl["type"],
                currency=tmpl["currency"],
                valuation_method="manual",
                purchase_date=dates[0],
                purchase_price=tmpl["purchase"],
            )
            session.add(asset)
            await session.flush()

            value_rows = []
            v = tmpl["purchase"]
            target = tmpl["current"]
            # Linear-ish drift from purchase to current
            step = (target - v) / max(1, n_days)
            for d in dates:
                value_rows.append({
                    "id": uuid.uuid4(),
                    "asset_id": asset.id,
                    "workspace_id": wsid,
                    "amount": v.quantize(Decimal("0.01")),
                    "date": d,
                    "source": "seed",
                })
                v = v + step
            for i in range(0, len(value_rows), 2000):
                await session.execute(pg_insert(AssetValue).values(value_rows[i : i + 2000]))

        await session.commit()
        print(f"  {len(TICKER_ASSETS) + len(MANUAL_ASSETS)} assets")

        # 7. Recurring -------------------------------------------------------
        cc_recurring = {"Assinaturas", "Lazer"}
        for desc, amt, kind, cat_name in RECURRING:
            acc = brl_credit if cat_name in cc_recurring else brl_checking
            session.add(RecurringTransaction(
                user_id=uid,
                workspace_id=wsid,
                account_id=acc.id,
                category_id=cat_by_name[cat_name].id,
                description=desc,
                amount=amt,
                currency=acc.currency,
                type=kind,
                frequency="monthly",
                day_of_month=15 if kind == "debit" and cat_name != "Aluguel" else 10,
                start_date=start,
                next_occurrence=today + timedelta(days=rng.randint(1, 25)),
                is_active=True,
            ))
        await session.commit()
        print(f"  {len(RECURRING)} recurring transactions")

        # 8. Goals -----------------------------------------------------------
        session.add(Goal(
            user_id=uid,
            workspace_id=wsid,
            name="Reserva de Emergência",
            target_amount=Decimal("8000.00"),
            current_amount=Decimal("5000.00"),
            initial_amount=Decimal("0.00"),
            currency="BRL",
            target_date=today + timedelta(days=210),
            tracking_type="manual",
            status="active",
            icon="shield",
            color="#16a34a",
        ))
        session.add(Goal(
            user_id=uid,
            workspace_id=wsid,
            name="Viagem ao Japão",
            target_amount=Decimal("6000.00"),
            current_amount=Decimal("2500.00"),
            initial_amount=Decimal("0.00"),
            currency="BRL",
            target_date=today + timedelta(days=300),
            tracking_type="manual",
            status="active",
            icon="plane",
            color="#0ea5e9",
        ))
        await session.commit()
        print("  2 goals")

        # 9. Split group ----------------------------------------------------
        # "Casa" — a household with two flatmates. Demonstrates the
        # transaction-splitting feature: shared bills are split 3-way and
        # each housemate owes their portion.
        casa = Group(
            user_id=uid,
            workspace_id=wsid,
            name="Casa",
            kind="social",
            default_currency="BRL",
            icon="home",
            color="#0ea5e9",
            notes="Despesas compartilhadas do apartamento",
        )
        session.add(casa)
        await session.flush()
        members = [
            GroupMember(group_id=casa.id, name="Eu",    is_self=True),
            GroupMember(group_id=casa.id, name="Ana",   is_self=False),
            GroupMember(group_id=casa.id, name="Bruno", is_self=False),
        ]
        for m in members:
            session.add(m)
        await session.flush()

        # Split three recent shared expenses 3-way (equal). We pick the
        # most recent Carrefour, Enel and Net Claro transactions on the
        # primary checking account.
        shared_targets = ["Carrefour", "Enel", "Net Claro"]
        shared_tx_ids: list[uuid.UUID] = []
        for payee in shared_targets:
            row = await session.execute(
                select(Transaction)
                .where(Transaction.user_id == uid, Transaction.payee == payee)
                .order_by(Transaction.date.desc())
                .limit(1)
            )
            tx = row.scalar_one_or_none()
            if not tx:
                continue
            shared_tx_ids.append(tx.id)
            # Equal 3-way split (rounded to cents; remainder lands on "Eu")
            share = (tx.amount / Decimal("3")).quantize(Decimal("0.01"))
            remainder = tx.amount - (share * Decimal("3"))
            for i, m in enumerate(members):
                amt = share + (remainder if i == 0 else Decimal("0"))
                session.add(TransactionSplit(
                    transaction_id=tx.id,
                    group_member_id=m.id,
                    share_amount=amt,
                    share_type="equal",
                ))
        await session.commit()
        print(f"  1 group ({len(members)} members, {len(shared_tx_ids)} shared transactions)")

        # 10. Rules ----------------------------------------------------------
        # A handful of common Brazilian auto-categorisation rules so
        # visitors can see how matching + actions work.
        rules_data = [
            {
                "name": "iFood → Restaurantes",
                "conditions": [{"field": "description", "op": "starts_with", "value": "IFOOD"}],
                "category": "Restaurantes",
                "priority": 10,
            },
            {
                "name": "Uber / 99 → Transporte",
                "conditions_op": "or",
                "conditions": [
                    {"field": "description", "op": "starts_with", "value": "UBER"},
                    {"field": "description", "op": "starts_with", "value": "99 "},
                ],
                "category": "Transporte",
                "priority": 10,
            },
            {
                "name": "Streaming (Netflix, Spotify, Disney+) → Assinaturas",
                "conditions_op": "or",
                "conditions": [
                    {"field": "description", "op": "contains", "value": "NETFLIX"},
                    {"field": "description", "op": "contains", "value": "SPOTIFY"},
                    {"field": "description", "op": "contains", "value": "DISNEY"},
                ],
                "category": "Assinaturas",
                "priority": 10,
            },
            {
                "name": "Folha de pagamento → Salário",
                "conditions": [{"field": "description", "op": "contains", "value": "FOLHA PAGAMENTO"}],
                "category": "Salário",
                "priority": 20,
            },
        ]
        for r in rules_data:
            cat = cat_by_name.get(r["category"])
            if cat is None:
                continue
            session.add(Rule(
                user_id=uid,
                workspace_id=wsid,
                name=r["name"],
                conditions_op=r.get("conditions_op", "and"),
                conditions=r["conditions"],
                actions=[{"op": "set_category", "value": str(cat.id)}],
                priority=r["priority"],
                is_active=True,
            ))
        await session.commit()
        print(f"  {len(rules_data)} rules")

        # 11. Budgets --------------------------------------------------------
        # Monthly budgets for the current month + 2 months back so the
        # dashboard's category-progress bars have something to render.
        budget_targets = [
            ("Aluguel",      Decimal("1000.00")),
            ("Alimentação",  Decimal("850.00")),
            ("Transporte",   Decimal("500.00")),
            ("Restaurantes", Decimal("500.00")),
            ("Compras",      Decimal("500.00")),
            ("Lazer",        Decimal("170.00")),
            ("Contas",       Decimal("400.00")),
            ("Assinaturas",  Decimal("200.00")),
            ("Saúde",        Decimal("270.00")),
            ("Viagem",       Decimal("700.00")),
        ]
        budget_months: list[date] = []
        cursor = today.replace(day=1)
        for _ in range(3):
            budget_months.append(cursor)
            # one calendar month back
            year = cursor.year - (1 if cursor.month == 1 else 0)
            month = 12 if cursor.month == 1 else cursor.month - 1
            cursor = cursor.replace(year=year, month=month)
        for m in budget_months:
            for cat_name, amount in budget_targets:
                cat = cat_by_name.get(cat_name)
                if cat is None:
                    continue
                session.add(Budget(
                    user_id=uid,
                    workspace_id=wsid,
                    category_id=cat.id,
                    amount=amount,
                    month=m,
                    currency="BRL",
                ))
        await session.commit()
        print(f"  {len(budget_targets) * len(budget_months)} budgets ({len(budget_targets)} cats × {len(budget_months)} months)")

    print("\nDemo seed complete.")
    print(f"  Login : {email}")
    print(f"  Passwd: {password}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed showcase data for the public demo")
    parser.add_argument("--email",    default="demo@securo.app")
    parser.add_argument("--password", default="DemoSecuro1!")
    parser.add_argument("--months",   type=int, default=6,
                        help="How many months of history to seed (default: 6)")
    args = parser.parse_args()
    asyncio.run(seed(args.email, args.password, args.months))


if __name__ == "__main__":
    main()
