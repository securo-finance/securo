"""The business demo seed fills the workspace the demo landing shows.

The two scripts write to their own database in these tests: the seed
commits as it goes, and the shared in-memory session of the rest of the
suite must not gain eighteen invoices and a catalog from here.
"""
import importlib.util
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register every table for create_all)
from app.core.database import Base
from app.fiscal.validators import validate_br_cnpj
from app.models.account import Account
from app.models.invoice import Invoice
from app.models.invoice_schedule import InvoiceSchedule
from app.models.payee import Payee, PayeeTaxId
from app.models.product import Product
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceTaxId
from app.services import invoice_service

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
TODAY = date(2026, 11, 18)


def _load(name: str):
    """Load a sibling script by path, the way the container runs it."""
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _workspaces(session: AsyncSession, user_id) -> list[Workspace]:
    rows = await session.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == user_id,
            Workspace.is_archived.is_(False),
        )
        .order_by(Workspace.created_at.asc())
    )
    return list(rows.unique().scalars().all())


async def _invoices(session: AsyncSession, workspace_id) -> list[Invoice]:
    rows = await session.execute(select(Invoice).where(Invoice.workspace_id == workspace_id))
    return list(rows.unique().scalars().all())


@pytest.fixture
async def demo_database():
    """A private in-memory database for one test, thrown away at the end."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def test_every_seeded_cnpj_is_synthetic_and_passes_the_check_digits():
    """The demo is public, so no real company may be identified on it.

    Each number sits in the 99.000.0XX/0001 range: valid under the mod-11
    rule the fiscal pack applies, and registered to nobody (checked
    against BrasilAPI). The formatted copy the printed invoice carries
    has to be the same number.
    """
    seed = _load("seed_demo").business_seed
    numbers = [seed.COMPANY["cnpj"], seed.SECOND_COMPANY["cnpj"]]
    numbers += [row["cnpj"] for row in seed.CLIENTS]
    numbers += [row["cnpj"] for row in seed.SECOND_CLIENTS]

    assert len(numbers) == 8
    assert len(set(numbers)) == 8
    for value in numbers:
        assert value.startswith("990000"), value
        assert validate_br_cnpj(value) is None, value

    for company, settings in (
        (seed.COMPANY, seed.INVOICE_SETTINGS),
        (seed.SECOND_COMPANY, seed.SECOND_INVOICE_SETTINGS),
    ):
        printed = _digits(settings["footer_note"] + settings["payment_details"])
        assert company["cnpj"] in printed


async def test_business_seed_lists_first_and_covers_every_invoice_state(demo_database):
    session_maker = demo_database
    demo = _load("seed_demo")
    seed = demo.business_seed

    async with session_maker() as session:
        user = User(
            email="demo@example.test",
            hashed_password="not-used-here",
            is_active=True,
            is_verified=True,
            is_superuser=False,
            preferences={"language": "pt-BR", "currency_display": "BRL"},
        )
        session.add(user)
        await session.commit()

        personal = await demo._ensure_personal_workspace(session, user)
        business = await seed.ensure_business_workspace(
            session, user, before=personal.created_at
        )

        workspaces = await _workspaces(session, user.id)
        assert [row.kind for row in workspaces] == ["business", "personal"]
        assert workspaces[0].id == business.id

        summary = await seed.seed_business(session, user, business, months=6, today=TODAY)

        # Every derived state the invoice list can show is represented.
        assert summary["states"] == {
            "paid": 14,
            "partial": 1,
            "overdue": 1,
            "open": 1,
            "draft": 1,
        }
        assert summary["invoices"] == 18
        assert summary["receivable"] == "14000.00"
        assert summary["number_from"] == 1
        assert summary["number_to"] == 17

        invoices = await _invoices(session, business.id)
        states = {}
        for invoice in invoices:
            state = invoice_service.derive_state(invoice, TODAY)
            states[state] = states.get(state, 0) + 1
            if state in ("open", "partial", "overdue"):
                assert invoice_service.balance(invoice) > 0
        assert states == summary["states"]

        # The installment plan adds up to the invoice, to the cent.
        plans = [invoice for invoice in invoices if invoice.installments]
        assert len(plans) == 1
        plan = plans[0]
        assert len(plan.installments) == 3
        assert sum(row.amount for row in plan.installments) == plan.total
        assert plan.due_date == max(row.due_date for row in plan.installments)

        # One agreement still billing, one that churned with a reason.
        schedules = list(
            (
                await session.execute(
                    select(InvoiceSchedule).where(
                        InvoiceSchedule.workspace_id == business.id
                    )
                )
            )
            .unique()
            .scalars()
            .all()
        )
        by_name = {row.name: row for row in schedules}
        retainer = by_name["Gestão de redes sociais, Clínica Bem Viver"]
        churned = by_name["Pacote de conteúdo, Padaria Bela Vista"]
        assert retainer.status == "active"
        assert churned.status == "ended"
        assert churned.end_reason == "canceled_by_client"

        async def _linked(schedule: InvoiceSchedule) -> int:
            return await session.scalar(
                select(func.count())
                .select_from(Invoice)
                .where(Invoice.schedule_id == schedule.id)
            )

        assert await _linked(retainer) == 6
        assert await _linked(churned) == 3

        # The issuer identity the printed document needs.
        tax_ids = list(
            (
                await session.execute(
                    select(WorkspaceTaxId).where(WorkspaceTaxId.workspace_id == business.id)
                )
            )
            .scalars()
            .all()
        )
        assert [(row.kind, row.value) for row in tax_ids] == [
            ("cnpj", "99000001000101")
        ]
        settings = await invoice_service.get_settings(session, business.id)
        assert settings.issuer_display_name == "Aurora Studio"
        assert settings.series == "A"
        assert settings.next_number == 18
        assert settings.default_payment_terms_days == 15

        # Catalog, clients and suppliers travelled with it.
        assert summary["products"] == 5
        assert await session.scalar(
            select(func.count()).select_from(Product).where(Product.workspace_id == business.id)
        ) == 5
        assert await session.scalar(
            select(func.count()).select_from(Payee).where(Payee.workspace_id == business.id)
        ) == 14
        client_tax_ids = list(
            (
                await session.execute(
                    select(PayeeTaxId).where(
                        PayeeTaxId.payee_id.in_(
                            select(Payee.id).where(Payee.workspace_id == business.id)
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(client_tax_ids) == 4
        assert all(row.kind == "cnpj" and len(row.value) == 14 for row in client_tax_ids)

        # Opening balances stay out of the profit and loss report.
        opening = list(
            (
                await session.execute(
                    select(Transaction).where(
                        Transaction.workspace_id == business.id,
                        Transaction.description == "Saldo inicial",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(opening) == 2
        assert all(row.exclude_from_pnl for row in opening)
        assert {row.amount for row in opening} == {
            Decimal("18500.00"),
            Decimal("24000.00"),
        }

        # Running it again rewrites the same numbers instead of doubling them.
        again = await seed.seed_business(session, user, business, months=6, today=TODAY)
        assert again["states"] == summary["states"]
        assert again["invoices"] == summary["invoices"]
        assert again["receivable"] == summary["receivable"]
        assert again["number_from"] == summary["number_from"]
        assert again["number_to"] == summary["number_to"]
        assert await session.scalar(
            select(func.count()).select_from(Payee).where(Payee.workspace_id == business.id)
        ) == 14

        workspaces = await _workspaces(session, user.id)
        assert [row.kind for row in workspaces] == ["business", "personal"]


async def test_second_company_sits_between_the_studio_and_the_personal_wallet(
    demo_database,
):
    """Two businesses list before the household, in the order the demo shows.

    The second company exists so a screenshot can show two companies'
    records apart; it needs its own identity, account and receivables,
    and it must not disturb the studio's ledger or the personal wallet.
    """
    session_maker = demo_database
    demo = _load("seed_demo")
    seed = demo.business_seed

    async with session_maker() as session:
        user = User(
            email="demo@example.test",
            hashed_password="not-used-here",
            is_active=True,
            is_verified=True,
            is_superuser=False,
            preferences={"language": "pt-BR", "currency_display": "BRL"},
        )
        session.add(user)
        await session.commit()

        personal = await demo._ensure_personal_workspace(session, user)
        studio = await seed.ensure_business_workspace(
            session, user, before=personal.created_at
        )
        second = await seed.ensure_second_workspace(
            session, user, before=personal.created_at, exclude=(studio.id,)
        )

        workspaces = await _workspaces(session, user.id)
        assert [row.kind for row in workspaces] == [
            "business",
            "business",
            "personal",
        ]
        assert [row.id for row in workspaces] == [
            studio.id,
            second.id,
            personal.id,
        ]
        assert [row.name for row in workspaces] == [
            "Aurora Studio",
            "Ponte Norte Consultoria",
            "Pessoal",
        ]
        assert (
            workspaces[0].created_at
            < workspaces[1].created_at
            < workspaces[2].created_at
        )

        studio_summary = await seed.seed_business(
            session, user, studio, months=6, today=TODAY
        )
        summary = await seed.seed_second_company(session, user, second, today=TODAY)

        # The short ledger: two settled, one open, one overdue.
        assert summary["invoices"] == 4
        assert summary["states"] == {"paid": 2, "open": 1, "overdue": 1}
        assert summary["receivable"] == "6200.00"
        assert summary["number_from"] == 1
        assert summary["number_to"] == 4
        assert summary["products"] == 2
        assert summary["payees"] == 2

        invoices = await _invoices(session, second.id)
        states: dict[str, int] = {}
        for invoice in invoices:
            state = invoice_service.derive_state(invoice, TODAY)
            states[state] = states.get(state, 0) + 1
        assert states == summary["states"]
        assert sorted(invoice.number for invoice in invoices) == [1, 2, 3, 4]

        # Its own issuer identity, separate from the studio's.
        tax_ids = list(
            (
                await session.execute(
                    select(WorkspaceTaxId).where(WorkspaceTaxId.workspace_id == second.id)
                )
            )
            .scalars()
            .all()
        )
        assert [(row.kind, row.value) for row in tax_ids] == [
            ("cnpj", "99000006000126")
        ]
        settings = await invoice_service.get_settings(session, second.id)
        assert settings.issuer_display_name == "Ponte Norte Consultoria"
        assert settings.number_prefix == "PNO-"
        assert settings.next_number == 5
        assert settings.default_payment_terms_days == 20

        # Its own account and movement.
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Account)
                .where(Account.workspace_id == second.id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.workspace_id == second.id)
            )
            > 0
        )

        # The studio's ledger and the personal wallet are untouched by it.
        assert len(await _invoices(session, studio.id)) == studio_summary["invoices"]
        assert studio_summary["states"] == {
            "paid": 14,
            "partial": 1,
            "overdue": 1,
            "open": 1,
            "draft": 1,
        }
        assert studio_summary["receivable"] == "14000.00"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Invoice)
                .where(Invoice.workspace_id == personal.id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Account)
                .where(Account.workspace_id == personal.id)
            )
            == 0
        )

        # Running it again rewrites the same numbers, invoices included.
        again = await seed.seed_second_company(session, user, second, today=TODAY)
        assert again["states"] == summary["states"]
        assert again["receivable"] == summary["receivable"]
        assert again["number_from"] == summary["number_from"]
        assert again["number_to"] == summary["number_to"]
        assert len(await _invoices(session, second.id)) == 4

        # The reset keeps all three wallets and archives anything else.
        stray = Workspace(
            name="Workspace do visitante",
            kind="business",
            created_by_user_id=user.id,
        )
        session.add(stray)
        await session.flush()
        session.add(
            WorkspaceMember(workspace_id=stray.id, user_id=user.id, role="owner")
        )
        await session.commit()

        archived = await seed.archive_other_workspaces(
            session, user, keep=[studio.id, second.id, personal.id]
        )
        assert archived == 1
        await session.refresh(stray)
        assert stray.is_archived
        assert [row.id for row in await _workspaces(session, user.id)] == [
            studio.id,
            second.id,
            personal.id,
        ]


async def test_standalone_seed_still_opens_on_the_studio(demo_database):
    """Run alone on a database that already has the personal wallet.

    The reset passes `before` in; the standalone entry point has to find
    it itself, or both companies would be created after the wallet and
    the visitor would open on the personal one.
    """
    session_maker = demo_database
    demo = _load("seed_demo")
    seed = demo.business_seed

    async with session_maker() as session:
        user = User(
            email="demo@example.test",
            hashed_password="not-used-here",
            is_active=True,
            is_verified=True,
            is_superuser=False,
            preferences={"language": "pt-BR", "currency_display": "BRL"},
        )
        session.add(user)
        await session.commit()
        user_id = user.id
        personal = await demo._ensure_personal_workspace(session, user)
        personal_id = personal.id

    summaries = await seed.seed(
        "demo@example.test", "DemoSecuro1!", 6, session_factory=session_maker
    )
    assert summaries["business"]["workspace_name"] == "Aurora Studio"
    assert summaries["second"]["workspace_name"] == "Ponte Norte Consultoria"

    async with session_maker() as session:
        rows = await _workspaces(session, user_id)
        assert [row.kind for row in rows] == ["business", "business", "personal"]
        assert str(rows[0].id) == summaries["business"]["workspace_id"]
        assert str(rows[1].id) == summaries["second"]["workspace_id"]
        assert rows[2].id == personal_id
