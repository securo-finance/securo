import re
import uuid
from datetime import date
from typing import Optional

from sqlalchemy import select, func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.transaction import Transaction
from app.models.transaction_attachment import TransactionAttachment
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.payee import Payee
from app.schemas.transaction import TransactionCreate, TransactionUpdate, TransferCreate
from app.services.credit_card_service import apply_effective_date
from app.services.rule_service import apply_rules_to_transaction
from app.services.fx_rate_service import stamp_primary_amount, convert as fx_convert


def _apply_fx_override(transaction, amount, amount_primary=None, fx_rate_used=None):
    """Apply manual FX override values to a transaction.

    - Both provided → use as-is
    - Only amount_primary → derive rate = amount_primary / amount
    - Only fx_rate_used → derive amount_primary = amount * fx_rate_used
    """
    from decimal import Decimal, ROUND_HALF_UP

    amount = Decimal(str(amount))
    if amount_primary is not None and fx_rate_used is not None:
        transaction.amount_primary = Decimal(str(amount_primary))
        transaction.fx_rate_used = Decimal(str(fx_rate_used))
    elif amount_primary is not None:
        transaction.amount_primary = Decimal(str(amount_primary))
        if amount:
            transaction.fx_rate_used = (Decimal(str(amount_primary)) / amount)
        else:
            transaction.fx_rate_used = Decimal("1")
    elif fx_rate_used is not None:
        transaction.fx_rate_used = Decimal(str(fx_rate_used))
        transaction.amount_primary = (amount * Decimal(str(fx_rate_used))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )


