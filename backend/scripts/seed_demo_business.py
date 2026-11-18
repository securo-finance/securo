#!/usr/bin/env python3
"""Seed the business side of the public demo.

The demo account owns two workspaces: "Aurora Studio" (kind `business`)
and "Pessoal" (kind `personal`). `seed_demo.py` fills the personal one
and calls this module at the end of the same run, so one reset rewrites
both wallets.

The business workspace is created first, with an earlier `created_at`,
on purpose: a visitor with no workspace selected lands on the first one
the API lists, and the B2B story is the one the landing has to show.

What this module builds, all inside the business workspace:

* the studio's identity: legal name, address, CNPJ and the issuer block
  an invoice document prints;
* a catalog of services with their prices;
* clients and suppliers as payees, the clients carrying valid CNPJs;
* six months of cash movement on the checking account and the card;
* the invoice ledger, with every derived state the list can show:
  paid, partial, overdue, open and draft, plus one installment plan;
* two recurring agreements: an active monthly retainer whose periods
  were invoiced, and one that ended with a reason;
* budgets, a goal, recurring bills and rules, so the business workspace
  does not look empty next to the personal one.

It also deletes what it wrote before writing again, so running it twice
keeps the same numbers and does not duplicate a single row.

Inside the backend container, as the last step of the reset:

    python scripts/seed_demo.py

On its own, against an existing demo user:

    python scripts/seed_demo_business.py
"""
from __future__ import annotations

import argparse
import asyncio
import calendar
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.exceptions import UserAlreadyExists
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.auth import UserManager
from app.core.database import async_session_maker
from app.models.account import Account
from app.models.budget import Budget
from app.models.category import Category
from app.models.credit_card_bill import CreditCardBill
from app.models.goal import Goal
from app.models.invoice import (
    Invoice,
    InvoiceAllocation,
    InvoiceDeduction,
    InvoiceInstallment,
    InvoiceLine,
    InvoiceSettings,
)
from app.models.invoice_attachment import InvoiceAttachment
from app.models.invoice_schedule import InvoiceSchedule, InvoiceScheduleTerm
from app.models.payee import Payee, PayeeTaxId
from app.models.product import Product, ProductPrice
from app.models.recurring_transaction import RecurringTransaction
from app.models.recurring_transaction_category_line import RecurringTransactionCategoryLine
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.transaction_attachment import TransactionAttachment
from app.models.transaction_category_line import TransactionCategoryLine
from app.models.transaction_split import TransactionSplit
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceTaxId
from app.schemas.user import UserCreate
from app.services import (
    credit_card_service,
    invoice_schedule_service,
    invoice_service,
    product_service,
)

BRL = "BRL"
CENT = Decimal("0.01")
DEMO_TIMEZONE = "America/Sao_Paulo"

#: A design studio that sells projects and a monthly retainer. The name is
#: fictional, and so is every supplier below: the demo is published on the
#: project's own site, and a screenshot should not put somebody else's
#: trademark on the marketing page.
COMPANY = {
    "name": "Aurora Studio",
    "legal_name": "Aurora Studio Ltda",
    "address": "Rua Harmonia, 412, Vila Madalena, São Paulo, SP",
    "tax_jurisdiction": "BR",
    # Valid under the mod-11 rule the fiscal pack applies, so the issuer
    # block and the invoice form accept it.
    "cnpj": "11222333000181",
}

INVOICE_SETTINGS = {
    # `tracking` issues on creation, which is what makes the ledger show
    # receivables instead of a pile of drafts. The document still renders
    # from the snapshot, so the print view has a real issuer block.
    "preset": "tracking",
    "default_payment_terms_days": 15,
    "number_prefix": "AUR-",
    "series": "A",
    "issuer_display_name": COMPANY["name"],
    "footer_note": (
        "Aurora Studio Ltda, CNPJ 11.222.333/0001-81. "
        "Obrigado pela parceria."
    ),
    "payment_details": (
        "Pix CNPJ 11.222.333/0001-81, banco digital, "
        "agência 0001, conta 1234567-8"
    ),
    "accent_color": "#0f766e",
}

ACCOUNTS: tuple[dict[str, Any], ...] = (
    {
        "key": "checking",
        "name": "Conta PJ",
        "type": "checking",
        "opening": Decimal("18500.00"),
    },
    {
        "key": "savings",
        "name": "Reserva do negócio",
        "type": "savings",
        "opening": Decimal("24000.00"),
    },
    {
        "key": "card",
        "name": "Cartão PJ",
        "type": "credit_card",
        "opening": Decimal("0.00"),
        "credit_limit": Decimal("8000.00"),
        "statement_close_day": 5,
        "payment_due_day": 12,
    },
)

