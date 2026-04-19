import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models.bank_connection import BankConnection
from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.user import User
from app.providers import get_provider
from app.services.account_service import sync_opening_balance_for_connected_account
from app.services.credit_card_service import apply_effective_date
from app.services.rule_service import apply_rules_to_transaction
from app.services.transfer_detection_service import detect_transfer_pairs
from app.services.fx_rate_service import stamp_primary_amount
from app.services.payee_service import get_or_create_payee

settings = get_settings()

PLUGGY_CATEGORY_MAP = {
    "Eating out": "Alimentação",
    "Restaurants": "Alimentação",
    "Food": "Alimentação",
    "Groceries": "Mercado",
    "Supermarkets": "Mercado",
    "Pharmacy": "Saúde",
    "Health": "Saúde",
    "Taxi and ride-hailing": "Transporte",
    "Transport": "Transporte",
    "Gas": "Transporte",
    "Travel": "Transporte",
    "Housing": "Moradia",
    "Rent": "Moradia",
    "Utilities": "Moradia",
    "Entertainment": "Lazer",
    "Leisure": "Lazer",
    "Education": "Educação",
    "Subscriptions": "Assinaturas",
    "Online services": "Assinaturas",
    "Transfer": "Transferências",
    "Transfers": "Transferências",
    "Wire transfers": "Transferências",
}


async def _match_pluggy_category(
    session: AsyncSession, user_id: uuid.UUID, pluggy_category: Optional[str]
) -> Optional[uuid.UUID]:
    if not pluggy_category:
        return None
    # Try exact match first, then prefix before " - " (e.g. "Transfer - PIX" → "Transfer")
    app_name = PLUGGY_CATEGORY_MAP.get(pluggy_category)
    if not app_name and " - " in pluggy_category:
        app_name = PLUGGY_CATEGORY_MAP.get(pluggy_category.split(" - ")[0])
    if not app_name:
        return None
    result = await session.execute(
        select(Category.id).where(Category.user_id == user_id, Category.name == app_name)
    )
    return result.scalar_one_or_none()


async def get_connections(session: AsyncSession, user_id: uuid.UUID) -> list[BankConnection]:
    result = await session.execute(
        select(BankConnection)
        .where(BankConnection.user_id == user_id)
        .options(selectinload(BankConnection.accounts))
        .order_by(BankConnection.created_at.desc())
    )
    return list(result.scalars().all())