async def get_transactions(
    session: AsyncSession,
    user_id: uuid.UUID,
    account_id: Optional[uuid.UUID] = None,
    category_id: Optional[uuid.UUID] = None,
    payee_id: Optional[uuid.UUID] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    page: int = 1,
    limit: int = 50,
    include_opening_balance: bool = False,
    search: Optional[str] = None,
    uncategorized: bool = False,
    txn_type: Optional[str] = None,
    skip_pagination: bool = False,
    exclude_transfers: bool = False,
    account_ids: Optional[list[uuid.UUID]] = None,
    category_ids: Optional[list[uuid.UUID]] = None,
    accounting_mode: Optional[str] = None,
    tags: Optional[list[str]] = None,
    bill_id: Optional[uuid.UUID] = None,
) -> tuple[list[Transaction], int]:
    # In "accrual" mode, bucket/order by effective_date so list filters
    # line up with the cash-flow view used by the dashboard and reports.
    # When the user has set a manual cycle override (effective_bill_date)
    # we honor it FIRST regardless of accounting mode — that's the whole
    # point of the override (issue #92, LucasFidelis suggestion).
    date_col = func.coalesce(
        Transaction.effective_bill_date,
        Transaction.effective_date if accounting_mode == "accrual" else Transaction.date,
    )
    # Base query: user's own transactions (manual or via account)
    base_query = (
        select(Transaction)
        .outerjoin(Account)
        .outerjoin(BankConnection)
        .outerjoin(Payee, Transaction.payee_id == Payee.id)
        .where(
            or_(
                Transaction.user_id == user_id,
                BankConnection.user_id == user_id,
            )
        )
        .options(selectinload(Transaction.category), selectinload(Transaction.account), selectinload(Transaction.payee_entity))
    )

    # Exclude opening_balance transactions from the normal list unless explicitly requested
    if not include_opening_balance:
        base_query = base_query.where(Transaction.source != "opening_balance")

    # Apply filters
    # Multi-id filters take precedence over single-id filters.
    if account_ids:
        base_query = base_query.where(Transaction.account_id.in_(account_ids))
    elif account_id:
        base_query = base_query.where(Transaction.account_id == account_id)
    if category_ids:
        base_query = base_query.where(Transaction.category_id.in_(category_ids))
    elif category_id:
        base_query = base_query.where(Transaction.category_id == category_id)
    if payee_id:
        base_query = base_query.where(Transaction.payee_id == payee_id)
    if uncategorized:
        base_query = base_query.where(
            Transaction.category_id == None,
            Transaction.transfer_pair_id.is_(None),
        )
    if exclude_transfers:
        base_query = base_query.where(Transaction.transfer_pair_id.is_(None))
    if txn_type:
        base_query = base_query.where(Transaction.type == txn_type)
    # Bill-driven filter: when the caller passes bill_id, include
    #   (a) txs linked to this bill via Pluggy's billId mapping (handles
    #       charges the bank rolled into a bill whose nominal range doesn't
    #       contain them — e.g. a 30/03 charge in the May statement), AND
    #   (b) txs with NO bill_id (manual / OFX / CSV / recurring fills) whose
    #       bucketing date falls in the cycle window. Without (b) we'd drop
    #       user-added entries that exist precisely to compensate for txs
    #       the provider failed to fetch (issue #92, abdalanervoso's Wellhub
    #       case on Bradesco).
    # Without bill_id (cycle-math cycles or non-CC), apply the date window
    # straight to all txs.
    if bill_id is not None:
        bill_predicates = [Transaction.bill_id == bill_id]
        if from_date or to_date:
            unlinked_clauses = [Transaction.bill_id.is_(None)]
            if from_date:
                unlinked_clauses.append(date_col >= from_date)
            if to_date:
                unlinked_clauses.append(date_col <= to_date)
            from sqlalchemy import and_ as _and
            bill_predicates.append(_and(*unlinked_clauses))
        base_query = base_query.where(or_(*bill_predicates))
    else:
        if from_date:
            base_query = base_query.where(date_col >= from_date)
        if to_date:
            base_query = base_query.where(date_col <= to_date)
    if search:
        term = f"%{search}%"
        base_query = base_query.where(
            or_(
                Transaction.description.ilike(term),
                Transaction.payee.ilike(term),
                Transaction.notes.ilike(term),
                Payee.name.ilike(term),
            )
        )
    if tags:
        # Exact tag match using portable ILIKE patterns so `#test` never
        # matches `#test2`. Multiple tags are OR-combined (union) — a row
        # matches if it carries ANY of the requested tags. The four boundary
        # variants cover position at start / middle / end / standalone
        # (issue #88).
        clauses = []
        for raw_tag in tags:
            tag = raw_tag if raw_tag.startswith("#") else f"#{raw_tag}"
            clauses.extend([
                Transaction.notes == tag,
                Transaction.notes.ilike(f"{tag} %"),
                Transaction.notes.ilike(f"% {tag}"),
                Transaction.notes.ilike(f"% {tag} %"),
            ])
        base_query = base_query.where(or_(*clauses))

    # Get total count
    count_query = select(func.count()).select_from(base_query.subquery())
    total = await session.scalar(count_query)

    # Apply ordering (and pagination unless skipped)
    query = base_query.order_by(date_col.desc(), Transaction.created_at.desc())
    if not skip_pagination:
        query = query.offset((page - 1) * limit).limit(limit)

    result = await session.execute(query)
    transactions = list(result.scalars().all())

    # Batch-load attachment counts in a single query
    if transactions:
        tx_ids = [tx.id for tx in transactions]
        count_rows = await session.execute(
            select(
                TransactionAttachment.transaction_id,
                func.count(TransactionAttachment.id),
            )
            .where(TransactionAttachment.transaction_id.in_(tx_ids))
            .group_by(TransactionAttachment.transaction_id)
        )
        counts = dict(count_rows.all())
        for tx in transactions:
            tx.attachment_count = counts.get(tx.id, 0)
            tx.payee_name = tx.payee_entity.name if tx.payee_entity else None

    return transactions, total or 0


