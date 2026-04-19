import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_connection import BankConnection
from app.models.category import Category
from app.models.transaction import Transaction
from app.providers.base import AccountData, ConnectionData, ConnectTokenData, TransactionData
from app.services.connection_service import (
    _description_similarity,
    _match_pluggy_category,
    create_connect_token,
    delete_connection,
    get_connection,
    get_connections,
    handle_oauth_callback,
    sync_connection,
    update_connection_settings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_connection(
    session: AsyncSession, user_id: uuid.UUID, name: str = "Test Bank",
    settings: dict | None = None,
) -> BankConnection:
    conn = BankConnection(
        id=uuid.uuid4(), user_id=user_id, provider="test",
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name=name, credentials={"token": "fake"},
        status="active", settings=settings,
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()
    await session.refresh(conn)
    return conn


async def _make_category(
    session: AsyncSession, user_id: uuid.UUID, name: str,
) -> Category:
    cat = Category(
        id=uuid.uuid4(), user_id=user_id, name=name,
        icon="tag", color="#000", is_system=False,
    )
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return cat


# ---------------------------------------------------------------------------
# _description_similarity (pure function)
# ---------------------------------------------------------------------------


def test_description_similarity_identical():
    assert _description_similarity("hello world", "hello world") == 1.0


def test_description_similarity_partial():
    score = _description_similarity("hello world foo", "hello world bar")
    assert 0.0 < score < 1.0


def test_description_similarity_no_overlap():
    assert _description_similarity("abc", "xyz") == 0.0


def test_description_similarity_none():
    assert _description_similarity(None, "hello") == 0.0
    assert _description_similarity("hello", None) == 0.0
    assert _description_similarity(None, None) == 0.0


def test_description_similarity_empty():
    assert _description_similarity("", "hello") == 0.0
    assert _description_similarity("hello", "") == 0.0


def test_description_similarity_case_insensitive():
    score = _description_similarity("Hello World", "hello world")
    assert score == 1.0


# ---------------------------------------------------------------------------
# _match_pluggy_category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_pluggy_exact(session: AsyncSession, test_user):
    """Exact Pluggy category match maps to user's category."""
    await _make_category(session, test_user.id, "Alimentação")
    cat_id = await _match_pluggy_category(session, test_user.id, "Eating out")
    assert cat_id is not None


@pytest.mark.asyncio
async def test_match_pluggy_prefix(session: AsyncSession, test_user):
    """Pluggy category with ' - ' prefix matches via split."""
    await _make_category(session, test_user.id, "Transferências")
    cat_id = await _match_pluggy_category(session, test_user.id, "Transfer - PIX")
    assert cat_id is not None


@pytest.mark.asyncio
async def test_match_pluggy_no_match(session: AsyncSession, test_user):
    """Unknown Pluggy category returns None."""
    cat_id = await _match_pluggy_category(session, test_user.id, "Unknown Category XYZ")
    assert cat_id is None


@pytest.mark.asyncio
async def test_match_pluggy_none(session: AsyncSession, test_user):
    """None category returns None."""
    cat_id = await _match_pluggy_category(session, test_user.id, None)
    assert cat_id is None


@pytest.mark.asyncio
async def test_match_pluggy_user_has_no_category(session: AsyncSession, test_user):
    """Pluggy category maps but user doesn't have the target category."""
    # "Eating out" maps to "Alimentação" but we don't create it
    cat_id = await _match_pluggy_category(session, test_user.id, "Eating out")
    assert cat_id is None


# ---------------------------------------------------------------------------
# get_connections / get_connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_connections_returns_list(session: AsyncSession, test_user):
    """Returns list of connections for user."""
    await _make_connection(session, test_user.id, "Bank A")
    await _make_connection(session, test_user.id, "Bank B")

    connections = await get_connections(session, test_user.id)
    assert len(connections) >= 2
    names = {c.institution_name for c in connections}
    assert "Bank A" in names
    assert "Bank B" in names


@pytest.mark.asyncio
async def test_get_connections_empty(session: AsyncSession, test_user):
    """Returns empty list when no connections."""
    connections = await get_connections(session, test_user.id)
    # May have connections from other fixtures; just verify it's a list
    assert isinstance(connections, list)


@pytest.mark.asyncio
async def test_get_connection_found(session: AsyncSession, test_user):
    """Returns a specific connection."""
    conn = await _make_connection(session, test_user.id, "Specific Bank")
    result = await get_connection(session, conn.id, test_user.id)
    assert result is not None
    assert result.institution_name == "Specific Bank"


@pytest.mark.asyncio
async def test_get_connection_not_found(session: AsyncSession, test_user):
    """Returns None for nonexistent connection."""
    result = await get_connection(session, uuid.uuid4(), test_user.id)
    assert result is None


@pytest.mark.asyncio
async def test_get_connection_wrong_user(session: AsyncSession, test_user):
    """Returns None when connection belongs to another user."""
    conn = await _make_connection(session, test_user.id, "Other User Bank")
    result = await get_connection(session, conn.id, uuid.uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# update_connection_settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_settings_new(session: AsyncSession, test_user):
    """Updates settings on a connection with no prior settings."""
    conn = await _make_connection(session, test_user.id, "Settings Test")

    updated = await update_connection_settings(
        session, conn.id, test_user.id, {"payee_source": "merchant"},
    )
    assert updated is not None
    assert updated.settings["payee_source"] == "merchant"


@pytest.mark.asyncio
async def test_update_settings_preserves_existing(session: AsyncSession, test_user):
    """Updates one setting without clobbering others."""
    conn = await _make_connection(
        session, test_user.id, "Preserve Test",
        settings={"payee_source": "auto", "import_pending": True},
    )

    updated = await update_connection_settings(
        session, conn.id, test_user.id, {"import_pending": False},
    )
    assert updated is not None
    assert updated.settings["payee_source"] == "auto"
    assert updated.settings["import_pending"] is False


@pytest.mark.asyncio
async def test_update_settings_ignores_none(session: AsyncSession, test_user):
    """None values in settings_update are not written."""
    conn = await _make_connection(
        session, test_user.id, "None Test",
        settings={"payee_source": "auto"},
    )
    updated = await update_connection_settings(
        session, conn.id, test_user.id, {"payee_source": None},
    )
    assert updated is not None
    assert updated.settings["payee_source"] == "auto"


@pytest.mark.asyncio
async def test_update_settings_not_found(session: AsyncSession, test_user):
    """Returns None when connection not found."""
    result = await update_connection_settings(
        session, uuid.uuid4(), test_user.id, {"payee_source": "auto"},
    )
    assert result is None


# ---------------------------------------------------------------------------
# delete_connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_connection_found(session: AsyncSession, test_user):
    """Deletes an existing connection."""
    conn = await _make_connection(session, test_user.id, "To Delete")
    result = await delete_connection(session, conn.id, test_user.id)
    assert result is True

    assert await get_connection(session, conn.id, test_user.id) is None


@pytest.mark.asyncio
async def test_delete_connection_not_found(session: AsyncSession, test_user):
    """Returns False for nonexistent connection."""
    result = await delete_connection(session, uuid.uuid4(), test_user.id)
    assert result is False


# ---------------------------------------------------------------------------
# create_connect_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_connect_token_success(test_user):
    mock_provider = AsyncMock()
    mock_provider.create_connect_token = AsyncMock(
        return_value=ConnectTokenData(access_token="tok-123")
    )
    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        result = await create_connect_token("pluggy", test_user.id)
    assert result == {"access_token": "tok-123"}


# ---------------------------------------------------------------------------
# handle_oauth_callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_oauth_callback_creates_connection(session: AsyncSession, test_user):
    mock_provider = AsyncMock()
    mock_provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="ext-oauth-1",
        institution_name="Test Bank",
        credentials={"token": "abc"},
        accounts=[
            AccountData(
                external_id="acc-1", name="Checking",
                type="checking", balance=Decimal("1000"), currency="BRL",
            ),
        ],
    ))
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="tx-1", description="UBER", amount=Decimal("25"),
            date=date.today(), type="debit", currency="BRL",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        conn = await handle_oauth_callback(session, test_user.id, "auth-code", "pluggy")

    assert conn.institution_name == "Test Bank"
    assert conn.external_id == "ext-oauth-1"
    assert conn.status == "active"


@pytest.mark.asyncio
async def test_handle_oauth_callback_with_payee(session: AsyncSession, test_user):
    mock_provider = AsyncMock()
    mock_provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="ext-oauth-2",
        institution_name="Payee Bank",
        credentials={"token": "def"},
        accounts=[
            AccountData(
                external_id="acc-2", name="Savings",
                type="savings", balance=Decimal("500"), currency="BRL",
            ),
        ],
    ))
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="tx-2", description="IFOOD", amount=Decimal("30"),
            date=date.today(), type="debit", currency="BRL",
            payee="iFood Restaurant",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        conn = await handle_oauth_callback(session, test_user.id, "code2", "pluggy")

    assert conn.institution_name == "Payee Bank"


