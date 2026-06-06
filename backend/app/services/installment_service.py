import hashlib
import json
import re
import uuid
from collections import Counter
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.category import Category
from app.models.transaction import Transaction
from app.schemas.installment import (
    InstallmentCategoryInfo,
    InstallmentItem,
    InstallmentMetrics,
    InstallmentPurchaseDetail,
    InstallmentPurchaseRead,
    InstallmentSummary,
    ManualInstallmentCreate,
    ManualInstallmentUpdate,
)

_INSTALLMENT_RE = re.compile(r"(\d{1,2})\s*[/]\s*(\d{1,2})")


def detect_installment(description: str) -> Optional[tuple[int, int]]:
    match = _INSTALLMENT_RE.search(description)
    if match:
        current, total = int(match.group(1)), int(match.group(2))
        if 1 <= current <= total <= 60:
            return current, total
    return None


def _purchase_key(tx: Transaction) -> tuple:
    return (
        tx.account_id,
        tx.installment_purchase_date,
    )


def _make_purchase_id(key: tuple) -> uuid.UUID:
    raw = f"{key[0]}:{key[1]}"
    h = hashlib.md5(raw.encode()).hexdigest()
    return uuid.UUID(h[:32])


def _resolve_merchant_name(txs: list[Transaction]) -> str:
    payees = [tx.payee for tx in txs if tx.payee]
    if payees:
        return Counter(payees).most_common(1)[0][0]
    return txs[0].description if txs[0].description else "Unknown"


def _is_manual_installment(tx: Transaction) -> bool:
    if not tx.notes:
        return False
    try:
        return json.loads(tx.notes).get("manual_installment") is True
    except (json.JSONDecodeError, AttributeError):
        return False