async def get_transaction(
    session: AsyncSession, transaction_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[Transaction]:
    result = await session.execute(
        select(Transaction)
        .outerjoin(Account)
        .outerjoin(BankConnection)
        .where(
            Transaction.id == transaction_id,
            or_(
                Transaction.user_id == user_id,
                BankConnection.user_id == user_id,
            ),
        )
        .options(selectinload(Transaction.category), selectinload(Transaction.payee_entity))
    )
    transaction = result.scalar_one_or_none()
    if transaction:
        count_result = await session.execute(
            select(func.count(TransactionAttachment.id)).where(
                TransactionAttachment.transaction_id == transaction.id
            )
        )
        transaction.attachment_count = count_result.scalar_one()
        transaction.payee_name = transaction.payee_entity.name if transaction.payee_entity else None
    return transaction


async def create_transaction(
    session: AsyncSession, user_id: uuid.UUID, data: TransactionCreate
) -> Transaction:
    # Verify account belongs to user
    account_result = await session.execute(
        select(Account)
        .outerjoin(BankConnection)
        .where(
            Account.id == data.account_id,
            or_(
                Account.user_id == user_id,
                BankConnection.user_id == user_id,
            ),
        )
    )
    account = account_result.scalar_one_or_none()
    if not account:
        raise ValueError("Account not found")

    # Resolve currency: explicit value > account currency
    currency = data.currency or account.currency

    transaction = Transaction(
        user_id=user_id,
        account_id=data.account_id,
        category_id=data.category_id,  # use provided category if given
        payee_id=data.payee_id,
        description=data.description,
        amount=data.amount,
        currency=currency,
        date=data.date,
        type=data.type,
        source="manual",
        notes=data.notes,
    )
    apply_effective_date(transaction, account)
    session.add(transaction)
    await session.flush()  # get ID without committing

    # Apply rules only if no explicit category provided
    if not data.category_id:
        await apply_rules_to_transaction(session, user_id, transaction)

    # Stamp primary currency amount (manual override or auto)
    if data.amount_primary is not None or data.fx_rate_used is not None:
        _apply_fx_override(transaction, data.amount, data.amount_primary, data.fx_rate_used)
    else:
        await stamp_primary_amount(session, user_id, transaction)

    await session.commit()
    await session.refresh(transaction, ["category"])
    return transaction


async def create_transfer(
    session: AsyncSession, user_id: uuid.UUID, data: TransferCreate
) -> tuple[Transaction, Transaction]:
    if data.from_account_id == data.to_account_id:
        raise ValueError("Cannot transfer to the same account")

    # Verify both accounts belong to user
    from_result = await session.execute(
        select(Account)
        .outerjoin(BankConnection)
        .where(
            Account.id == data.from_account_id,
            or_(Account.user_id == user_id, BankConnection.user_id == user_id),
        )
    )
    from_account = from_result.scalar_one_or_none()
    if not from_account:
        raise ValueError("Source account not found")

    to_result = await session.execute(
        select(Account)
        .outerjoin(BankConnection)
        .where(
            Account.id == data.to_account_id,
            or_(Account.user_id == user_id, BankConnection.user_id == user_id),
        )
    )
    to_account = to_result.scalar_one_or_none()
    if not to_account:
        raise ValueError("Destination account not found")

    transfer_pair_id = uuid.uuid4()
    from decimal import Decimal

    # Debit transaction (from account)
    debit_tx = Transaction(
        user_id=user_id,
        account_id=data.from_account_id,
        description=data.description,
        amount=data.amount,
        currency=from_account.currency,
        date=data.date,
        type="debit",
        source="transfer",
        notes=data.notes,
        transfer_pair_id=transfer_pair_id,
    )
    apply_effective_date(debit_tx, from_account)
    session.add(debit_tx)

    # Credit transaction (to account) — convert if cross-currency
    if from_account.currency != to_account.currency:
        if data.fx_rate is not None:
            from decimal import ROUND_HALF_UP
            credit_amount = (Decimal(str(data.amount)) * Decimal(str(data.fx_rate))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            converted_amount, _ = await fx_convert(
                session, Decimal(str(data.amount)), from_account.currency, to_account.currency, data.date
            )
            credit_amount = converted_amount
    else:
        credit_amount = data.amount

    credit_tx = Transaction(
        user_id=user_id,
        account_id=data.to_account_id,
        description=data.description,
        amount=credit_amount,
        currency=to_account.currency,
        date=data.date,
        type="credit",
        source="transfer",
        notes=data.notes,
        transfer_pair_id=transfer_pair_id,
    )
    apply_effective_date(credit_tx, to_account)
    session.add(credit_tx)
    await session.flush()

    # Stamp primary amounts on both
    await stamp_primary_amount(session, user_id, debit_tx)
    await stamp_primary_amount(session, user_id, credit_tx)

    # For cross-currency transfers, both sides should show the same primary amount
    if from_account.currency != to_account.currency and debit_tx.amount_primary is not None:
        credit_tx.amount_primary = debit_tx.amount_primary
        if credit_tx.amount and Decimal(str(credit_tx.amount)):
            credit_tx.fx_rate_used = debit_tx.amount_primary / Decimal(str(credit_tx.amount))

    await session.commit()
    await session.refresh(debit_tx, ["category"])
    await session.refresh(credit_tx, ["category"])
    return debit_tx, credit_tx


async def get_transfer_candidates(
    session: AsyncSession,
    user_id: uuid.UUID,
    transaction_id: uuid.UUID,
    limit: int = 10,
    window_days: int = 30,
) -> list[Transaction]:
    """Return ranked transfer-pair candidates for the given anchor transaction.

    Filters: different account, opposing type, not already linked, within
    ``window_days`` of the anchor's date. Ranked by date proximity, then
    primary-currency amount closeness (so cross-currency pairs match well).
    """
    from datetime import timedelta
    from decimal import Decimal

    anchor = await get_transaction(session, transaction_id, user_id)
    if not anchor:
        return []
    if anchor.transfer_pair_id is not None:
        return []

    opposing_type = "credit" if anchor.type == "debit" else "debit"
    from_date = anchor.date - timedelta(days=window_days)
    to_date = anchor.date + timedelta(days=window_days)

    result = await session.execute(
        select(Transaction)
        .outerjoin(Account)
        .outerjoin(BankConnection)
        .where(
            or_(
                Transaction.user_id == user_id,
                BankConnection.user_id == user_id,
            ),
            Transaction.id != anchor.id,
            Transaction.account_id != anchor.account_id,
            Transaction.type == opposing_type,
            Transaction.transfer_pair_id.is_(None),
            Transaction.source != "opening_balance",
            Transaction.date >= from_date,
            Transaction.date <= to_date,
        )
        .options(
            selectinload(Transaction.category),
            selectinload(Transaction.account),
            selectinload(Transaction.payee_entity),
        )
    )
    candidates = list(result.scalars().all())

    anchor_amount_primary = (
        Decimal(str(anchor.amount_primary)) if anchor.amount_primary is not None else None
    )

    def score(tx: Transaction) -> tuple[int, Decimal]:
        date_diff = abs((tx.date - anchor.date).days)
        if anchor_amount_primary is not None and tx.amount_primary is not None:
            amount_diff = abs(
                Decimal(str(tx.amount_primary)).copy_abs()
                - anchor_amount_primary.copy_abs()
            )
        else:
            amount_diff = abs(
                Decimal(str(tx.amount)).copy_abs()
                - Decimal(str(anchor.amount)).copy_abs()
            )
        return (date_diff, amount_diff)

    candidates.sort(key=score)
    candidates = candidates[:limit]

    # Hydrate fields the schema needs
    if candidates:
        tx_ids = [tx.id for tx in candidates]
        count_rows = await session.execute(
            select(
                TransactionAttachment.transaction_id,
                func.count(TransactionAttachment.id),
            )
            .where(TransactionAttachment.transaction_id.in_(tx_ids))
            .group_by(TransactionAttachment.transaction_id)
        )
        counts = dict(count_rows.all())
        for tx in candidates:
            tx.attachment_count = counts.get(tx.id, 0)
            tx.payee_name = tx.payee_entity.name if tx.payee_entity else None

    return candidates


async def link_existing_as_transfer(
    session: AsyncSession, user_id: uuid.UUID, transaction_ids: list[uuid.UUID]
) -> tuple[Transaction, Transaction]:
    """Link two existing transactions as a transfer pair.

    Permissive by design: amounts don't have to match. Validation enforces
    ownership, opposing types, different accounts, and that neither side is
    already part of an existing transfer.
    """
    if len(transaction_ids) != 2:
        raise ValueError("Exactly two transactions are required")
    if transaction_ids[0] == transaction_ids[1]:
        raise ValueError("Cannot link a transaction to itself")

    result = await session.execute(
        select(Transaction)
        .outerjoin(Account)
        .outerjoin(BankConnection)
        .where(
            Transaction.id.in_(transaction_ids),
            or_(
                Transaction.user_id == user_id,
                BankConnection.user_id == user_id,
            ),
        )
    )
    txns = list(result.scalars().all())
    if len(txns) != 2:
        raise ValueError("Transaction not found")

    for tx in txns:
        if tx.transfer_pair_id is not None:
            raise ValueError("Transaction is already part of a transfer")

    if txns[0].account_id == txns[1].account_id:
        raise ValueError("Transactions must be in different accounts")

    types = {tx.type for tx in txns}
    if types != {"debit", "credit"}:
        raise ValueError("Transactions must be one debit and one credit")

    transfer_pair_id = uuid.uuid4()
    for tx in txns:
        tx.transfer_pair_id = transfer_pair_id
        tx.category_id = None  # transfers are excluded from category reports

    await session.commit()
    for tx in txns:
        await session.refresh(tx, ["category"])

    debit_tx = next(tx for tx in txns if tx.type == "debit")
    credit_tx = next(tx for tx in txns if tx.type == "credit")
    return debit_tx, credit_tx


async def _resync_bill_link_from_override(
    session: AsyncSession, transaction: Transaction, account: Optional[Account]
) -> None:
    """Re-link transaction.bill_id when the manual effective_bill_date changes.

    - Override SET to a date matching an existing bill's due_date → link to it.
    - Override SET to a date with no matching bill → keep bill_id null (the
      override still sets effective_date directly).
    - Override CLEARED → fall back to whatever Pluggy originally tagged via
      `creditCardMetadata.billId` in raw_data, if recoverable; else null.
    """
    from app.models.credit_card_bill import CreditCardBill  # local: avoid circular
    if account is None or account.type != "credit_card":
        return
    override = transaction.effective_bill_date
    if override is not None:
        bill = (
            await session.execute(
                select(CreditCardBill).where(
                    CreditCardBill.account_id == account.id,
                    CreditCardBill.due_date == override,
                )
            )
        ).scalar_one_or_none()
        transaction.bill_id = bill.id if bill is not None else None
        return
    # Override cleared: try to recover the original Pluggy linkage.
    raw_bill_id = None
    if isinstance(transaction.raw_data, dict):
        meta = transaction.raw_data.get("creditCardMetadata") or {}
        raw_bill_id = meta.get("billId")
    if raw_bill_id:
        bill = (
            await session.execute(
                select(CreditCardBill).where(
                    CreditCardBill.account_id == account.id,
                    CreditCardBill.external_id == str(raw_bill_id),
                )
            )
        ).scalar_one_or_none()
        transaction.bill_id = bill.id if bill is not None else None
    else:
        transaction.bill_id = None


async def update_transaction(
    session: AsyncSession, transaction_id: uuid.UUID, user_id: uuid.UUID, data: TransactionUpdate
) -> Optional[Transaction]:
    transaction = await get_transaction(session, transaction_id, user_id)
    if not transaction:
        return None

    update_data = data.model_dump(exclude_unset=True)

    # Verify the new account belongs to the user before touching the row.
    # When changing the account on one side of a transfer pair, refuse to
    # collide with the paired transaction's account (a transfer must have two
    # distinct accounts).
    new_account_id = update_data.get("account_id")
    if new_account_id is not None and new_account_id != transaction.account_id:
        account_result = await session.execute(
            select(Account)
            .outerjoin(BankConnection)
            .where(
                Account.id == new_account_id,
                or_(
                    Account.user_id == user_id,
                    BankConnection.user_id == user_id,
                ),
            )
        )
        if account_result.scalar_one_or_none() is None:
            raise ValueError("Account not found")

        if transaction.transfer_pair_id:
            paired_result = await session.execute(
                select(Transaction).where(
                    Transaction.transfer_pair_id == transaction.transfer_pair_id,
                    Transaction.id != transaction.id,
                )
            )
            paired_tx = paired_result.scalar_one_or_none()
            if paired_tx and paired_tx.account_id == new_account_id:
                raise ValueError("Cannot move transfer to the same account as its paired transaction")

    # Pop FX override fields before generic setattr loop
    override_amount_primary = update_data.pop("amount_primary", None)
    override_fx_rate = update_data.pop("fx_rate_used", None)
    has_fx_override = override_amount_primary is not None or override_fx_rate is not None

    restamp_fields = {"amount", "currency", "date"}
    needs_restamp = bool(restamp_fields & update_data.keys())

    for key, value in update_data.items():
        setattr(transaction, key, value)

    if has_fx_override:
        _apply_fx_override(
            transaction,
            transaction.amount,
            override_amount_primary,
            override_fx_rate,
        )
    elif needs_restamp:
        await stamp_primary_amount(session, user_id, transaction)

    # Refresh effective_date when the purchase date, account, or the manual
    # bill-cycle override changed. Also re-link bill_id when the override
    # changed so the tx moves into the right cycle (issue #92 manual override).
    if "date" in update_data or "account_id" in update_data or "effective_bill_date" in update_data:
        account_for_tx = await session.get(Account, transaction.account_id)
        if "effective_bill_date" in update_data:
            await _resync_bill_link_from_override(session, transaction, account_for_tx)
        apply_effective_date(transaction, account_for_tx)

    # Cascade changes to paired transfer transaction
    cascade_fields = {"amount", "date", "description", "notes"}
    if transaction.transfer_pair_id and (cascade_fields & update_data.keys()):
        paired = await session.execute(
            select(Transaction).where(
                Transaction.transfer_pair_id == transaction.transfer_pair_id,
                Transaction.id != transaction.id,
            )
        )
        paired_tx = paired.scalar_one_or_none()
        if paired_tx:
            for key in cascade_fields & update_data.keys():
                if key == "amount" and paired_tx.currency != transaction.currency:
                    from decimal import Decimal
                    converted, _ = await fx_convert(
                        session, Decimal(str(transaction.amount)),
                        transaction.currency, paired_tx.currency, transaction.date,
                    )
                    paired_tx.amount = converted
                elif key != "amount":
                    setattr(paired_tx, key, update_data[key])
                else:
                    paired_tx.amount = update_data[key]
            await stamp_primary_amount(session, user_id, paired_tx)
            if "date" in update_data:
                paired_account = await session.get(Account, paired_tx.account_id)
                apply_effective_date(paired_tx, paired_account)

    await session.commit()
    await session.refresh(transaction)
    return transaction


async def bulk_update_category(
    session: AsyncSession,
    user_id: uuid.UUID,
    transaction_ids: list[uuid.UUID],
    category_id: Optional[uuid.UUID] = None,
) -> int:
    result = await session.execute(
        update(Transaction)
        .where(
            Transaction.id.in_(transaction_ids),
            Transaction.user_id == user_id,
        )
        .values(category_id=category_id)
    )
    await session.commit()
    return result.rowcount


_TAG_CHAR_CLASS = r"[\wÀ-ž-]"


def _normalize_tag(tag: str) -> str:
    """Return the tag in canonical `#foo` form."""
    return tag if tag.startswith("#") else f"#{tag}"


def _parse_hashtags(notes: Optional[str]) -> list[str]:
    if not notes:
        return []
    return re.findall(rf"#{_TAG_CHAR_CLASS}+", notes)


async def bulk_add_tags(
    session: AsyncSession,
    user_id: uuid.UUID,
    transaction_ids: list[uuid.UUID],
    tags: list[str],
) -> int:
    """Append the given tags to each transaction's `notes`, skipping tags
    that are already present. Returns the number of rows modified (issue #88)."""
    if not transaction_ids or not tags:
        return 0

    normalized_tags = [_normalize_tag(t.strip()) for t in tags if t and t.strip()]
    if not normalized_tags:
        return 0

    result = await session.execute(
        select(Transaction).where(
            Transaction.id.in_(transaction_ids),
            Transaction.user_id == user_id,
        )
    )
    touched = 0
    for tx in result.scalars().all():
        existing = set(_parse_hashtags(tx.notes))
        to_add = [t for t in normalized_tags if t not in existing]
        if not to_add:
            continue
        new_notes = (tx.notes.rstrip() + " " if tx.notes else "") + " ".join(to_add)
        tx.notes = new_notes.strip()
        touched += 1

    await session.commit()
    return touched


async def bulk_remove_tags(
    session: AsyncSession,
    user_id: uuid.UUID,
    transaction_ids: list[uuid.UUID],
    tags: list[str],
) -> int:
    if not transaction_ids or not tags:
        return 0

    normalized_tags = [_normalize_tag(t.strip()) for t in tags if t and t.strip()]
    if not normalized_tags:
        return 0

    result = await session.execute(
        select(Transaction).where(
            Transaction.id.in_(transaction_ids),
            Transaction.user_id == user_id,
        )
    )
    touched = 0
    for tx in result.scalars().all():
        if not tx.notes:
            continue
        original = tx.notes
        updated = original
        for tag in normalized_tags:
            pattern = (
                r"(?:(?<=^)|(?<=[^\wÀ-ž-]))"
                + re.escape(tag)
                + r"(?=$|[^\wÀ-ž-])"
            )
            updated = re.sub(pattern, "", updated)
        # Collapse consecutive whitespace left behind by removed tags.
        updated = re.sub(r"\s{2,}", " ", updated).strip()
        if updated != original:
            tx.notes = updated or None
            touched += 1

    await session.commit()
    return touched


async def delete_transaction(
    session: AsyncSession, transaction_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    transaction = await get_transaction(session, transaction_id, user_id)
    if not transaction:
        return False

    # Clean up attachment files from storage before ORM cascade deletes DB records
    from app.services.attachment_service import cleanup_attachment_files

    tx_ids_to_cleanup = [transaction_id]

    # Cascade delete paired transfer transaction
    paired_tx = None
    if transaction.transfer_pair_id:
        paired_result = await session.execute(
            select(Transaction).where(
                Transaction.transfer_pair_id == transaction.transfer_pair_id,
                Transaction.id != transaction.id,
            )
        )
        paired_tx = paired_result.scalar_one_or_none()
        if paired_tx:
            tx_ids_to_cleanup.append(paired_tx.id)

    await cleanup_attachment_files(session, tx_ids_to_cleanup)

    if paired_tx:
        await session.delete(paired_tx)
    await session.delete(transaction)
    await session.commit()
    return True