#: Icons and colours exist in the frontend map; a name it does not know
#: renders as a blank square in the category picker.
CATEGORIES = (
    ("Receita de Serviços", "#0f766e", "briefcase"),
    ("Receita Recorrente", "#0ea5e9", "trending-up"),
    ("Pró-labore", "#7c3aed", "wallet"),
    ("Impostos", "#b91c1c", "landmark"),
    ("Contas e Escritório", "#64748b", "building-2"),
    ("Software e Assinaturas", "#2563eb", "laptop"),
    ("Marketing", "#db2777", "sparkles"),
    ("Serviços de Terceiros", "#0d9488", "briefcase"),
    ("Equipamentos", "#475569", "monitor"),
    ("Alimentação", "#f59e0b", "utensils-crossed"),
    ("Transporte", "#65a30d", "car"),
    ("Tarifas", "#6b7280", "receipt"),
)

CLIENTS: tuple[dict[str, Any], ...] = (
    {
        "key": "horizonte",
        "name": "Construtora Horizonte Ltda",
        "cnpj": "45723174000110",
        "email": "financeiro@horizonte.example",
        "phone": "(11) 3555-1020",
        "favorite": True,
    },
    {
        "key": "centro",
        "name": "Café do Centro Ltda",
        "cnpj": "08187168000160",
        "email": "contato@cafedocentro.example",
        "phone": "(11) 3555-2040",
    },
    {
        "key": "bela_vista",
        "name": "Padaria Bela Vista Ltda",
        "cnpj": "31415926000171",
        "email": "adm@belavista.example",
        "phone": "(11) 3555-3080",
        "favorite": True,
    },
    {
        "key": "clinica",
        "name": "Clínica Bem Viver Ltda",
        "cnpj": "27865757000102",
        "email": "financeiro@bemviver.example",
        "phone": "(11) 3555-4010",
    },
)

SUPPLIERS: tuple[dict[str, Any], ...] = (
    {"key": "cloud", "name": "Nuvem Brasil", "email": "faturas@nuvembrasil.example"},
    {"key": "design", "name": "Licença Criativa"},
    {"key": "ads", "name": "Criativo Ads"},
    {"key": "accounting", "name": "Contabilidade Almeida"},
    {"key": "coworking", "name": "Coworking Pinheiros"},
    {"key": "telecom", "name": "Fibra PJ Telecom"},
    {"key": "store", "name": "Tech Store Brasil"},
    {"key": "lunch", "name": "Restaurante Vila"},
    {"key": "rides", "name": "Corrida App"},
    {"key": "stationery", "name": "Papelaria Central"},
)

#: `fiscal_refs` follow the shape the Brazilian pack suggests for a
#: service line; they travel to the invoice line as a snapshot.
PRODUCTS: tuple[dict[str, Any], ...] = (
    {
        "key": "identity",
        "name": "Identidade visual",
        "kind": "service",
        "unit": "projeto",
        "price": Decimal("4500.00"),
        "description": "Marca, aplicações e manual de uso.",
        "fiscal_refs": {"service_code": "10.02", "municipal_service_code": "1301"},
    },
    {
        "key": "website",
        "name": "Site institucional",
        "kind": "service",
        "unit": "projeto",
        "price": Decimal("8900.00"),
        "description": "Desenho, implementação e publicação.",
        "fiscal_refs": {"service_code": "10.02", "municipal_service_code": "1301"},
    },
    {
        "key": "consulting",
        "name": "Consultoria de marca",
        "kind": "service",
        "unit": "projeto",
        "price": Decimal("6200.00"),
        "description": "Diagnóstico e plano de posicionamento.",
        "fiscal_refs": {"service_code": "17.01", "municipal_service_code": "1701"},
    },
    {
        "key": "social",
        "name": "Gestão de redes sociais",
        "kind": "service",
        "unit": "mês",
        "price": Decimal("2200.00"),
        "billing": "recurring",
        "interval": "monthly",
        "description": "Conteúdo, calendário editorial e relatório.",
        "fiscal_refs": {"service_code": "17.06", "municipal_service_code": "1706"},
    },
    {
        "key": "content",
        "name": "Pacote de conteúdo",
        "kind": "service",
        "unit": "mês",
        "price": Decimal("1800.00"),
        "billing": "recurring",
        "interval": "monthly",
        "description": "Fotos, textos e publicações do mês.",
        "fiscal_refs": {"service_code": "17.06", "municipal_service_code": "1706"},
    },
)