async def get_connection(
    session: AsyncSession, connection_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[BankConnection]:
    result = await session.execute(
        select(BankConnection)
        .where(BankConnection.id == connection_id, BankConnection.user_id == user_id)
        .options(selectinload(BankConnection.accounts))
    )
    return result.scalar_one_or_none()


def get_oauth_url(provider_name: str, user_id: uuid.UUID) -> str:
    provider = get_provider(provider_name)
    state = str(user_id)
    return provider.get_oauth_url(settings.pluggy_oauth_redirect_uri, state)


async def create_connect_token(
    provider_name: str, user_id: uuid.UUID, item_id: str | None = None
) -> dict:
    provider = get_provider(provider_name)
    token_data = await provider.create_connect_token(str(user_id), item_id=item_id)
    return {"access_token": token_data.access_token}


async def update_connection_settings(
    session: AsyncSession,
    connection_id: uuid.UUID,
    user_id: uuid.UUID,
    settings_update: dict,
) -> Optional[BankConnection]:
    connection = await get_connection(session, connection_id, user_id)
    if not connection:
        return None

    current = dict(connection.settings or {})
    for key, value in settings_update.items():
        if value is not None:
            current[key] = value
    connection.settings = current

    await session.commit()
    await session.refresh(connection)
    return connection


async def handle_oauth_callback(
    session: AsyncSession, user_id: uuid.UUID, code: str, provider_name: str
) -> BankConnection:
    provider = get_provider(provider_name)
    connection_data = await provider.handle_oauth_callback(code)

    connection = BankConnection(
        user_id=user_id,
        provider=provider_name,
        external_id=connection_data.external_id,
        institution_name=connection_data.institution_name,
        credentials=connection_data.credentials,
        status="active",
    )
    session.add(connection)
    await session.flush()

    user = await session.get(User, user_id)
    user_currency = user.primary_currency if user else get_settings().default_currency
    new_tx_ids: list[uuid.UUID] = []

    for acc_data in connection_data.accounts:
        is_cc = acc_data.type == "credit_card"
        account = Account(
            user_id=user_id,
            connection_id=connection.id,
            external_id=acc_data.external_id,
            name=acc_data.name,
            type=acc_data.type,
            balance=acc_data.balance,
            currency=acc_data.currency,
            credit_limit=acc_data.credit_limit if is_cc else None,
            statement_close_day=acc_data.statement_close_day if is_cc else None,
            payment_due_day=acc_data.payment_due_day if is_cc else None,
            minimum_payment=acc_data.minimum_payment if is_cc else None,
            card_brand=acc_data.card_brand if is_cc else None,
            card_level=acc_data.card_level if is_cc else None,
        )
        session.add(account)
        await session.flush()

        # Fetch initial transactions (since=None fetches all available history)
        transactions_data = await provider.get_transactions(
            connection_data.credentials, acc_data.external_id, None
        )
        for txn_data in transactions_data:
            category_id = await _match_pluggy_category(
                session, user_id, txn_data.pluggy_category
            )
            # Resolve payee entity from raw payee text
            payee_id = None
            if txn_data.payee:
                payee_entity = await get_or_create_payee(session, user_id, txn_data.payee)
                payee_id = payee_entity.id

            transaction = Transaction(
                user_id=user_id,
                account_id=account.id,
                external_id=txn_data.external_id,
                description=txn_data.description,
                amount=txn_data.amount,
                currency=txn_data.currency or acc_data.currency or user_currency,
                date=txn_data.date,
                type=txn_data.type,
                source="sync",
                status=txn_data.status,
                payee=txn_data.payee,
                payee_id=payee_id,
                raw_data=txn_data.raw_data,
                category_id=category_id,
                installment_number=txn_data.installment_number,
                total_installments=txn_data.total_installments,
                installment_total_amount=txn_data.installment_total_amount,
                installment_purchase_date=txn_data.installment_purchase_date,
            )
            apply_effective_date(transaction, account)
            session.add(transaction)
            await session.flush()
            new_tx_ids.append(transaction.id)
            if not category_id:
                await apply_rules_to_transaction(session, user_id, transaction)

            # Prefer bank-provided conversion for international transactions
            acct_currency = acc_data.currency or user_currency
            if (
                txn_data.amount_in_account_currency is not None
                and txn_data.amount
                and acct_currency == user_currency
                and txn_data.currency != acct_currency
            ):
                transaction.amount_primary = txn_data.amount_in_account_currency
                transaction.fx_rate_used = txn_data.amount_in_account_currency / txn_data.amount
            else:
                await stamp_primary_amount(session, user_id, transaction)

        # After importing the initial batch, reconcile the opening balance so
        # that SUM(all transactions) matches the provider-reported balance. Any
        # history that falls outside the provider's lookback window gets
        # absorbed into this synthetic transaction.
        await sync_opening_balance_for_connected_account(session, account)

    # Detect transfer pairs among newly synced transactions
    await detect_transfer_pairs(session, user_id, candidate_ids=new_tx_ids)

    connection.last_sync_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(connection)
    return connection


def _description_similarity(a: str | None, b: str | None) -> float:
    """Token overlap ratio between two descriptions."""
    if not a or not b:
        return 0.0
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    return len(intersection) / max(len(tokens_a), len(tokens_b))


async def _fuzzy_match_manual(
    session: AsyncSession,
    account_id: uuid.UUID,
    txn_data,
) -> Optional[Transaction]:
    """Try to find a manual transaction that matches the incoming synced one."""
    date_lo = txn_data.date - timedelta(days=3)
    date_hi = txn_data.date + timedelta(days=3)

    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.external_id.is_(None),
            Transaction.source == "manual",
            Transaction.amount == txn_data.amount,
            Transaction.type == txn_data.type,
            Transaction.date >= date_lo,
            Transaction.date <= date_hi,
        )
    )
    candidates = result.scalars().all()
    if not candidates:
        return None

    best_match = None
    best_score = 0.0
    for candidate in candidates:
        score = _description_similarity(candidate.description, txn_data.description)
        if score > best_score:
            best_score = score
            best_match = candidate

    if best_match and best_score >= 0.6:
        return best_match
    return None


