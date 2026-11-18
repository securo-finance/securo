"""The business demo seed fills the workspace the demo landing shows.

The two scripts write to their own database in these tests: the seed
commits as it goes, and the shared in-memory session of the rest of the
suite must not gain eighteen invoices and a catalog from here.
"""
import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register every table for create_all)
from app.core.database import Base
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
            ("cnpj", "11222333000181")
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
