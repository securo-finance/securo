"""A restore brings back the invoice facts the service derives.

An invoice's `paid`, `partial` and `overdue` are not columns: they come
from the allocations and the deductions that settled it and from the
installment dates. The numbering cursor in `invoice_settings` and the
schedule's `next_sequence` are engine state the job reads next run.

Restoring the family row by row is only useful if all of that survives.
A workspace that comes back with a paid invoice reading `open` has a
receivable that never existed, and one whose numbering cursor went back
dies on `uq_invoices_workspace_series_number` the first time it issues.
This test takes a ledger with every one of those states through a
backup and compares the derived state before and after.
"""
import io
import uuid
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.invoice import (
    Invoice,
    InvoiceAllocation,
    InvoiceDeduction,
    InvoiceInstallment,
    InvoiceSettings,
)
from app.models.invoice_schedule import InvoiceSchedule
from app.models.transaction import Transaction
from app.services import backup_service, invoice_service


TODAY = date(2026, 11, 19)


async def _restored_invoices(session: AsyncSession, workspace_id: uuid.UUID) -> dict[int, Invoice]:
    rows = (
        (
            await session.execute(
                select(Invoice).where(Invoice.workspace_id == workspace_id).order_by(Invoice.number)
            )
        )
        .scalars()
        .all()
    )
    by_number: dict[int, Invoice] = {}
    for invoice in rows:
        # Every invoice in this fixture is issued, so each one carries a number.
        assert invoice.number is not None
        by_number[invoice.number] = invoice
    return by_number


