import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models.bank_connection import BankConnection
from app.models.account import Account
from app.models.transaction import Transaction
from app.models.user import User
from app.providers import get_provider
from app.services.rule_service import apply_rules_to_transaction
from app.services.transfer_detection_service import detect_transfer_pairs
from app.services.fx_rate_service import stamp_primary_amount
from app.services.month_service import (
    get_current_month_period,
    month_to_period_value,
    period_to_month_start,
    resolve_current_monthly_period,
)
from app.services.payee_service import get_or_create_payee

settings = get_settings()


def _next_period(period: str) -> str:
    month_start = period_to_month_start(period)
    next_month = month_start.replace(day=28) + timedelta(days=4)
    return month_to_period_value(next_month.replace(day=1))


async def _get_target_bill_ids(
    provider,
    credentials: dict,
    account_external_id: str,
    current_period: str | None,
) -> set[str]:
    if current_period is None:
        return set()

    target_due_period = _next_period(current_period)
    bills = await provider.get_bills(credentials, account_external_id)
    return {
        bill.id
        for bill in bills
        if month_to_period_value(bill.due_date) == target_due_period
    }


async def _load_bill_transactions_for_current_month(
    provider,
    credentials: dict,
    account_external_id: str,
    current_period: str | None,
    payee_source: str = "auto",
) -> list:
    if current_period is None:
        return []

    target_bill_ids = await _get_target_bill_ids(
        provider, credentials, account_external_id, current_period
    )
    transactions = await provider.get_transactions(
        credentials,
        account_external_id,
        None,
        payee_source=payee_source,
    )

    if target_bill_ids:
        bill_transactions = [txn for txn in transactions if txn.bill_id in target_bill_ids]
        if bill_transactions:
            return bill_transactions

    # Some connectors expose credit-card bills but never populate transaction.billId.
    # In that case, fall back to the active month window and keep expense rows only.
    month_start = period_to_month_start(current_period)
    next_month_start = period_to_month_start(_next_period(current_period))
    return [
        txn
        for txn in transactions
        if txn.type == "debit" and month_start <= txn.date < next_month_start
    ]


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

    if "bill_import_enabled" in settings_update and settings_update["bill_import_enabled"] is not None:
        for account in connection.accounts:
            if account.type == "credit_card":
                account.bill_import_enabled = bool(settings_update["bill_import_enabled"])

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
        settings={
            "display_name": connection_data.institution_name,
            "bill_import_enabled": True,
        },
        status="active",
    )
    session.add(connection)
    await session.flush()

    user = await session.get(User, user_id)
    user_currency = user.primary_currency if user else get_settings().default_currency
    current_monthly_period = None
    if user is not None and get_current_month_period(user):
        current_monthly_period = await resolve_current_monthly_period(session, user_id, user)
    new_tx_ids: list[uuid.UUID] = []
    current_period = get_current_month_period(user) if user is not None else None

    for acc_data in connection_data.accounts:
        account = Account(
            user_id=user_id,
            monthly_period_id=current_monthly_period.id if current_monthly_period is not None else None,
            connection_id=connection.id,
            external_id=acc_data.external_id,
            name=acc_data.name,
            custom_name=None,
            type=acc_data.type,
            balance=acc_data.balance,
            currency=acc_data.currency,
            bill_import_enabled=bool((connection.settings or {}).get("bill_import_enabled", True)),
        )
        session.add(account)
        await session.flush()

        if acc_data.type != "credit_card" or not bool((connection.settings or {}).get("bill_import_enabled", True)):
            continue

        transactions_data = await _load_bill_transactions_for_current_month(
            provider,
            connection_data.credentials,
            acc_data.external_id,
            current_period,
        )
        for txn_data in transactions_data:
            # Resolve payee entity from raw payee text
            payee_id = None
            if txn_data.payee:
                payee_entity = await get_or_create_payee(session, user_id, txn_data.payee)
                payee_id = payee_entity.id

            transaction = Transaction(
                user_id=user_id,
                account_id=account.id,
                monthly_period_id=(
                    current_monthly_period.id if current_monthly_period is not None else None
                ),
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
                category_id=None,
            )
            session.add(transaction)
            await session.flush()
            new_tx_ids.append(transaction.id)
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
        current_monthly_period = None
        if user is not None and get_current_month_period(user):
            current_monthly_period = await resolve_current_monthly_period(session, user_id, user)
        current_period = get_current_month_period(user) if user is not None else None
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
                if not account.custom_name:
                    account.name = acc_data.name
            else:
                account = Account(
                    user_id=user_id,
                    monthly_period_id=current_monthly_period.id if current_monthly_period is not None else None,
                    connection_id=connection.id,
                    external_id=acc_data.external_id,
                    name=acc_data.name,
                    custom_name=None,
                    type=acc_data.type,
                    balance=acc_data.balance,
                    currency=acc_data.currency,
                    bill_import_enabled=bool(conn_settings.get("bill_import_enabled", True)),
                )
                session.add(account)
                await session.flush()

            if account and account.is_closed:
                continue

            if (
                account.type != "credit_card"
                or not bool(conn_settings.get("bill_import_enabled", True))
                or not account.bill_import_enabled
            ):
                continue

            transactions_data = await _load_bill_transactions_for_current_month(
                provider,
                credentials,
                acc_data.external_id,
                current_period,
                payee_source=payee_source,
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

                # Resolve payee entity from raw payee text
                sync_payee_id = None
                if txn_data.payee:
                    sync_payee_entity = await get_or_create_payee(session, user_id, txn_data.payee)
                    sync_payee_id = sync_payee_entity.id

                transaction = Transaction(
                    user_id=user_id,
                    account_id=account.id,
                    monthly_period_id=(
                        current_monthly_period.id if current_monthly_period is not None else None
                    ),
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
                    category_id=None,
                )
                session.add(transaction)
                await session.flush()
                new_tx_ids.append(transaction.id)
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

        # Detect transfer pairs among newly synced transactions
        if new_tx_ids:
            await detect_transfer_pairs(session, user_id, candidate_ids=new_tx_ids)

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