async def _cleanup_phantom_duplicates(
    session: AsyncSession,
    connection_id: uuid.UUID,
) -> int:
    """Delete synced transactions that are phantom duplicates.

    Some providers (or sandbox data) report the same payment twice with
    different external ids on adjacent days. Transfer detection matches the
    real one against the counterpart in another account; the phantom remains
    orphaned.

    We delete an unpaired synced tx when it has a *paired* sibling in the same
    account with: same amount, same type, near-identical description, dated
    within ±1 day. The pairing of the sibling is the safety signal that lets
    us distinguish the duplicate from a legitimate same-day repeat (e.g. two
    real Uber rides for the same fare).
    """
    accounts_result = await session.execute(
        select(Account.id).where(Account.connection_id == connection_id)
    )
    account_ids = [row[0] for row in accounts_result.all()]
    if not account_ids:
        return 0

    unmatched_result = await session.execute(
        select(Transaction).where(
            Transaction.account_id.in_(account_ids),
            Transaction.source == "sync",
            Transaction.transfer_pair_id.is_(None),
        )
    )
    unmatched = list(unmatched_result.scalars().all())

    deleted = 0
    for tx in unmatched:
        date_lo = tx.date - timedelta(days=1)
        date_hi = tx.date + timedelta(days=1)
        sibling_result = await session.execute(
            select(Transaction).where(
                Transaction.account_id == tx.account_id,
                Transaction.source == "sync",
                Transaction.amount == tx.amount,
                Transaction.type == tx.type,
                Transaction.date >= date_lo,
                Transaction.date <= date_hi,
                Transaction.transfer_pair_id.is_not(None),
                Transaction.id != tx.id,
            )
        )
        for sibling in sibling_result.scalars():
            if _description_similarity(sibling.description, tx.description) >= 0.9:
                await session.delete(tx)
                deleted += 1
                break

    return deleted