@pytest.mark.asyncio
async def test_restore_keeps_the_derived_invoice_state(
    session: AsyncSession,
    test_user,
    test_workspace,
):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="PJ",
        type="checking",
        currency="BRL",
        balance=Decimal("0"),
    )
    settings = InvoiceSettings(
        workspace_id=test_workspace.id,
        series="AUR",
        number_prefix="AUR-",
        next_number=18,
    )
    schedule = InvoiceSchedule(
        id=uuid.uuid4(),
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        name="Retainer mensal",
        frequency="monthly",
        start_date=date(2026, 5, 5),
        currency="BRL",
        next_sequence=7,
        consecutive_failures=2,
        last_generated_at=datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
    )
    # 3,000.00 invoiced, 1,000.00 paid in cash and 500.00 withheld: the
    # invoice is short by 1,500.00 and late on the second installment.
    partial = Invoice(
        id=uuid.uuid4(),
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        number=15,
        series="AUR",
        status="open",
        issue_date=date(2026, 8, 5),
        due_date=date(2026, 11, 10),
        currency="BRL",
        subtotal=Decimal("3000.00"),
        total=Decimal("3000.00"),
        schedule_id=schedule.id,
        sequence=6,
        period_start=date(2026, 10, 5),
        period_end=date(2026, 11, 4),
    )
    # Fully settled by a credit note rather than by cash.
    paid = Invoice(
        id=uuid.uuid4(),
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        number=16,
        series="AUR",
        status="open",
        issue_date=date(2026, 9, 5),
        due_date=date(2026, 10, 5),
        currency="BRL",
        subtotal=Decimal("2000.00"),
        total=Decimal("2000.00"),
    )
    credit_note = Invoice(
        id=uuid.uuid4(),
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        number=17,
        series="AUR",
        document_type="credit_note",
        status="open",
        issue_date=date(2026, 9, 20),
        due_date=date(2026, 9, 20),
        currency="BRL",
        total=Decimal("0.00"),
        corrects_id=paid.id,
    )
    session.add_all([account, settings, schedule, partial, paid, credit_note])
    await session.flush()

    transaction = Transaction(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description="PIX Aurora",
        amount=Decimal("1000.00"),
        currency="BRL",
        date=date(2026, 9, 12),
        type="credit",
        source="manual",
    )
    session.add(transaction)
    await session.flush()

    session.add_all(
        [
            InvoiceInstallment(
                invoice_id=partial.id,
                workspace_id=test_workspace.id,
                position=0,
                due_date=date(2026, 9, 10),
                amount=Decimal("1000.00"),
            ),
            InvoiceInstallment(
                invoice_id=partial.id,
                workspace_id=test_workspace.id,
                position=1,
                due_date=date(2026, 10, 10),
                amount=Decimal("1000.00"),
            ),
            InvoiceInstallment(
                invoice_id=partial.id,
                workspace_id=test_workspace.id,
                position=2,
                due_date=date(2026, 11, 10),
                amount=Decimal("1000.00"),
            ),
            InvoiceAllocation(
                invoice_id=partial.id,
                workspace_id=test_workspace.id,
                transaction_id=transaction.id,
                amount=Decimal("1000.00"),
            ),
            InvoiceDeduction(
                invoice_id=partial.id,
                workspace_id=test_workspace.id,
                kind="withholding_tax",
                amount=Decimal("500.00"),
            ),
            InvoiceAllocation(
                invoice_id=paid.id,
                workspace_id=test_workspace.id,
                credit_note_id=credit_note.id,
                amount=Decimal("2000.00"),
            ),
        ]
    )
    await session.commit()

    before = await _restored_invoices(session, test_workspace.id)
    before_states = {
        number: invoice_service.derive_state(invoice, TODAY) for number, invoice in before.items()
    }
    before_receivable = sum((invoice_service.balance(invoice) for invoice in before.values()), Decimal())
    assert before_states == {15: "overdue", 16: "paid", 17: "paid"}
    assert before_receivable == Decimal("1500.00")

    data = await backup_service.build_backup_zip(session, test_workspace)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        counts = zf.read("metadata.json")
    assert b'"invoice_allocations": 2' in counts
    assert b'"invoice_deductions": 1' in counts
    assert b'"invoice_installments": 3' in counts

    result = await backup_service.restore_backup_bytes(
        session,
        data=data,
        target_user_id=test_user.id,
        current_workspace=test_workspace,
        content=backup_service.BackupContent.both,
        mode=backup_service.BackupRestoreMode.new_workspace,
    )

    after = await _restored_invoices(session, result.workspace_id)
    assert set(after) == {15, 16, 17}
    after_states = {
        number: invoice_service.derive_state(invoice, TODAY) for number, invoice in after.items()
    }
    assert after_states == before_states

    after_receivable = sum(
        (invoice_service.balance(invoice) for invoice in after.values()), Decimal()
    )
    assert after_receivable == before_receivable

    # The numbering cursor travels, so the next invoice issued in the
    # restored workspace takes 18 instead of colliding with a number
    # that is already there.
    restored_settings = (
        await session.execute(
            select(InvoiceSettings).where(InvoiceSettings.workspace_id == result.workspace_id)
        )
    ).scalar_one()
    assert restored_settings.next_number == 18
    assert restored_settings.series == "AUR"
    assert restored_settings.number_prefix == "AUR-"
    assert restored_settings.next_number > max(after)

    # The schedule keeps the period it would emit and how many times it
    # has failed, so the job neither re-bills October nor forgets that
    # it is about to be paused.
    restored_schedule = (
        await session.execute(
            select(InvoiceSchedule).where(InvoiceSchedule.workspace_id == result.workspace_id)
        )
    ).scalar_one()
    assert restored_schedule.next_sequence == 7
    assert restored_schedule.consecutive_failures == 2
    assert restored_schedule.status == "active"
    # SQLite hands the timestamp back naive; the instant is what matters.
    assert restored_schedule.last_generated_at is not None
    assert schedule.last_generated_at is not None
    assert restored_schedule.last_generated_at.replace(tzinfo=None) == (
        schedule.last_generated_at.replace(tzinfo=None)
    )
    assert after[15].schedule_id == restored_schedule.id
    assert after[15].sequence == 6
    assert after[15].period_start == date(2026, 10, 5)

    # Every reference points at the copy, not at the old workspace.
    restored_transaction_id = (
        await session.execute(
            select(Transaction.id).where(Transaction.workspace_id == result.workspace_id)
        )
    ).scalar_one()
    assert restored_transaction_id != transaction.id
    (restored_cash,) = [a for a in after[15].allocations if a.transaction_id is not None]
    assert restored_cash.transaction_id == restored_transaction_id
    assert restored_cash.amount == Decimal("1000.00")

    (restored_credit_note,) = [a for a in after[16].allocations if a.credit_note_id is not None]
    assert restored_credit_note.credit_note_id == after[17].id
    assert after[17].corrects_id == after[16].id

    (restored_deduction,) = after[15].deductions
    assert restored_deduction.kind == "withholding_tax"
    assert restored_deduction.amount == Decimal("500.00")

    assert [i.amount for i in after[15].installments] == [
        Decimal("1000.00"),
        Decimal("1000.00"),
        Decimal("1000.00"),
    ]
    assert invoice_service.first_unpaid_due(after[15]) == date(2026, 10, 10)
