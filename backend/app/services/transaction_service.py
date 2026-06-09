import re
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func, or_, not_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.transaction import Transaction
from app.models.transaction_attachment import TransactionAttachment
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.category import Category
from app.models.group import Group, GroupMember
from app.models.payee import Payee
from app.schemas.transaction import TransactionCreate, TransactionUpdate, TransferCreate
from app.schemas.transaction_split import TransactionSplitInput, TransactionSplitsInput
from app.services import split_service
from app.services.credit_card_service import apply_effective_date
from app.services.rule_service import apply_rules_to_transaction
from app.services.fx_rate_service import stamp_primary_amount, convert as fx_convert
from app.services._query_filters import counts_as_pnl


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
    workspace_id: uuid.UUID,
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
    group_id: Optional[uuid.UUID] = None,
    unbilled_only: bool = False,
    sort_by: Optional[str] = None,
    sort_dir: str = "desc",
    transaction_ids: Optional[list[uuid.UUID]] = None,
    currency: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    account_types: Optional[list[str]] = None,
    include_summary: bool = False,
) -> tuple[list[Transaction], int, Optional[dict]]:
    """List transactions for a workspace.

    `workspace_id` scopes the tenant (which transactions are visible). `user_id`
    is the *viewer* — used for Splitwise projection (linked-member visibility,
    is-shared tagging) which is identity-based, not tenancy-based.
    """
    # In "accrual" mode, bucket/order by effective_date so list filters
    # line up with the cash-flow view used by the dashboard and reports.
    # When the user has set a manual cycle override (effective_bill_date)
    # we honor it FIRST regardless of accounting mode — that's the whole
    # point of the override (issue #92, LucasFidelis suggestion).
    date_col = func.coalesce(
        Transaction.effective_bill_date,
        Transaction.effective_date if accounting_mode == "accrual" else Transaction.date,
    )

    # Group-scope visibility: when the caller filters by a group they
    # have access to (owner or linked member), bypass the user-owns-it
    # check and return that group's transactions instead. Lets a linked
    # member view the owner's transactions for shared groups.
    use_group_scope = False
    if group_id is not None:
        from app.services.group_service import get_group_visible

        accessible = await get_group_visible(session, group_id, workspace_id, user_id)
        if accessible is None:
            return [], 0
        use_group_scope = True

    # CC bill-view date column: when the caller asks "what's in this bill?"
    # (bill_id passed, or in-progress cycle via unbilled_only), the answer
    # is bank-truth — the charges that fell in the cycle by purchase date —
    # independent of the user's cash/accrual reporting preference. Without
    # this carve-out, accrual mode would hide a 4/30 charge whose
    # effective_date points at the next bill's due date because the cycle
    # window [prev_close, this_close-1] doesn't contain the future
    # effective_date (issue #92, abdalanervoso's accrual case).
    bill_view_date_col = func.coalesce(
        Transaction.effective_bill_date,
        Transaction.date,
    )
    in_bill_view = bill_id is not None or unbilled_only
    filter_date_col = bill_view_date_col if in_bill_view else date_col
    # Base query: user's own transactions (manual or via account), or
    # group-scoped when `group_id` resolves to a visible group.
    base_query = (
        select(Transaction)
        .outerjoin(Account)
        .outerjoin(BankConnection)
        .outerjoin(Payee, Transaction.payee_id == Payee.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .options(
            selectinload(Transaction.category),
            selectinload(Transaction.account),
            selectinload(Transaction.payee_entity),
            selectinload(Transaction.splits),
        )
    )
    if transaction_ids:
        base_query = base_query.where(Transaction.id.in_(transaction_ids))
    if use_group_scope:
        from app.models.group import GroupMember
        from app.models.transaction_split import TransactionSplit

        member_ids_subq = select(GroupMember.id).where(GroupMember.group_id == group_id)
        tx_ids_subq = (
            select(TransactionSplit.transaction_id)
            .where(TransactionSplit.group_member_id.in_(member_ids_subq))
            .distinct()
        )
        base_query = base_query.where(Transaction.id.in_(tx_ids_subq))
    else:
        # Default scope: transactions in this workspace PLUS cross-workspace
        # shares the viewer participates in. The union surfaces the
        # viewer's "Concert Tickets · share $90" from another workspace
        # alongside their own expenses; account-balance integrity is
        # preserved because the parent tx's account still belongs to the
        # original owner. The shared-id subquery EXCLUDES rows already
        # in this workspace — otherwise self-membership in an
        # in-workspace group double-surfaces the owner's own
        # transactions when they switch to another workspace.
        from app.models.group import GroupMember
        from app.models.transaction_split import TransactionSplit

        # Exclude is_self memberships — those represent the viewer's
        # OWN self-member in groups they created, not invitations from
        # someone else's workspace. Without this exclusion, the owner's
        # own transactions get double-projected when they switch into
        # a different workspace.
        viewer_member_ids = select(GroupMember.id).where(
            GroupMember.linked_user_id == user_id,
            GroupMember.is_self.is_(False),
        )
        shared_tx_ids = (
            select(TransactionSplit.transaction_id)
            .join(Transaction, Transaction.id == TransactionSplit.transaction_id)
            .where(
                TransactionSplit.group_member_id.in_(viewer_member_ids),
                Transaction.workspace_id != workspace_id,
            )
            .distinct()
        )
        base_query = base_query.where(
            or_(
                Transaction.workspace_id == workspace_id,
                Transaction.id.in_(shared_tx_ids),
            )
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
    if currency:
        # Native-currency filter — match the column verbatim. Lets agents
        # answer "do I have any EUR transactions?" without text-searching
        # the description column.
        base_query = base_query.where(Transaction.currency == currency.upper())
    if min_amount is not None or max_amount is not None:
        # Amount filters operate on the primary-currency value when
        # available so cross-currency totals make sense; fall back to the
        # native amount on rows that haven't been stamped yet.
        amount_expr = func.coalesce(Transaction.amount_primary, Transaction.amount)
        if min_amount is not None:
            base_query = base_query.where(amount_expr >= min_amount)
        if max_amount is not None:
            base_query = base_query.where(amount_expr <= max_amount)
    if account_types:
        base_query = base_query.where(Account.type.in_(account_types))
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
        from app.models.credit_card_bill import CreditCardBill  # local — avoid cycle
        bill_predicates = [Transaction.bill_id == bill_id]
        if from_date or to_date:
            from sqlalchemy import and_ as _and, not_ as _not
            # Resolve the active bill's due_date once so we can trust
            # cycle-math classification when Pluggy hasn't tagged a tx yet.
            active_due_subq = (
                select(CreditCardBill.due_date)
                .where(CreditCardBill.id == bill_id)
                .scalar_subquery()
            )
            unlinked_clauses = [
                Transaction.bill_id.is_(None),
                # Sync-pending txs without a billId are normally deferred
                # (provider hasn't classified them) — but if our cycle-math
                # `apply_effective_date` already pre-classified them to THIS
                # bill's due_date, trust it and include them. That's the
                # in-progress case: pending charges the user can already see
                # in their bank app, classified by close-date math we
                # computed at sync time. Past closed bills aren't affected
                # because pending txs there have effective_date pointing
                # forward to a later bill (ingrid's case stays clean).
                # Issue #92, abdalanervoso's empty-May.
                #
                # Manual override (effective_bill_date) bypasses the
                # exclusion entirely: the user has hand-corrected the
                # bucketing and that signal beats both cycle-math
                # classification and sync-pending caution. Without this
                # carve-out, a pending tx whose override doesn't snap to
                # an existing bill's due_date (so bill_id stays null)
                # gets filtered out of every closed-bill view (issue #162).
                _not(_and(
                    Transaction.source == "sync",
                    Transaction.status == "pending",
                    Transaction.effective_bill_date.is_(None),
                    Transaction.effective_date != active_due_subq,
                )),
            ]
            if from_date:
                unlinked_clauses.append(filter_date_col >= from_date)
            if to_date:
                unlinked_clauses.append(filter_date_col <= to_date)
            bill_predicates.append(_and(*unlinked_clauses))
        base_query = base_query.where(or_(*bill_predicates))
    else:
        # Cycle-math fallback (no bill_id was passed). The opt-in
        # `unbilled_only` flag is for callers that need the in-progress
        # cycle on a CC account whose date window may overlap a closed
        # bill's range — they ask us to exclude already-billed txs so the
        # in-progress bar / list doesn't double-count them. The global
        # /transactions list and other generic callers leave it False.
        if unbilled_only:
            base_query = base_query.where(Transaction.bill_id.is_(None))
        # Forward-pointing override catch (issue #162): a manual override
        # pointing past this cycle's right edge (e.g., user wants the tx
        # on a future bill that doesn't exist yet) won't fit any closed
        # bill window either — past bills are by definition behind us.
        # Honor the user's explicit intent by including those orphans in
        # the in-progress cycle so the tx stays visible until a real bill
        # eventually anchors it. Limited to `unbilled_only` so the global
        # /transactions list isn't reshaped by the same rule.
        if unbilled_only and to_date is not None:
            from sqlalchemy import and_ as _and
            window_clauses = []
            if from_date:
                window_clauses.append(filter_date_col >= from_date)
            window_clauses.append(filter_date_col <= to_date)
            base_query = base_query.where(or_(
                _and(*window_clauses),
                _and(
                    Transaction.effective_bill_date.is_not(None),
                    Transaction.effective_bill_date > to_date,
                ),
            ))
        else:
            if from_date:
                base_query = base_query.where(filter_date_col >= from_date)
            if to_date:
                base_query = base_query.where(filter_date_col <= to_date)
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

    # Filtered summary (issue #185): income / expense / net across ALL
    # rows matching the active filters — not just the current page — so
    # the UI can show an accurate total even when results span pages.
    # Cross-currency rows are normalized to their primary-currency amount
    # when stamped (coalesce → native amount as fallback for unstamped
    # rows). Computed before pagination so it covers the whole result set.
    summary: Optional[dict] = None
    if include_summary:
        # Income / expense / net use the shared `counts_as_pnl()` definition
        # (issue #242) so the footer matches the dashboard & reports: paired
        # transfers, `treat_as_transfer` categories (transfers, investments,
        # custom) and ignored items are kept OUT of income/expense.
        pnl_filter = counts_as_pnl()
        pnl_subq = base_query.where(pnl_filter).subquery()
        amount_norm = func.coalesce(
            pnl_subq.c.amount_primary, pnl_subq.c.amount
        )
        summary_rows = await session.execute(
            select(
                pnl_subq.c.type,
                func.coalesce(func.sum(func.abs(amount_norm)), 0),
            ).group_by(pnl_subq.c.type)
        )
        income = Decimal("0")
        expense = Decimal("0")
        for row_type, row_total in summary_rows:
            if row_type == "credit":
                income = Decimal(str(row_total or 0))
            elif row_type == "debit":
                expense = Decimal(str(row_total or 0))

        # Excluded: the absolute total of everything filtered out of P/L for
        # the same rows — the complement of `counts_as_pnl()`. Surfaces
        # transfer-like movement (e.g. how much was moved/invested) without
        # distorting income/expense/net.
        excl_subq = base_query.where(not_(pnl_filter)).subquery()
        excl_amount_norm = func.coalesce(
            excl_subq.c.amount_primary, excl_subq.c.amount
        )
        excluded_total = await session.scalar(
            select(func.coalesce(func.sum(func.abs(excl_amount_norm)), 0))
        )
        excluded = Decimal(str(excluded_total or 0))

        summary = {
            "income": income,
            "expense": expense,
            "net": income - expense,
            "excluded": excluded,
        }

    # Apply ordering (and pagination unless skipped). Bill-view callers
    # order by purchase date so the in-cycle list matches the bank's own
    # statement ordering regardless of accounting mode.
    default_order_col = bill_view_date_col if in_bill_view else date_col
    sort_columns: dict[str, object] = {
        # `date` is the cycle/accrual-aware column the UI lists use so its
        # ordering matches the cash-flow & dashboard views.
        "date": default_order_col,
        # `transaction_date` orders strictly by Transaction.date (purchase
        # date), regardless of effective_bill_date. Useful for "what's my
        # most recent transaction?" where credit-card bill projections
        # would otherwise float old purchases to the top because their
        # bill due date is in the future.
        "transaction_date": Transaction.date,
        "amount": Transaction.amount,
        "description": Transaction.description,
        "payee": Payee.name,
        "category": Category.name,
        "account": Account.name,
        "type": Transaction.type,
        "status": Transaction.status,
        "created_at": Transaction.created_at,
    }
    chosen_col = sort_columns.get(sort_by) if sort_by else None
    if chosen_col is None:
        # Default: by date desc, with created_at as tiebreaker.
        query = base_query.order_by(default_order_col.desc(), Transaction.created_at.desc())
    else:
        direction = (chosen_col.asc() if sort_dir == "asc" else chosen_col.desc())
        # Always tie-break on date desc + created_at desc so equal values
        # stay in a sensible order (e.g. multiple txs with the same amount).
        query = base_query.order_by(direction, default_order_col.desc(), Transaction.created_at.desc())
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
            if not tx.is_ignored and tx.category and tx.category.is_ignored:
                tx.is_ignored = True
        # Tag shared rows with the viewer's share + the source group.
        # Owned rows stay as-is. We pre-compute the viewer's linked
        # member ids → group ids once, then look up each transaction's
        # split that targets one of those member ids.
        # Run for both default and group-scoped queries: when filtering
        # by `group_id`, a linked member sees the owner's transactions
        # and the frontend needs `is_shared` to lock them from edits.
        await _tag_shared_view(session, transactions, user_id)

    return transactions, total or 0, summary


async def _tag_shared_view(
    session: AsyncSession,
    transactions: list[Transaction],
    user_id: uuid.UUID,
) -> None:
    """Annotate transactions with split metadata for the viewer:

    - `is_shared`: true when the viewer doesn't own the parent but is a
      linked split member.
    - `viewer_share`: the viewer's share amount (only when shared).
    - `group_id`: the group the splits belong to. Set for both owner
      and linked-member views so the UI can show a group badge on
      either side.
    - `parent_owner_name`: friendly name of the parent owner; only
      meaningful for shared rows.

    Mutates the in-memory objects so Pydantic's from_attributes picks
    them up directly.
    """
    from app.models.group import GroupMember

    member_rows = await session.execute(
        select(GroupMember.id, GroupMember.group_id).where(
            GroupMember.linked_user_id == user_id,
            GroupMember.is_self.is_(False),
        )
    )
    member_to_group = {row.id: row.group_id for row in member_rows}

    # Map every split-member that appears in this batch → group, so we
    # can also tag owner-side transactions with their group. (The
    # viewer-linked map above only covers the viewer's own member ids.)
    all_split_member_ids: set[uuid.UUID] = set()
    for tx in transactions:
        for s in tx.splits or []:
            all_split_member_ids.add(s.group_member_id)
    split_member_to_group: dict[uuid.UUID, uuid.UUID] = {}
    if all_split_member_ids:
        rows = await session.execute(
            select(GroupMember.id, GroupMember.group_id).where(
                GroupMember.id.in_(all_split_member_ids)
            )
        )
        split_member_to_group = {row.id: row.group_id for row in rows}

    if not member_to_group and not split_member_to_group:
        for tx in transactions:
            tx.is_shared = False
            tx.viewer_share = None
            tx.group_id = None
            tx.parent_owner_name = None
        return

    # Per-group lookups: the `is_self` member represents the parent
    # owner / payer. We need this for ALL groups touching this batch,
    # not just the viewer-linked ones — owners need their own
    # self-member id to compute their share. Cached once per call.
    all_group_ids = set(member_to_group.values()) | set(split_member_to_group.values())
    self_member_rows = await session.execute(
        select(GroupMember.id, GroupMember.group_id, GroupMember.name).where(
            GroupMember.group_id.in_(all_group_ids),
            GroupMember.is_self.is_(True),
        )
    )
    self_member_id_by_group: dict[uuid.UUID, uuid.UUID] = {}
    owner_name_by_group: dict[uuid.UUID, str] = {}
    for row in self_member_rows:
        self_member_id_by_group[row.group_id] = row.id
        owner_name_by_group[row.group_id] = row.name

    for tx in transactions:
        if tx.user_id == user_id:
            # Owner of the parent — not "shared" but still tag the
            # group_id so the UI can show a group badge for owners.
            # Also populate viewer_share with the owner's *own* split
            # share when they participate (is_self member appears in the
            # splits): the UI surfaces "your share: $X" alongside the
            # full amount that hit the account.
            tx.is_shared = False
            tx.parent_owner_name = None
            owner_group_id: Optional[uuid.UUID] = None
            for s in tx.splits or []:
                gid = split_member_to_group.get(s.group_member_id)
                if gid is not None:
                    owner_group_id = gid
                    break
            tx.group_id = owner_group_id
            self_mid = (
                self_member_id_by_group.get(owner_group_id)
                if owner_group_id is not None
                else None
            )
            owner_split = (
                next(
                    (s for s in tx.splits or [] if s.group_member_id == self_mid),
                    None,
                )
                if self_mid is not None
                else None
            )
            tx.viewer_share = owner_split.share_amount if owner_split else None
            continue
        # The viewer doesn't own this; find their split share.
        match = next(
            (s for s in (tx.splits or []) if s.group_member_id in member_to_group),
            None,
        )
        if match is None:
            tx.is_shared = False
            tx.viewer_share = None
            tx.group_id = None
            tx.parent_owner_name = None
        else:
            tx.is_shared = True
            tx.viewer_share = match.share_amount
            tx.group_id = member_to_group[match.group_member_id]
            tx.parent_owner_name = owner_name_by_group.get(tx.group_id)
            # Hide attachment count on shared rows — Bob can see the
            # paperclip but the API would 403 the actual download
            # (attachment auth is owner-only). Avoid the dead-end UX
            # by not advertising files he can't open.
            tx.attachment_count = 0


async def get_transaction(
    session: AsyncSession,
    transaction_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> Optional[Transaction]:
    """Fetch a single transaction by id, scoped to the workspace."""
    result = await session.execute(
        select(Transaction)
        .where(
            Transaction.id == transaction_id,
            Transaction.workspace_id == workspace_id,
        )
        .options(
            selectinload(Transaction.category),
            selectinload(Transaction.payee_entity),
            selectinload(Transaction.splits),
        )
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
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: TransactionCreate,
) -> Transaction:
    # Verify account belongs to the workspace
    account_result = await session.execute(
        select(Account)
        .outerjoin(BankConnection)
        .where(
            Account.id == data.account_id,
            or_(
                Account.workspace_id == workspace_id,
                BankConnection.workspace_id == workspace_id,
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
        workspace_id=workspace_id,
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

    if data.splits is not None:
        await split_service.replace_splits(session, transaction, data.splits, user_id)

    await session.commit()
    await session.refresh(transaction, ["category", "splits"])
    return transaction


async def create_transfer(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: TransferCreate,
) -> tuple[Transaction, Transaction]:
    if data.from_account_id == data.to_account_id:
        raise ValueError("Cannot transfer to the same account")

    # Verify both accounts belong to the workspace
    from_result = await session.execute(
        select(Account)
        .outerjoin(BankConnection)
        .where(
            Account.id == data.from_account_id,
            or_(
                Account.workspace_id == workspace_id,
                BankConnection.workspace_id == workspace_id,
            ),
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
            or_(
                Account.workspace_id == workspace_id,
                BankConnection.workspace_id == workspace_id,
            ),
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
        workspace_id=workspace_id,
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
        workspace_id=workspace_id,
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
    workspace_id: uuid.UUID,
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

    anchor = await get_transaction(session, transaction_id, workspace_id)
    if not anchor:
        return []
    if anchor.transfer_pair_id is not None:
        return []

    opposing_type = "credit" if anchor.type == "debit" else "debit"
    from_date = anchor.date - timedelta(days=window_days)
    to_date = anchor.date + timedelta(days=window_days)

    result = await session.execute(
        select(Transaction)
        .where(
            Transaction.workspace_id == workspace_id,
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
            selectinload(Transaction.splits),
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
    session: AsyncSession,
    workspace_id: uuid.UUID,
    transaction_ids: list[uuid.UUID],
) -> tuple[Transaction, Transaction]:
    """Link two existing transactions as a transfer pair.

    Permissive by design: amounts don't have to match. Validation enforces
    workspace ownership, opposing types, different accounts, and that
    neither side is already part of an existing transfer.
    """
    if len(transaction_ids) != 2:
        raise ValueError("Exactly two transactions are required")
    if transaction_ids[0] == transaction_ids[1]:
        raise ValueError("Cannot link a transaction to itself")

    result = await session.execute(
        select(Transaction)
        .where(
            Transaction.id.in_(transaction_ids),
            Transaction.workspace_id == workspace_id,
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


async def create_transfer_counterpart(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    transaction_id: uuid.UUID,
    to_account_id: uuid.UUID,
) -> tuple[Transaction, Transaction]:
    """Mark an existing transaction as a transfer by auto-creating its
    counterpart in another account.

    Used when the counterpart account is manual (not bank-synced), so no
    matching transaction exists to link against. The counterpart mirrors the
    anchor's date / description / notes with the opposite type, converting the
    amount when the destination account uses a different currency.
    """
    from decimal import Decimal

    anchor = await get_transaction(session, transaction_id, workspace_id)
    if not anchor:
        raise ValueError("Transaction not found")
    if anchor.transfer_pair_id is not None:
        raise ValueError("Transaction is already part of a transfer")
    if anchor.account_id == to_account_id:
        raise ValueError("Counterpart must be in a different account")

    to_result = await session.execute(
        select(Account)
        .outerjoin(BankConnection)
        .where(
            Account.id == to_account_id,
            or_(
                Account.workspace_id == workspace_id,
                BankConnection.workspace_id == workspace_id,
            ),
        )
    )
    to_account = to_result.scalar_one_or_none()
    if not to_account:
        raise ValueError("Destination account not found")

    opposing_type = "credit" if anchor.type == "debit" else "debit"

    # Convert the amount when the destination account uses another currency.
    if anchor.currency != to_account.currency:
        counterpart_amount, _ = await fx_convert(
            session, Decimal(str(anchor.amount)), anchor.currency, to_account.currency, anchor.date
        )
    else:
        counterpart_amount = anchor.amount

    transfer_pair_id = uuid.uuid4()

    counterpart_tx = Transaction(
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=to_account_id,
        description=anchor.description,
        amount=counterpart_amount,
        currency=to_account.currency,
        date=anchor.date,
        type=opposing_type,
        source="transfer",
        notes=anchor.notes,
        transfer_pair_id=transfer_pair_id,
    )
    apply_effective_date(counterpart_tx, to_account)
    session.add(counterpart_tx)

    # Link the anchor into the pair; transfers are excluded from category reports.
    anchor.transfer_pair_id = transfer_pair_id
    anchor.category_id = None

    await session.flush()
    await stamp_primary_amount(session, user_id, counterpart_tx)

    # Cross-currency: keep both sides on the same primary amount.
    if anchor.currency != to_account.currency and anchor.amount_primary is not None:
        counterpart_tx.amount_primary = anchor.amount_primary
        if counterpart_tx.amount and Decimal(str(counterpart_tx.amount)):
            counterpart_tx.fx_rate_used = Decimal(str(anchor.amount_primary)) / Decimal(
                str(counterpart_tx.amount)
            )

    await session.commit()
    await session.refresh(anchor, ["category"])
    await session.refresh(counterpart_tx, ["category"])

    debit_tx = anchor if anchor.type == "debit" else counterpart_tx
    credit_tx = counterpart_tx if anchor.type == "debit" else anchor
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
    session: AsyncSession,
    transaction_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: TransactionUpdate,
) -> Optional[Transaction]:
    transaction = await get_transaction(session, transaction_id, workspace_id)
    if not transaction:
        return None

    update_data = data.model_dump(exclude_unset=True)
    apply_to_transfer_pair = update_data.pop("apply_to_transfer_pair", False)

    # Splits are processed separately after column updates land so the
    # service can validate against the new amount.
    splits_payload = data.splits if "splits" in update_data else None
    update_data.pop("splits", None)

    # Verify the new account belongs to the workspace before touching the
    # row. When changing the account on one side of a transfer pair,
    # refuse to collide with the paired transaction's account (a transfer
    # must have two distinct accounts).
    new_account_id = update_data.get("account_id")
    if new_account_id is not None and new_account_id != transaction.account_id:
        account_result = await session.execute(
            select(Account)
            .outerjoin(BankConnection)
            .where(
                Account.id == new_account_id,
                or_(
                    Account.workspace_id == workspace_id,
                    BankConnection.workspace_id == workspace_id,
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
    should_cascade_category = apply_to_transfer_pair and "category_id" in update_data
    if transaction.transfer_pair_id and ((cascade_fields & update_data.keys()) or should_cascade_category):
        paired = await session.execute(
            select(Transaction).where(
                Transaction.transfer_pair_id == transaction.transfer_pair_id,
                Transaction.id != transaction.id,
            )
        )
        paired_tx = paired.scalar_one_or_none()
        if paired_tx:
            if should_cascade_category:
                paired_tx.category_id = transaction.category_id
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

    if splits_payload is not None:
        await split_service.replace_splits(session, transaction, splits_payload, user_id)

    await session.commit()
    await session.refresh(transaction, ["splits"])
    return transaction


async def bulk_update_category(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    transaction_ids: list[uuid.UUID],
    category_id: Optional[uuid.UUID] = None,
) -> int:
    result = await session.execute(
        update(Transaction)
        .where(
            Transaction.id.in_(transaction_ids),
            Transaction.workspace_id == workspace_id,
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
    workspace_id: uuid.UUID,
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
            Transaction.workspace_id == workspace_id,
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
    workspace_id: uuid.UUID,
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
            Transaction.workspace_id == workspace_id,
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


async def bulk_add_to_group(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    transaction_ids: list[uuid.UUID],
    group_id: uuid.UUID,
    share_type: str = "equal",
    member_splits: Optional[list[TransactionSplitInput]] = None,
) -> dict[str, int]:
    """Apply the same group-split configuration to every selected transaction.

    Supports `share_type` of "equal" or "percent" only — exact amounts
    can't generalize across transactions of different totals.

    Conservative semantics (issue #156): transactions that are transfers
    or already have splits are skipped — never overwritten — so the
    operation can't destroy prior splitting work.
    """
    if share_type not in ("equal", "percent"):
        raise ValueError(
            "Bulk add-to-group only supports share_type 'equal' or 'percent' — "
            "use the per-transaction dialog for exact amounts"
        )

    if not transaction_ids:
        return {"updated": 0, "skipped": 0}

    # The caller may own the group OR be a linked member of it.
    linked_group_ids = (
        select(GroupMember.group_id)
        .where(GroupMember.linked_user_id == user_id)
        .distinct()
    )
    group_result = await session.execute(
        select(Group).where(
            Group.id == group_id,
            or_(Group.user_id == user_id, Group.id.in_(linked_group_ids)),
        )
    )
    group = group_result.scalar_one_or_none()
    if group is None:
        raise ValueError("Group not found")

    members_result = await session.execute(
        select(GroupMember).where(GroupMember.group_id == group_id)
    )
    members = members_result.scalars().all()
    if not members:
        raise ValueError("Group has no members")

    valid_member_ids = {m.id for m in members}

    # If the caller didn't specify, default to all members (the previous
    # behavior). Otherwise honor the subset they chose.
    if not member_splits:
        chosen = [TransactionSplitInput(group_member_id=m.id) for m in members]
    else:
        chosen = list(member_splits)

    if not chosen:
        raise ValueError("At least one member must be selected")

    for entry in chosen:
        if entry.group_member_id not in valid_member_ids:
            raise ValueError("One or more split members not found")

    payload = TransactionSplitsInput(share_type=share_type, splits=chosen)

    txs_result = await session.execute(
        select(Transaction)
        .where(
            Transaction.id.in_(transaction_ids),
            Transaction.workspace_id == workspace_id,
        )
        .options(selectinload(Transaction.splits))
    )
    txs = txs_result.scalars().all()

    updated = 0
    skipped = 0
    for tx in txs:
        if tx.transfer_pair_id is not None or tx.splits:
            skipped += 1
            continue
        await split_service.replace_splits(session, tx, payload, user_id)
        updated += 1

    # Account for ids that didn't match (wrong user, deleted, etc.)
    skipped += len(set(transaction_ids)) - len(txs)

    await session.commit()
    return {"updated": updated, "skipped": skipped}


async def toggle_ignore_transaction(
    session: AsyncSession,
    transaction_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> Optional[Transaction]:
    """Flip the is_ignored flag on a transaction. Acts immediately (no
    other field is touched) so the edit dialog can offer ignore as a
    one-click action alongside delete, instead of bundling it into the
    form's Salvar flow."""
    transaction = await get_transaction(session, transaction_id, workspace_id)
    if not transaction:
        return None
    transaction.is_ignored = not transaction.is_ignored
    await session.commit()
    await session.refresh(transaction)
    return transaction


async def delete_transaction(
    session: AsyncSession,
    transaction_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> bool:
    transaction = await get_transaction(session, transaction_id, workspace_id)
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