#: One-off projects, oldest first, every one of them settled. The invoice
#: that is only halfway paid is built next to the ledger instead, because
#: "halfway paid and not yet late" has to be measured from today.
PROJECTS: tuple[dict[str, Any], ...] = (
    {
        "months_ago": 5,
        "day": 8,
        "client": "centro",
        "product": "identity",
        "description": "Identidade visual, Café do Centro",
        "amount": Decimal("4500.00"),
    },
    {
        "months_ago": 4,
        "day": 3,
        "client": "horizonte",
        "product": "website",
        "description": "Site institucional, Construtora Horizonte",
        "amount": Decimal("8900.00"),
    },
    {
        "months_ago": 3,
        "day": 12,
        "client": "bela_vista",
        "product": "identity",
        "description": "Identidade visual, Padaria Bela Vista",
        "amount": Decimal("3800.00"),
    },
    {
        "months_ago": 1,
        "day": 6,
        "client": "centro",
        "product": "website",
        "description": "Site institucional, Café do Centro",
        "amount": Decimal("7400.00"),
    },
    {
        "months_ago": 0,
        "day": 4,
        "client": "horizonte",
        "product": "identity",
        "description": "Identidade visual, Construtora Horizonte",
        "amount": Decimal("5200.00"),
    },
)

#: Fixed monthly costs on the checking account: (day, payee, category, amount).
MONTHLY_COSTS = (
    (5, None, "Pró-labore", Decimal("5500.00")),
    (3, "coworking", "Contas e Escritório", Decimal("1200.00")),
    (10, "accounting", "Serviços de Terceiros", Decimal("480.00")),
    (12, "telecom", "Contas e Escritório", Decimal("149.90")),
    (20, None, "Impostos", Decimal("720.00")),
    (28, None, "Tarifas", Decimal("49.90")),
)

#: Card purchases, seeded for the last two cycles only, as if the older
#: statements had already been paid off. `None` on the first row of the
#: older cycle keeps the description readable in the list.
CARD_PURCHASES = (
    ("cloud", "Nuvem Brasil, hospedagem e banco de dados", "Software e Assinaturas",
     1, Decimal("389.00")),
    ("design", "Licença Criativa, plano anual", "Software e Assinaturas",
     8, Decimal("189.90")),
    ("ads", "Criativo Ads, campanha do mês", "Marketing",
     15, Decimal("1350.00")),
    ("lunch", "Restaurante Vila", "Alimentação", 17, Decimal("86.00")),
    ("rides", "Corrida App", "Transporte", 19, Decimal("34.50")),
    ("stationery", "Papelaria Central", "Contas e Escritório", 22, Decimal("58.90")),
)

ONE_OFFS: tuple[dict[str, Any], ...] = (
    {
        "months_ago": 3,
        "day": 14,
        "payee": "store",
        "description": "Notebook para o estúdio",
        "category": "Equipamentos",
        "amount": Decimal("6499.00"),
    },
    {
        "months_ago": 1,
        "day": 11,
        "payee": "store",
        "description": "Monitor 27 polegadas",
        "category": "Equipamentos",
        "amount": Decimal("1899.00"),
    },
)

BUDGETS = (
    ("Contas e Escritório", Decimal("1400.00")),
    ("Software e Assinaturas", Decimal("650.00")),
    ("Marketing", Decimal("1500.00")),
    ("Serviços de Terceiros", Decimal("500.00")),
    ("Alimentação", Decimal("300.00")),
    ("Transporte", Decimal("200.00")),
    ("Equipamentos", Decimal("2500.00")),
    ("Impostos", Decimal("800.00")),
)

RULES: tuple[dict[str, Any], ...] = (
    {
        "name": "Coworking → Contas e Escritório",
        "conditions": [{"field": "description", "op": "contains", "value": "COWORKING"}],
        "category": "Contas e Escritório",
        "priority": 10,
    },
    {
        "name": "Nuvem Brasil → Software e Assinaturas",
        "conditions": [{"field": "description", "op": "contains", "value": "NUVEM"}],
        "category": "Software e Assinaturas",
        "priority": 10,
    },
    {
        "name": "Corrida App → Transporte",
        "conditions": [{"field": "description", "op": "contains", "value": "CORRIDA"}],
        "category": "Transporte",
        "priority": 10,
    },
)

RECURRING_BILLS = (
    ("coworking", "Contas e Escritório", Decimal("1200.00"), 3),
    ("accounting", "Serviços de Terceiros", Decimal("480.00"), 10),
    ("telecom", "Contas e Escritório", Decimal("149.90"), 12),
    ("cloud", "Software e Assinaturas", Decimal("389.00"), 1),
)


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------
def _month_start(today: date, months_ago: int) -> date:
    """The first day of the month `months_ago` months before `today`."""
    index = today.year * 12 + (today.month - 1) - months_ago
    year, month = divmod(index, 12)
    return date(year, month + 1, 1)