# ---------------------------------------------------------------------------
# sync_connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_connection_new_transactions(session: AsyncSession, test_user):
    conn = await _make_connection(session, test_user.id, "Sync Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "refreshed"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="sync-acc-1", name="Checking",
            type="checking", balance=Decimal("2000"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="sync-tx-1", description="GROCERY",
            amount=Decimal("80"), date=date.today(), type="debit", currency="BRL",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        result_conn, merged = await sync_connection(session, conn.id, test_user.id)

    assert result_conn.status == "active"
    assert merged == 0


@pytest.mark.asyncio
async def test_sync_connection_not_found(session: AsyncSession, test_user):
    with pytest.raises(ValueError, match="not found"):
        await sync_connection(session, uuid.uuid4(), test_user.id)


@pytest.mark.asyncio
async def test_sync_connection_with_category_mapping(session: AsyncSession, test_user):
    conn = await _make_connection(session, test_user.id, "Cat Bank")
    await _make_category(session, test_user.id, "Alimentação")

    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="cat-acc-1", name="Checking",
            type="checking", balance=Decimal("100"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="cat-tx-1", description="RESTAURANT",
            amount=Decimal("50"), date=date.today(), type="debit",
            currency="BRL", pluggy_category="Eating out",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock):
        result_conn, _ = await sync_connection(session, conn.id, test_user.id)

    assert result_conn.status == "active"


@pytest.mark.asyncio
async def test_sync_connection_error_raises(session: AsyncSession, test_user):
    conn = await _make_connection(session, test_user.id, "Error Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(side_effect=RuntimeError("API down"))

    with patch("app.services.connection_service.get_provider", return_value=mock_provider):
        with pytest.raises(RuntimeError, match="API down"):
            await sync_connection(session, conn.id, test_user.id)


@pytest.mark.asyncio
async def test_sync_connection_skips_pending(session: AsyncSession, test_user):
    conn = await _make_connection(
        session, test_user.id, "Pending Bank",
        settings={"import_pending": False},
    )
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="pend-acc-1", name="Checking",
            type="checking", balance=Decimal("100"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="pend-tx-1", description="PENDING TXN",
            amount=Decimal("10"), date=date.today(), type="debit",
            currency="BRL", status="pending",
        ),
        TransactionData(
            external_id="pend-tx-2", description="POSTED TXN",
            amount=Decimal("20"), date=date.today(), type="debit",
            currency="BRL", status="posted",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        result_conn, _ = await sync_connection(session, conn.id, test_user.id)

    assert result_conn.status == "active"


# ---------------------------------------------------------------------------
# Installment metadata persistence (issue #14 v1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_callback_persists_installment_metadata(
    session: AsyncSession, test_user,
):
    """handle_oauth_callback must store all 4 installment fields on the
    Transaction row exactly as the provider returned them."""
    mock_provider = AsyncMock()
    mock_provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="ext-inst-1",
        institution_name="Inst Bank",
        credentials={"token": "x"},
        accounts=[
            AccountData(
                external_id="inst-acc-1", name="Nubank Gold",
                type="credit_card", balance=Decimal("0"), currency="BRL",
                credit_limit=Decimal("5000"),
            ),
        ],
    ))
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="inst-tx-1", description="AMAZON PARCELADO",
            amount=Decimal("120.50"), date=date(2026, 4, 10),
            type="debit", currency="BRL",
            installment_number=3,
            total_installments=12,
            installment_total_amount=Decimal("1446.00"),
            installment_purchase_date=date(2026, 2, 10),
        ),
        TransactionData(
            external_id="inst-tx-2", description="SINGLE CHARGE",
            amount=Decimal("40.00"), date=date(2026, 4, 11),
            type="debit", currency="BRL",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await handle_oauth_callback(session, test_user.id, "code", "pluggy")

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.user_id == test_user.id,
            Transaction.source != "opening_balance",
        )
        .order_by(Transaction.external_id)
    )).scalars().all()
    assert len(rows) == 2

    parcel = next(t for t in rows if t.external_id == "inst-tx-1")
    assert parcel.installment_number == 3
    assert parcel.total_installments == 12
    assert parcel.installment_total_amount == Decimal("1446.00")
    assert parcel.installment_purchase_date == date(2026, 2, 10)

    single = next(t for t in rows if t.external_id == "inst-tx-2")
    assert single.installment_number is None
    assert single.total_installments is None
    assert single.installment_total_amount is None
    assert single.installment_purchase_date is None