async def sync_connection(
    session: AsyncSession, connection_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[BankConnection, int]:
    connection = await get_connection(session, connection_id, user_id)
    if not connection:
        raise ValueError("Connection not found")

    conn_settings = connection.settings or {}
    payee_source = conn_settings.get("payee_source", "auto")
    import_pending = conn_settings.get("import_pending", True)

    try:
        provider = get_provider(connection.provider)

        # Refresh credentials if needed
        credentials = await provider.refresh_credentials(connection.credentials)
        connection.credentials = credentials

        # Update accounts
        user = await session.get(User, user_id)
        user_currency = user.primary_currency if user else get_settings().default_currency
        new_tx_ids: list[uuid.UUID] = []
        merged_count = 0
        accounts_data = await provider.get_accounts(credentials)
        for acc_data in accounts_data:
            result = await session.execute(
                select(Account).where(
                    Account.connection_id == connection.id,
                    Account.external_id == acc_data.external_id,
                )
            )
            account = result.scalar_one_or_none()

            if account:
                account.balance = acc_data.balance
                account.name = acc_data.name
                if acc_data.type == "credit_card":
                    # Preserve existing CC metadata when the provider doesn't
                    # expose it. Pluggy's creditData fields (limit, close/due
                    # dates, minimum payment, brand/level) are intermittently
                    # null even on connectors that have them elsewhere, and
                    # users may have filled them in manually via the edit
                    # dialog. Treat user input + previously-synced values as
                    # the higher source of truth than a fresh None.
                    if acc_data.credit_limit is not None:
                        account.credit_limit = acc_data.credit_limit
                    if acc_data.statement_close_day is not None:
                        account.statement_close_day = acc_data.statement_close_day
                    if acc_data.payment_due_day is not None:
                        account.payment_due_day = acc_data.payment_due_day
                    if acc_data.minimum_payment is not None:
                        account.minimum_payment = acc_data.minimum_payment
                    if acc_data.card_brand is not None:
                        account.card_brand = acc_data.card_brand
                    if acc_data.card_level is not None:
                        account.card_level = acc_data.card_level
            else:
                is_cc = acc_data.type == "credit_card"
                account = Account(
                    user_id=user_id,
                    connection_id=connection.id,
                    external_id=acc_data.external_id,
                    name=acc_data.name,
                    type=acc_data.type,
                    balance=acc_data.balance,
                    currency=acc_data.currency,
                    credit_limit=acc_data.credit_limit if is_cc else None,
                    statement_close_day=acc_data.statement_close_day if is_cc else None,
                    payment_due_day=acc_data.payment_due_day if is_cc else None,
                    minimum_payment=acc_data.minimum_payment if is_cc else None,
                    card_brand=acc_data.card_brand if is_cc else None,
                    card_level=acc_data.card_level if is_cc else None,
                )
                session.add(account)
                await session.flush()

            if account and account.is_closed:
                continue

            # Fetch and sync transactions. The 14-day rewind is on Pluggy's
            # `createdAt` (when their row was inserted), so it covers two
            # cases: (1) PENDING transactions that POSTED since last sync,
            # (2) any rows Pluggy ingested late but backdated. Dedup on
            # external_id below handles overlap cheaply.
            since = (
                connection.last_sync_at.date() - timedelta(days=14)
                if connection.last_sync_at
                else None
            )
            transactions_data = await provider.get_transactions(
                credentials, acc_data.external_id, since, payee_source=payee_source
            )

            if not import_pending:
                transactions_data = [t for t in transactions_data if t.status != "pending"]

            for txn_data in transactions_data:
                existing = await session.execute(
                    select(Transaction).where(
                        Transaction.account_id == account.id,
                        Transaction.external_id == txn_data.external_id,
                    )
                )
                existing_tx = existing.scalar_one_or_none()
                if existing_tx:
                    if existing_tx.status == "pending" and txn_data.status == "posted":
                        existing_tx.status = "posted"
                    continue

                # Pass 2: Fuzzy match against manual transactions
                fuzzy_match = await _fuzzy_match_manual(session, account.id, txn_data)
                if fuzzy_match:
                    fuzzy_match.external_id = txn_data.external_id
                    fuzzy_match.source = "sync"
                    fuzzy_match.raw_data = txn_data.raw_data
                    if not fuzzy_match.payee and txn_data.payee:
                        fuzzy_match.payee = txn_data.payee
                    merged_count += 1
                    continue

                category_id = await _match_pluggy_category(
                    session, user_id, txn_data.pluggy_category
                )

                # Resolve payee entity from raw payee text
                sync_payee_id = None
                if txn_data.payee:
                    sync_payee_entity = await get_or_create_payee(session, user_id, txn_data.payee)
                    sync_payee_id = sync_payee_entity.id

                transaction = Transaction(
                    user_id=user_id,
                    account_id=account.id,
                    external_id=txn_data.external_id,
                    description=txn_data.description,
                    amount=txn_data.amount,
                    currency=txn_data.currency or acc_data.currency or user_currency,
                    date=txn_data.date,
                    type=txn_data.type,
                    source="sync",
                    status=txn_data.status,
                    payee=txn_data.payee,
                    payee_id=sync_payee_id,
                    raw_data=txn_data.raw_data,
                    category_id=category_id,
                    installment_number=txn_data.installment_number,
                    total_installments=txn_data.total_installments,
                    installment_total_amount=txn_data.installment_total_amount,
                    installment_purchase_date=txn_data.installment_purchase_date,
                )
                apply_effective_date(transaction, account)
                session.add(transaction)
                await session.flush()
                new_tx_ids.append(transaction.id)
                if not category_id:
                    await apply_rules_to_transaction(session, user_id, transaction)

                # Prefer bank-provided conversion for international transactions
                acct_currency = acc_data.currency or user_currency
                if (
                    txn_data.amount_in_account_currency is not None
                    and txn_data.amount
                    and acct_currency == user_currency
                    and txn_data.currency != acct_currency
                ):
                    transaction.amount_primary = txn_data.amount_in_account_currency
                    transaction.fx_rate_used = txn_data.amount_in_account_currency / txn_data.amount
                else:
                    await stamp_primary_amount(session, user_id, transaction)

            # Reconcile the opening balance after any new transactions land so
            # SUM(all txs) keeps matching account.balance from the provider.
            await sync_opening_balance_for_connected_account(session, account)

        # Detect transfer pairs among newly synced transactions
        if new_tx_ids:
            await detect_transfer_pairs(session, user_id, candidate_ids=new_tx_ids)

        # Clean up phantom duplicates: providers occasionally double-report the
        # same payment with different ids. Once transfer detection has paired
        # the real one, the orphan twin gets removed here.
        await _cleanup_phantom_duplicates(session, connection.id)

        connection.last_sync_at = datetime.now(timezone.utc)
        connection.status = "active"
        await session.commit()
        await session.refresh(connection)
        return connection, merged_count

    except Exception:
        # Mark connection as errored so UI shows reconnect banner
        await session.rollback()
        async with session.begin():
            conn = await session.get(BankConnection, connection_id)
            if conn:
                conn.status = "error"
        raise


async def delete_connection(
    session: AsyncSession, connection_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    connection = await get_connection(session, connection_id, user_id)
    if not connection:
        return False

    await session.delete(connection)
    await session.commit()
    return True