def _month_day(today: date, months_ago: int, day: int) -> date:
    """A day of a past or current month, clamped to the month's length."""
    start = _month_start(today, months_ago)
    last = calendar.monthrange(start.year, start.month)[1]
    return start.replace(day=min(day, last))


def _next_occurrence(today: date, day: int) -> date:
    """The next time this day of the month happens, today included."""
    this_month = _month_day(today, 0, day)
    if this_month >= today:
        return this_month
    return _month_day(today, -1, day)


# ---------------------------------------------------------------------------
# Wiping what a previous run wrote
# ---------------------------------------------------------------------------
async def _wipe_business(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Delete this workspace's demo rows, children before parents.

    The public reset wipes by user across every workspace, so a fresh
    database finds nothing here. This exists so the script can run twice
    in a row (locally, or by hand on the demo host) without tripping a
    foreign key or leaving two copies of every invoice. Children go
    first because the SQLite test harness does not enforce the cascades
    Postgres does.
    """
    for model in (
        InvoiceAllocation,
        InvoiceInstallment,
        InvoiceDeduction,
        InvoiceAttachment,
        InvoiceLine,
        Invoice,
        InvoiceScheduleTerm,
        InvoiceSchedule,
        ProductPrice,
        Product,
        TransactionAttachment,
        TransactionSplit,
        TransactionCategoryLine,
        RecurringTransactionCategoryLine,
        Transaction,
        CreditCardBill,
        RecurringTransaction,
        Rule,
        Budget,
        Goal,
        Category,
        Account,
    ):
        await session.execute(delete(model).where(model.workspace_id == workspace_id))

    await session.execute(
        delete(PayeeTaxId).where(
            PayeeTaxId.payee_id.in_(
                select(Payee.id).where(Payee.workspace_id == workspace_id)
            )
        )
    )
    await session.execute(delete(Payee).where(Payee.workspace_id == workspace_id))
    await session.execute(
        delete(WorkspaceTaxId).where(WorkspaceTaxId.workspace_id == workspace_id)
    )
    await session.execute(
        delete(InvoiceSettings).where(InvoiceSettings.workspace_id == workspace_id)
    )
    await session.flush()


# ---------------------------------------------------------------------------
# The workspace
# ---------------------------------------------------------------------------
def _stamp_workspace(workspace: Workspace) -> None:
    """Force the identity the demo screenshots were reviewed against."""
    workspace.name = COMPANY["name"]
    workspace.legal_name = COMPANY["legal_name"]
    workspace.address = COMPANY["address"]
    workspace.tax_jurisdiction = COMPANY["tax_jurisdiction"]
    workspace.default_currency = BRL
    workspace.locale = "pt-BR"
    workspace.timezone = DEMO_TIMEZONE
    workspace.icon = "briefcase"
    workspace.color = "#0f766e"


async def ensure_business_workspace(
    session: AsyncSession,
    user: User,
    *,
    before: Optional[datetime] = None,
) -> Workspace:
    """The business workspace of the demo, creating it if it is missing.

    `before` is the personal workspace's `created_at`: the business one
    is moved ten minutes earlier than it, because the frontend opens the
    first workspace the API lists when a visitor has no saved selection,
    and the B2B landing has to be that one. The value is deterministic,
    so every reset puts it back in the same place.

    A visitor may have created business workspaces of their own on the
    public demo. They are archived here: two business wallets in the
    switcher tell a story nobody reviewed.
    """
    rows = (
        (
            await session.execute(
                select(Workspace)
                .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                .where(
                    WorkspaceMember.user_id == user.id,
                    Workspace.kind == "business",
                )
                .order_by(Workspace.created_at.asc())
            )
        )
        .unique()
        .scalars()
        .all()
    )
    live = [row for row in rows if not row.is_archived]
    workspace = live[0] if live else (rows[0] if rows else None)

    if workspace is None:
        workspace = Workspace(
            name=COMPANY["name"],
            kind="business",
            created_by_user_id=user.id,
            default_currency=BRL,
            locale="pt-BR",
            timezone=DEMO_TIMEZONE,
        )
        session.add(workspace)
        await session.flush()

    membership = await session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if membership is None:
        session.add(
            WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner")
        )
    elif membership.role != "owner":
        membership.role = "owner"

    workspace.is_archived = False
    _stamp_workspace(workspace)
    if before is not None:
        workspace.created_at = before - timedelta(minutes=10)

    for stray in rows:
        if stray.id != workspace.id and not stray.is_archived:
            stray.is_archived = True

    await session.flush()
    await session.commit()
    return workspace


async def archive_other_workspaces(
    session: AsyncSession, user: User, keep: list[uuid.UUID]
) -> int:
    """Archive every other live workspace of the demo account.

    Visitors create workspaces on the public demo and nothing removes
    them; they pile up in the switcher. Only the two the demo seeds are
    kept, and both are passed in `keep`, so one run never archives the
    other wallet.
    """
    rows = (
        (
            await session.execute(
                select(Workspace)
                .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                .where(
                    WorkspaceMember.user_id == user.id,
                    Workspace.is_archived.is_(False),
                    Workspace.id.not_in(keep),
                )
            )
        )
        .unique()
        .scalars()
        .all()
    )
    for row in rows:
        row.is_archived = True
    await session.flush()
    await session.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Rows the seeder writes
# ---------------------------------------------------------------------------
def _add_transaction(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    account: Account,
    day: date,
    description: str,
    amount: Decimal,
    kind: str,
    category: Optional[Category] = None,
    payee: Optional[Payee] = None,
    exclude_from_pnl: bool = False,
) -> Transaction:
    """One posted row, with the effective date the account's cycle gives it."""
    row = Transaction(
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=account.id,
        category_id=category.id if category is not None else None,
        description=description,
        payee=payee.name if payee is not None else description,
        payee_id=payee.id if payee is not None else None,
        amount=amount,
        currency=account.currency,
        date=day,
        type=kind,
        source="manual",
        status="posted",
        exclude_from_pnl=exclude_from_pnl,
    )
    # Card rows land in the bill that will actually charge them; for
    # every other account the effective date is the date itself.
    credit_card_service.apply_effective_date(row, account)
    session.add(row)
    return row


def _invoice_line(
    product: Product, price: ProductPrice, description: str, amount: Decimal
) -> dict[str, Any]:
    return {
        "description": description,
        "quantity": Decimal("1"),
        "unit": product.unit,
        "unit_price": amount,
        "product_id": product.id,
        "price_id": price.id,
    }


def _term_line(product: Product, price: ProductPrice, description: str) -> dict[str, Any]:
    return {
        "description": description,
        "quantity": Decimal("1"),
        "unit": product.unit,
        "unit_price": price.unit_price,
        "product_id": product.id,
        "price_id": price.id,
    }


async def _receive(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    account: Account,
    category: Category,
    payee: Payee,
    invoice: Invoice,
    amount: Decimal,
    day: date,
) -> Transaction:
    """An incoming transfer that settles part or all of an invoice."""
    number = f" {invoice.number}" if invoice.number else ""
    transaction = _add_transaction(
        session,
        user_id=user_id,
        workspace_id=workspace_id,
        account=account,
        day=day,
        description=f"Recebimento da nota{number}, {payee.name}",
        amount=amount,
        kind="credit",
        category=category,
        payee=payee,
    )
    await session.flush()
    await invoice_service.allocate(session, invoice, transaction.id, amount=amount)
    return transaction


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------
async def seed_business(
    session: AsyncSession,
    user: User,
    workspace: Workspace,
    *,
    months: int = 6,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Rewrite the business workspace and return what it now holds."""
    today = today or app_today()
    wsid = workspace.id
    uid = user.id
    months = max(months, 3)

    _stamp_workspace(workspace)
    await session.flush()
    await _wipe_business(session, wsid)
    await session.commit()

    # 1. The issuer block ------------------------------------------------
    await invoice_service.update_settings(session, wsid, dict(INVOICE_SETTINGS))
    await invoice_service.set_issuer_tax_ids(
        session, wsid, [{"kind": "cnpj", "value": COMPANY["cnpj"]}]
    )
    await session.commit()

    # 2. Accounts --------------------------------------------------------
    accounts: dict[str, Account] = {}
    for spec in ACCOUNTS:
        account = Account(
            user_id=uid,
            workspace_id=wsid,
            name=spec["name"],
            type=spec["type"],
            currency=BRL,
            balance=Decimal("0.00"),
            credit_limit=spec.get("credit_limit"),
            statement_close_day=spec.get("statement_close_day"),
            payment_due_day=spec.get("payment_due_day"),
        )
        session.add(account)
        accounts[spec["key"]] = account
    await session.flush()
    checking = accounts["checking"]
    savings = accounts["savings"]
    card = accounts["card"]

    # 3. Categories ------------------------------------------------------
    categories: dict[str, Category] = {}
    for name, color, icon in CATEGORIES:
        row = Category(user_id=uid, workspace_id=wsid, name=name, color=color, icon=icon)
        session.add(row)
        categories[name] = row
    await session.flush()

    # 4. Clients and suppliers -------------------------------------------
    payees: dict[str, Payee] = {}
    for spec in (*CLIENTS, *SUPPLIERS):
        payee = Payee(
            user_id=uid,
            workspace_id=wsid,
            name=spec["name"],
            type="company",
            source="manual",
            is_favorite=spec.get("favorite", False),
            email=spec.get("email"),
            phone=spec.get("phone"),
        )
        session.add(payee)
        if spec.get("cnpj"):
            payee.tax_ids = [PayeeTaxId(kind="cnpj", value=spec["cnpj"])]
        payees[spec["key"]] = payee
    await session.flush()

    # 5. The catalog -----------------------------------------------------
    products: dict[str, tuple[Product, ProductPrice]] = {}
    for spec in PRODUCTS:
        product = await product_service.create_product(
            session,
            wsid,
            uid,
            {
                "name": spec["name"],
                "kind": spec.get("kind", "service"),
                "unit": spec.get("unit"),
                "description": spec.get("description"),
                "fiscal_refs": spec.get("fiscal_refs"),
                "prices": [
                    {
                        "currency": BRL,
                        "unit_price": spec["price"],
                        "billing": spec.get("billing", "one_time"),
                        "interval": spec.get("interval"),
                        "nickname": "Tabela 2026",
                    }
                ],
            },
        )
        products[spec["key"]] = (product, product.prices[0])
    await session.commit()

    # 6. The two recurring agreements ------------------------------------
    social, social_price = products["social"]
    content, content_price = products["content"]
    retainer = await invoice_schedule_service.create_schedule(
        session,
        wsid,
        uid,
        {
            "name": "Gestão de redes sociais, Clínica Bem Viver",
            "payee_id": payees["clinica"].id,
            "frequency": "monthly",
            "start_date": _month_start(today, months - 1),
            "end_type": "never",
            "currency": BRL,
            "payment_terms_days": 10,
            "lines": [
                _term_line(social, social_price, "Gestão de redes sociais, Clínica Bem Viver")
            ],
            "notes": "Pacote mensal de conteúdo e calendário editorial.",
        },
        today=today,
    )
    churned = await invoice_schedule_service.create_schedule(
        session,
        wsid,
        uid,
        {
            "name": "Pacote de conteúdo, Padaria Bela Vista",
            "payee_id": payees["bela_vista"].id,
            "frequency": "monthly",
            "start_date": _month_start(today, 4),
            "end_type": "never",
            "currency": BRL,
            "payment_terms_days": 10,
            "lines": [
                _term_line(content, content_price, "Pacote de conteúdo, Padaria Bela Vista")
            ],
            "notes": "Fotos e publicações do mês.",
        },
        today=today,
    )
    await session.commit()

    # 7. The invoice ledger ----------------------------------------------
    specs: list[dict[str, Any]] = []

    # The active retainer: one invoice per period, every one of them paid.
    for index in range(months):
        start = _month_start(today, months - 1 - index)
        specs.append(
            {
                "kind": "retainer",
                "sequence": index + 1,
                "period_start": start,
                "client": "clinica",
                "product": "social",
                "description": "Gestão de redes sociais, Clínica Bem Viver",
                "amount": Decimal("2200.00"),
                "issue_date": start,
                "due_date": start + timedelta(days=10),
                "status": "paid",
                "revenue": "Receita Recorrente",
            }
        )

    # The agreement that churned after three months.
    for index in range(3):
        start = _month_start(today, 4 - index)
        specs.append(
            {
                "kind": "churned",
                "sequence": index + 1,
                "period_start": start,
                "client": "bela_vista",
                "product": "content",
                "description": "Pacote de conteúdo, Padaria Bela Vista",
                "amount": Decimal("1800.00"),
                "issue_date": start,
                "due_date": start + timedelta(days=10),
                "status": "paid",
                "revenue": "Receita Recorrente",
            }
        )

    for spec in PROJECTS:
        issue = _month_day(today, spec["months_ago"], spec["day"])
        if issue > today:
            issue = today
        specs.append(
            {
                "kind": "project",
                "client": spec["client"],
                "product": spec["product"],
                "description": spec["description"],
                "amount": spec["amount"],
                "issue_date": issue,
                "due_date": issue + timedelta(days=15),
                "status": spec.get("status", "paid"),
                "paid": spec.get("paid"),
                "revenue": "Receita de Serviços",
            }
        )

    # One invoice halfway paid, still inside its payment window: half the
    # money landed, and the rest is not late yet.
    specs.append(
        {
            "kind": "project",
            "client": "clinica",
            "product": "consulting",
            "description": "Consultoria de marca, nova unidade da clínica",
            "amount": Decimal("6200.00"),
            "issue_date": today - timedelta(days=6),
            "due_date": today + timedelta(days=9),
            "status": "partial",
            "paid": Decimal("4000.00"),
            "revenue": "Receita de Serviços",
        }
    )

    # One invoice nobody paid, already past its due date.
    specs.append(
        {
            "kind": "project",
            "client": "bela_vista",
            "product": "website",
            "description": "Site institucional, Padaria Bela Vista",
            "amount": Decimal("2900.00"),
            "issue_date": today - timedelta(days=46),
            "due_date": today - timedelta(days=31),
            "status": "open",
            "revenue": "Receita de Serviços",
        }
    )

    # One open invoice split into three installments.
    installment_days = (22, 52, 82)
    specs.append(
        {
            "kind": "project",
            "client": "horizonte",
            "product": "website",
            "description": "Site institucional, Construtora Horizonte",
            "amount": Decimal("8900.00"),
            "issue_date": today - timedelta(days=8),
            "due_date": today + timedelta(days=installment_days[0]),
            "status": "open",
            "revenue": "Receita de Serviços",
            "installments": [
                {
                    "amount": Decimal("2966.67"),
                    "due_date": today + timedelta(days=installment_days[0]),
                },
                {
                    "amount": Decimal("2966.67"),
                    "due_date": today + timedelta(days=installment_days[1]),
                },
                {
                    "amount": Decimal("2966.66"),
                    "due_date": today + timedelta(days=installment_days[2]),
                },
            ],
        }
    )

    # A proposal still being written.
    specs.append(
        {
            "kind": "project",
            "client": "centro",
            "product": "identity",
            "description": "Identidade visual, Café do Centro",
            "amount": Decimal("4500.00"),
            "issue_date": today - timedelta(days=2),
            "due_date": today + timedelta(days=13),
            "status": "draft",
            "revenue": "Receita de Serviços",
        }
    )

    # Numbered in the order they were issued, so the ledger reads like a
    # ledger and not like the order this file happens to list things in.
    specs.sort(key=lambda spec: (spec["issue_date"], spec["kind"]))

    created: list[Invoice] = []
    for spec in specs:
        product, price = products[spec["product"]]
        invoice = await invoice_service.create_invoice(
            session,
            wsid,
            uid,
            {
                "payee_id": payees[spec["client"]].id,
                "issue_date": spec["issue_date"],
                "due_date": spec["due_date"],
                "currency": BRL,
                "direction": "receivable",
                "lines": [_invoice_line(product, price, spec["description"], spec["amount"])],
                "installments": spec.get("installments"),
                "as_draft": spec["status"] == "draft",
            },
        )
        created.append(invoice)

        if spec["kind"] == "retainer":
            await invoice_schedule_service.link_invoice(
                session, retainer, invoice, spec["period_start"]
            )
        elif spec["kind"] == "churned":
            await invoice_schedule_service.link_invoice(
                session, churned, invoice, spec["period_start"]
            )

        if spec["status"] in ("paid", "partial"):
            settled = spec.get("paid") or spec["amount"]
            paid_on = min(spec["issue_date"] + timedelta(days=5), today)
            await _receive(
                session,
                user_id=uid,
                workspace_id=wsid,
                account=checking,
                category=categories[spec["revenue"]],
                payee=payees[spec["client"]],
                invoice=invoice,
                amount=settled,
                day=paid_on,
            )

    await invoice_schedule_service.end_schedule(
        session,
        churned,
        reason="canceled_by_client",
        ended_at=_month_start(today, 1) - timedelta(days=1),
        today=today,
    )
    await session.commit()

    # 8. Cash movement ---------------------------------------------------
    start = _month_start(today, months - 1)

    # Opening balances, kept out of income and expense: they describe
    # where the studio started, not money it earned.
    for account, opening in (
        (checking, ACCOUNTS[0]["opening"]),
        (savings, ACCOUNTS[1]["opening"]),
    ):
        _add_transaction(
            session,
            user_id=uid,
            workspace_id=wsid,
            account=account,
            day=start,
            description="Saldo inicial",
            amount=opening,
            kind="credit",
            exclude_from_pnl=True,
        )

    for months_ago in range(months):
        for day_number, payee_key, category_name, amount in MONTHLY_COSTS:
            day = _month_day(today, months_ago, day_number)
            if day > today:
                continue
            payee = payees[payee_key] if payee_key else None
            _add_transaction(
                session,
                user_id=uid,
                workspace_id=wsid,
                account=checking,
                day=day,
                description=payee.name if payee is not None else category_name,
                amount=amount,
                kind="debit",
                category=categories[category_name],
                payee=payee,
            )

    # Only the last two card cycles, so the bill in the list is a real
    # one and not the whole card history.
    for months_ago in range(min(months, 2)):
        for payee_key, description, category_name, day_number, amount in CARD_PURCHASES:
            day = _month_day(today, months_ago, day_number)
            if day > today:
                continue
            _add_transaction(
                session,
                user_id=uid,
                workspace_id=wsid,
                account=card,
                day=day,
                description=description,
                amount=amount,
                kind="debit",
                category=categories[category_name],
                payee=payees[payee_key],
            )

    for spec in ONE_OFFS:
        day = _month_day(today, spec["months_ago"], spec["day"])
        if day > today:
            continue
        _add_transaction(
            session,
            user_id=uid,
            workspace_id=wsid,
            account=checking,
            day=day,
            description=spec["description"],
            amount=spec["amount"],
            kind="debit",
            category=categories[spec["category"]],
            payee=payees[spec["payee"]],
        )

    await session.commit()

    # 9. Budgets, rules, recurring bills and one goal --------------------
    for months_ago in range(3):
        month = _month_start(today, months_ago)
        for category_name, amount in BUDGETS:
            session.add(
                Budget(
                    user_id=uid,
                    workspace_id=wsid,
                    category_id=categories[category_name].id,
                    amount=amount,
                    month=month,
                    currency=BRL,
                )
            )

    for spec in RULES:
        session.add(
            Rule(
                user_id=uid,
                workspace_id=wsid,
                name=spec["name"],
                conditions_op="and",
                conditions=spec["conditions"],
                actions=[
                    {"op": "set_category", "value": str(categories[spec["category"]].id)}
                ],
                priority=spec["priority"],
                is_active=True,
            )
        )

    for payee_key, category_name, amount, day_number in RECURRING_BILLS:
        session.add(
            RecurringTransaction(
                user_id=uid,
                workspace_id=wsid,
                account_id=checking.id,
                category_id=categories[category_name].id,
                description=payees[payee_key].name,
                amount=amount,
                currency=BRL,
                type="debit",
                frequency="monthly",
                day_of_month=day_number,
                start_date=_month_start(today, months - 1),
                next_occurrence=_next_occurrence(today, day_number),
                is_active=True,
            )
        )

    session.add(
        Goal(
            user_id=uid,
            workspace_id=wsid,
            name="Reserva de emergência do estúdio",
            target_amount=Decimal("60000.00"),
            current_amount=ACCOUNTS[1]["opening"],
            initial_amount=ACCOUNTS[1]["opening"],
            currency=BRL,
            target_date=today + timedelta(days=365),
            tracking_type="account",
            account_id=savings.id,
            status="active",
            icon="piggy-bank",
            color="#0f766e",
        )
    )
    await session.commit()

    # 10. What the caller and the reset log want to know -----------------
    states: dict[str, int] = {}
    receivable = Decimal("0.00")
    for invoice in created:
        state = invoice_service.derive_state(invoice, today)
        states[state] = states.get(state, 0) + 1
        if state in ("open", "partial", "overdue"):
            receivable += invoice_service.balance(invoice)
    numbers = sorted(invoice.number for invoice in created if invoice.number)

    return {
        "workspace_id": str(wsid),
        "workspace_name": workspace.name,
        "invoices": len(created),
        "states": states,
        "receivable": str(receivable.quantize(CENT)),
        "number_from": numbers[0] if numbers else None,
        "number_to": numbers[-1] if numbers else None,
        "products": len(products),
        "payees": len(payees),
        "schedules": [
            {"id": str(retainer.id), "name": retainer.name, "status": retainer.status},
            {"id": str(churned.id), "name": churned.name, "status": churned.status},
        ],
    }


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
async def seed(email: str, password: str, months: int) -> dict[str, Any]:
    async with async_session_maker() as session:
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            user_db = SQLAlchemyUserDatabase(session, User)
            manager = UserManager(user_db)
            try:
                user = await manager.create(UserCreate(email=email, password=password))
            except UserAlreadyExists:
                user = await session.scalar(select(User).where(User.email == email))
            await session.commit()
        assert user is not None
        workspace = await ensure_business_workspace(session, user)
        return await seed_business(session, user, workspace, months=months)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the business workspace of the public demo"
    )
    parser.add_argument("--email", default="demo@securo.app")
    parser.add_argument("--password", default="DemoSecuro1!")
    parser.add_argument(
        "--months",
        type=int,
        default=6,
        help="How many months of history to seed (default: 6)",
    )
    args = parser.parse_args()
    summary = asyncio.run(seed(args.email, args.password, args.months))
    print("\nBusiness demo seed complete.")
    print(f"  Workspace : {summary['workspace_name']} ({summary['workspace_id']})")
    print(f"  Invoices  : {summary['invoices']} {summary['states']}")
    print(f"  Receivable: {summary['receivable']}")
    print(f"  Numbering : {summary['number_from']} to {summary['number_to']}")


if __name__ == "__main__":
    main()