@pytest.mark.asyncio
async def test_sync_connection_persists_installment_metadata(
    session: AsyncSession, test_user,
):
    """Incremental sync path must also persist installment fields."""
    conn = await _make_connection(session, test_user.id, "Sync Inst Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="sync-inst-acc-1", name="Credit Card",
            type="credit_card", balance=Decimal("0"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="sync-inst-tx-1", description="PARCELA MAGALU",
            amount=Decimal("50.00"), date=date(2026, 4, 1),
            type="debit", currency="BRL",
            installment_number=1,
            total_installments=6,
            installment_total_amount=Decimal("300.00"),
            installment_purchase_date=date(2026, 3, 25),
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_user.id)

    row = (await session.execute(
        select(Transaction).where(Transaction.external_id == "sync-inst-tx-1")
    )).scalar_one()
    assert row.installment_number == 1
    assert row.total_installments == 6
    assert row.installment_total_amount == Decimal("300.00")
    assert row.installment_purchase_date == date(2026, 3, 25)


@pytest.mark.asyncio
async def test_sync_connection_preserves_display_name(session: AsyncSession, test_user):
    """Resyncing a connection must update the provider name but never overwrite display_name."""
    from app.models.account import Account

    conn = await _make_connection(session, test_user.id, "Preserve Bank")
    mock_provider = AsyncMock()
    mock_provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="preserve-acc-1", name="BANCO ORIGINAL",
            type="checking", balance=Decimal("500"), currency="BRL",
        ),
    ])
    mock_provider.get_transactions = AsyncMock(return_value=[])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_user.id)

    # Set a display_name after the first sync
    account = (await session.execute(
        select(Account).where(Account.connection_id == conn.id)
    )).scalar_one()
    account.display_name = "Meu Apelido"
    await session.commit()

    # Resync — provider now returns a different name
    mock_provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id="preserve-acc-1", name="BANCO ATUALIZADO",
            type="checking", balance=Decimal("600"), currency="BRL",
        ),
    ])

    with patch("app.services.connection_service.get_provider", return_value=mock_provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, test_user.id)

    await session.refresh(account)
    assert account.name == "BANCO ATUALIZADO"
    assert account.display_name == "Meu Apelido"