def _add_months(d: date, months: int) -> date:
    month = d.month + months
    year = d.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def _project_timeline(
    purchase_date: date,
    total_installments: int,
    monthly_amount: Decimal,
    existing_txs: dict[int, Transaction],
) -> list[InstallmentItem]:
    items = []
    paid_dates: list[date] = []
    for i in range(1, total_installments + 1):
        tx = existing_txs.get(i)
        if tx:
            paid_date = tx.effective_date or tx.date
            paid_dates.append(paid_date)

    if paid_dates:
        last_paid = max(paid_dates)
        avg_interval = 30
        if len(paid_dates) > 1:
            sorted_dates = sorted(paid_dates)
            intervals = [(sorted_dates[i+1] - sorted_dates[i]).days for i in range(len(sorted_dates)-1)]
            avg_interval = max(sum(intervals) // len(intervals), 1)
    else:
        last_paid = purchase_date
        avg_interval = 30

    for i in range(1, total_installments + 1):
        tx = existing_txs.get(i)
        if tx:
            paid_date = tx.effective_date or tx.date
            items.append(
                InstallmentItem(
                    number=i,
                    due_date=paid_date,
                    amount=float(tx.amount),
                    status="PAID",
                    transaction_id=tx.id,
                )
            )
        else:
            last_paid = _add_months(last_paid, 1)
            items.append(
                InstallmentItem(
                    number=i,
                    due_date=last_paid,
                    amount=float(monthly_amount),
                    status="PENDING",
                    transaction_id=None,
                )
            )
    return items


def _build_purchase(
    key: tuple,
    txs: list[Transaction],
    account: Optional[Account] = None,
    category: Optional[Category] = None,
) -> InstallmentPurchaseRead:
    purchase_id = _make_purchase_id(key)
    merchant_name = _resolve_merchant_name(txs)

    total_installments = max((tx.total_installments or 0 for tx in txs), default=0)
    paid_count = len(set(tx.installment_number for tx in txs if tx.installment_number))
    current_installment = paid_count

    monthly_amounts = [float(tx.amount) for tx in txs if tx.amount]
    max_monthly = max(monthly_amounts) if monthly_amounts else 0.0
    total_amount = max_monthly * total_installments if total_installments > 0 else 0.0

    total_amount_estimated = any(tx.installment_total_amount is None for tx in txs)

    paid_amount = sum(float(tx.amount) for tx in txs)
    rounding_delta = round(total_amount - paid_amount, 2) if total_amount > 0 else 0.0
    remaining_amount = max(total_amount - paid_amount, 0.0)
    progress = (paid_amount / total_amount * 100) if total_amount > 0 else 0.0

    is_manual = any(_is_manual_installment(tx) for tx in txs)

    if total_installments > 0 and paid_count >= total_installments:
        status = "FINISHED"
    else:
        status = "ACTIVE"

    institution_name = ""
    if account:
        institution_name = account.display_name or account.name

    monthly = txs[0].amount if txs else Decimal("0")
    if is_manual:
        try:
            notes_data = json.loads(txs[0].notes) if txs[0].notes else {}
            manual_monthly = notes_data.get("monthly_amount")
            if manual_monthly:
                monthly = Decimal(str(manual_monthly))
        except (json.JSONDecodeError, AttributeError):
            pass

    cat_info = None
    if category:
        cat_info = InstallmentCategoryInfo(id=category.id, name=category.name, icon=category.icon, color=category.color)

    has_partial_sync = total_installments > 0 and len(txs) < total_installments

    purchase_date = txs[0].installment_purchase_date

    existing_by_number: dict[int, Transaction] = {}
    for tx in txs:
        num = tx.installment_number
        if num is not None:
            existing_by_number[num] = tx

    timeline = _project_timeline(
        purchase_date,
        total_installments,
        monthly,
        existing_by_number,
    )

    start_date = None
    end_date = None
    next_due_date = None
    installments_list = []
    for item in timeline:
        installments_list.append(item)
        if start_date is None or item.due_date < start_date:
            start_date = item.due_date
        if end_date is None or item.due_date > end_date:
            end_date = item.due_date
        if next_due_date is None and item.status == "PENDING":
            next_due_date = item.due_date

    return InstallmentPurchaseRead(
        id=purchase_id,
        merchant_name=merchant_name,
        status=status,
        current_installment=current_installment,
        paid_count=paid_count,
        total_installments=total_installments,
        installment_monthly_amount=float(monthly),
        institution_name=institution_name,
        total_amount=round(total_amount, 2),
        paid_amount=round(paid_amount, 2),
        remaining_amount=round(remaining_amount, 2),
        progress_percentage=round(progress, 1),
        purchase_date=purchase_date or date.today(),
        final_due_date=end_date,
        is_manual=is_manual,
        category=cat_info,
        next_due_date=next_due_date,
        start_date=start_date,
        end_date=end_date,
        total_amount_estimated=total_amount_estimated,
        rounding_delta=rounding_delta,
        has_partial_sync_data=has_partial_sync,
        installments=installments_list,
    )


def _sort_purchases(
    purchases: list[InstallmentPurchaseRead], sort: str
) -> list[InstallmentPurchaseRead]:
    if sort == "amount":
        return sorted(purchases, key=lambda p: p.total_amount, reverse=True)
    elif sort == "remaining":
        return sorted(purchases, key=lambda p: p.remaining_amount, reverse=True)
    return sorted(purchases, key=lambda p: p.purchase_date, reverse=True)


async def _load_accounts_map(
    session: AsyncSession, workspace_id: uuid.UUID
) -> dict[uuid.UUID, Account]:
    result = await session.execute(
        select(Account)
        .where(Account.workspace_id == workspace_id)
        .options(selectinload(Account.connection))
    )
    return {a.id: a for a in result.scalars().all()}


async def _load_categories_map(
    session: AsyncSession, workspace_id: uuid.UUID
) -> dict[uuid.UUID, Category]:
    result = await session.execute(
        select(Category).where(Category.workspace_id == workspace_id)
    )
    return {c.id: c for c in result.scalars().all()}


def _get_seed_category(
    txs: list[Transaction], categories: dict[uuid.UUID, Category]
) -> Optional[Category]:
    seed_tx = min(txs, key=lambda t: t.installment_number or 0)
    if seed_tx.category_id and seed_tx.category_id in categories:
        return categories[seed_tx.category_id]
    return None


async def _load_purchases(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    account_id: Optional[uuid.UUID] = None,
) -> dict[tuple, list[Transaction]]:
    q = select(Transaction).where(
        Transaction.workspace_id == workspace_id,
        Transaction.installment_number.isnot(None),
        Transaction.type == "debit",
        Transaction.is_ignored == False,
    )
    if account_id:
        q = q.where(Transaction.account_id == account_id)

    rows = (await session.execute(q)).scalars().all()

    groups: dict[tuple, list[Transaction]] = {}
    for tx in rows:
        key = _purchase_key(tx)
        groups.setdefault(key, []).append(tx)
    return groups


async def get_summary(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> InstallmentSummary:
    groups = await _load_purchases(session, workspace_id)
    accounts = await _load_accounts_map(session, workspace_id)
    categories = await _load_categories_map(session, workspace_id)

    purchases = []
    for key, txs in groups.items():
        acct = accounts.get(key[0])
        cat = _get_seed_category(txs, categories)
        purchase = _build_purchase(key, txs, acct, cat)
        if purchase.status == "ACTIVE":
            purchases.append(purchase)

    if not purchases:
        return InstallmentSummary(
            active_purchases_count=0,
            total_estimated_amount=0.0,
            total_paid_amount=0.0,
            total_remaining_amount=0.0,
            overall_progress_percentage=0.0,
            final_maturity_date=None,
        )

    total_est = sum(p.total_amount for p in purchases)
    total_paid = sum(p.paid_amount for p in purchases)
    total_rem = sum(p.remaining_amount for p in purchases)
    progress = (total_paid / total_est * 100) if total_est > 0 else 0.0

    final_dates = [p.final_due_date for p in purchases if p.final_due_date]
    final_maturity = max(final_dates) if final_dates else None

    return InstallmentSummary(
        active_purchases_count=len(purchases),
        total_estimated_amount=round(total_est, 2),
        total_paid_amount=round(total_paid, 2),
        total_remaining_amount=round(total_rem, 2),
        overall_progress_percentage=round(progress, 1),
        final_maturity_date=final_maturity,
    )


async def get_purchases(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    status_filter: Optional[str] = None,
    account_id: Optional[uuid.UUID] = None,
    sort: str = "date",
) -> list[InstallmentPurchaseRead]:
    groups = await _load_purchases(session, workspace_id, account_id)
    accounts = await _load_accounts_map(session, workspace_id)
    categories = await _load_categories_map(session, workspace_id)

    purchases = []
    for key, txs in groups.items():
        acct = accounts.get(key[0])
        cat = _get_seed_category(txs, categories)
        purchase = _build_purchase(key, txs, acct, cat)
        if status_filter and purchase.status != status_filter:
            continue
        purchases.append(purchase)

    return _sort_purchases(purchases, sort)


async def get_purchase_details(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    purchase_id: uuid.UUID,
) -> Optional[InstallmentPurchaseDetail]:
    groups = await _load_purchases(session, workspace_id)
    accounts = await _load_accounts_map(session, workspace_id)
    categories = await _load_categories_map(session, workspace_id)

    for key, txs in groups.items():
        if _make_purchase_id(key) == purchase_id:
            acct = accounts.get(key[0])
            cat = _get_seed_category(txs, categories)
            purchase = _build_purchase(key, txs, acct, cat)

            paid_dates = [tx.effective_date or tx.date for tx in txs]
            last_date = max(paid_dates) if paid_dates else None

            institution_name = ""
            if acct:
                institution_name = acct.display_name or acct.name

            timeline = purchase.installments

            return InstallmentPurchaseDetail(
                id=purchase.id,
                merchant_name=purchase.merchant_name,
                purchase_date=purchase.purchase_date,
                total_amount=purchase.total_amount,
                total_installments=purchase.total_installments,
                institution_name=institution_name,
                is_manual=purchase.is_manual,
                account_id=key[0],
                metrics=InstallmentMetrics(
                    paid_amount=purchase.paid_amount,
                    remaining_amount=purchase.remaining_amount,
                    last_installment_date=last_date,
                ),
                installments_timeline=timeline,
            )
    return None


async def create_manual_installment(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: ManualInstallmentCreate,
) -> InstallmentPurchaseRead:
    result = await session.execute(
        select(Account)
        .where(
            Account.id == data.account_id,
            Account.workspace_id == workspace_id,
        )
        .options(selectinload(Account.connection))
    )
    account = result.scalar_one_or_none()
    if not account:
        raise ValueError("Account not found")

    monthly = data.monthly_amount or (data.total_amount / data.total_installments)

    notes_payload = {"manual_installment": True, "monthly_amount": str(monthly)}
    if data.notes:
        notes_payload["user_notes"] = data.notes

    transaction = Transaction(
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=data.account_id,
        description=data.merchant_name,
        amount=monthly,
        currency=account.currency,
        date=data.purchase_date,
        effective_date=data.purchase_date,
        type="debit",
        source="manual",
        status="posted",
        installment_number=1,
        total_installments=data.total_installments,
        installment_total_amount=data.total_amount,
        installment_purchase_date=data.purchase_date,
        category_id=data.category_id,
        notes=json.dumps(notes_payload),
    )
    session.add(transaction)
    await session.flush()
    await session.commit()
    await session.refresh(transaction)

    key = _purchase_key(transaction)
    cat = None
    if transaction.category_id:
        cat_result = await session.execute(
            select(Category).where(Category.id == transaction.category_id)
        )
        cat = cat_result.scalar_one_or_none()
    return _build_purchase(key, [transaction], account, cat)


async def update_manual_installment(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    purchase_id: uuid.UUID,
    data: ManualInstallmentUpdate,
) -> Optional[InstallmentPurchaseRead]:
    groups = await _load_purchases(session, workspace_id)
    accounts = await _load_accounts_map(session, workspace_id)

    for key, txs in groups.items():
        if _make_purchase_id(key) == purchase_id:
            seed_tx = min(txs, key=lambda t: t.installment_number or 0)
            if seed_tx.source != "manual":
                raise ValueError("Only manual installments can be edited")

            update_data = data.model_dump(exclude_unset=True)
            if "monthly_amount" in update_data or "total_amount" in update_data:
                monthly = update_data.get("monthly_amount") or (
                    data.total_amount / data.total_installments
                    if data.total_amount and data.total_installments
                    else seed_tx.amount
                )
                seed_tx.amount = monthly
                if data.total_amount:
                    seed_tx.installment_total_amount = data.total_amount

            if "total_installments" in update_data:
                seed_tx.total_installments = data.total_installments
            if "purchase_date" in update_data:
                seed_tx.installment_purchase_date = data.purchase_date
                seed_tx.date = data.purchase_date
                seed_tx.effective_date = data.purchase_date
            if "category_id" in update_data:
                seed_tx.category_id = data.category_id
            if "merchant_name" in update_data:
                seed_tx.description = data.merchant_name

            try:
                notes_data = json.loads(seed_tx.notes) if seed_tx.notes else {}
            except (json.JSONDecodeError, AttributeError):
                notes_data = {}
            notes_data["manual_installment"] = True
            if seed_tx.amount:
                notes_data["monthly_amount"] = str(seed_tx.amount)
            if data.notes is not None:
                notes_data["user_notes"] = data.notes
            seed_tx.notes = json.dumps(notes_data)

            await session.commit()
            await session.refresh(seed_tx)

            acct = accounts.get(key[0])
            cat = None
            if seed_tx.category_id:
                cat_result = await session.execute(
                    select(Category).where(Category.id == seed_tx.category_id)
                )
                cat = cat_result.scalar_one_or_none()
            return _build_purchase(key, [seed_tx], acct, cat)
    return None


async def delete_manual_installment(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    purchase_id: uuid.UUID,
) -> bool:
    groups = await _load_purchases(session, workspace_id)

    for key, txs in groups.items():
        if _make_purchase_id(key) == purchase_id:
            seed_tx = min(txs, key=lambda t: t.installment_number or 0)
            if seed_tx.source != "manual":
                raise ValueError("Only manual installments can be deleted")
            await session.delete(seed_tx)
            await session.commit()
            return True
    return False


async def mark_installment_paid(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    purchase_id: uuid.UUID,
    installment_number: int,
    amount: Decimal,
    payment_date: date,
) -> Optional[InstallmentPurchaseRead]:
    groups = await _load_purchases(session, workspace_id)
    accounts = await _load_accounts_map(session, workspace_id)

    for key, txs in groups.items():
        if _make_purchase_id(key) == purchase_id:
            existing_numbers = {tx.installment_number for tx in txs}
            if installment_number in existing_numbers:
                raise ValueError(f"Installment {installment_number} already paid")

            seed_tx = min(txs, key=lambda t: t.installment_number or 0)
            account = accounts.get(key[0])
            if not account:
                raise ValueError("Account not found")

            new_tx = Transaction(
                user_id=seed_tx.user_id,
                workspace_id=workspace_id,
                account_id=key[0],
                description=seed_tx.description,
                amount=amount,
                currency=account.currency,
                date=payment_date,
                effective_date=payment_date,
                type="debit",
                source="manual",
                status="posted",
                installment_number=installment_number,
                total_installments=seed_tx.total_installments,
                installment_total_amount=seed_tx.installment_total_amount,
                installment_purchase_date=key[1],
                category_id=seed_tx.category_id,
                notes=json.dumps({"manual_installment": True, "paid_for_installment": installment_number}),
            )
            session.add(new_tx)
            await session.flush()
            await session.commit()

            updated_groups = await _load_purchases(session, workspace_id)
            updated_txs = updated_groups.get(key, txs)
            cat = None
            if seed_tx.category_id:
                cat_result = await session.execute(
                    select(Category).where(Category.id == seed_tx.category_id)
                )
                cat = cat_result.scalar_one_or_none()
            return _build_purchase(key, updated_txs, account, cat)
    return None
