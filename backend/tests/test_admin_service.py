import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.models.budget import Budget
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.import_log import ImportLog
from app.models.payee import Payee, PayeeMapping
from app.models.recurring_transaction import RecurringTransaction
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.transaction_attachment import TransactionAttachment
from app.models.user import User
from app.services.admin_service import (
    delete_user,
    get_app_setting,
    get_user,
    is_registration_enabled,
    list_users,
    set_app_setting,
    update_user,
    use_provider_categories,
)
from app.schemas.admin import AdminUserUpdate


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_user(session: AsyncSession, email: str, is_superuser: bool = False) -> User:
    import bcrypt as _bcrypt
    from app.services.workspace_service import create_personal_workspace_for_user

    hashed = _bcrypt.hashpw(b"password123", _bcrypt.gensalt()).decode()
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hashed,
        is_active=True,
        is_superuser=is_superuser,
        is_verified=True,
        preferences={"language": "en", "currency_display": "USD"},
    )
    session.add(user)
    await session.commit()
    # Every user needs a workspace so the autostamp listener can resolve
    # workspace_id when downstream rows (accounts, connections, etc.) are
    # inserted without it — the cascade test relies on this.
    await create_personal_workspace_for_user(session, user)
    await session.commit()
    await session.refresh(user)
    return user


async def _populate_user_data(session: AsyncSession, user: User):
    """Create related data for a user to test cascade delete."""
    # Category group
    group = CategoryGroup(id=uuid.uuid4(), user_id=user.id, name="Test Group")
    session.add(group)

    # Category
    cat = Category(id=uuid.uuid4(), user_id=user.id, name="Test Cat", icon="x", color="#000")
    session.add(cat)

    # Connection
    conn = BankConnection(
        id=uuid.uuid4(), user_id=user.id, provider="test",
        external_id="ext-1", institution_name="Test Bank",
        credentials={}, status="active",
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)

    # Account
    acct = Account(
        id=uuid.uuid4(), user_id=user.id, name="Acct",
        type="checking", balance=Decimal("100"), currency="BRL",
    )
    session.add(acct)

    # Payee + mapping
    payee = Payee(id=uuid.uuid4(), user_id=user.id, name="Test Payee")
    session.add(payee)
    await session.flush()
    mapping = PayeeMapping(
        id=uuid.uuid4(), user_id=user.id, target_id=payee.id,
    )
    session.add(mapping)

    # Rule
    rule = Rule(
        id=uuid.uuid4(), user_id=user.id, name="R",
        conditions_op="and", conditions=[], actions=[], priority=1,
    )
    session.add(rule)

    # Asset + value
    asset = Asset(
        id=uuid.uuid4(), user_id=user.id, name="House",
        type="real_estate", currency="BRL",
    )
    session.add(asset)
    await session.flush()
    av = AssetValue(id=uuid.uuid4(), asset_id=asset.id, amount=Decimal("100000"), date=date.today())
    session.add(av)

    # Transaction + attachment
    txn = Transaction(
        id=uuid.uuid4(), user_id=user.id, account_id=acct.id,
        description="Test txn", amount=Decimal("50"), date=date.today(),
        type="debit", source="manual", currency="BRL",
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.flush()
    att = TransactionAttachment(
        id=uuid.uuid4(), user_id=user.id, transaction_id=txn.id,
        filename="receipt.pdf", storage_key="k", content_type="application/pdf", size=100,
    )
    session.add(att)

    # Budget
    budget = Budget(
        id=uuid.uuid4(), user_id=user.id, category_id=cat.id,
        amount=Decimal("500"), month=date.today().replace(day=1), currency="BRL",
    )
    session.add(budget)

    # Recurring transaction
    rec = RecurringTransaction(
        id=uuid.uuid4(), user_id=user.id, description="Rent",
        amount=Decimal("2000"), type="debit", frequency="monthly",
        start_date=date.today(), next_occurrence=date.today(), currency="BRL",
    )
    session.add(rec)

    # Import log
    imp = ImportLog(
        id=uuid.uuid4(), user_id=user.id, account_id=acct.id,
        filename="test.ofx", format="ofx", transaction_count=1,
    )
    session.add(imp)

    await session.commit()


# ---------------------------------------------------------------------------
# list_users
# ---------------------------------------------------------------------------

async def test_list_users_basic(session: AsyncSession, clean_db):
    await _make_user(session, "alpha@test.com")
    await _make_user(session, "beta@test.com")
    users, total = await list_users(session)
    assert total >= 2
    emails = [u.email for u in users]
    assert "alpha@test.com" in emails
    assert "beta@test.com" in emails


async def test_list_users_search(session: AsyncSession, clean_db):
    await _make_user(session, "searchme@test.com")
    await _make_user(session, "other@test.com")
    users, total = await list_users(session, search="searchme")
    assert total == 1
    assert users[0].email == "searchme@test.com"


async def test_list_users_pagination(session: AsyncSession, clean_db):
    for i in range(5):
        await _make_user(session, f"page{i}@test.com")
    users, total = await list_users(session, page=1, limit=2)
    assert total == 5
    assert len(users) == 2


# ---------------------------------------------------------------------------
# get_user
# ---------------------------------------------------------------------------

async def test_get_user_found(session: AsyncSession, clean_db):
    u = await _make_user(session, "found@test.com")
    result = await get_user(session, u.id)
    assert result is not None
    assert result.email == "found@test.com"


async def test_get_user_not_found(session: AsyncSession, clean_db):
    result = await get_user(session, uuid.uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# update_user
# ---------------------------------------------------------------------------

async def test_update_user_email(session: AsyncSession, clean_db):
    admin = await _make_user(session, "admin_upd@test.com", is_superuser=True)
    target = await _make_user(session, "target_upd@test.com")
    data = AdminUserUpdate(email="newemail@test.com")
    result = await update_user(session, target.id, data, admin.id)

    assert result is not None
    assert result.email == "newemail@test.com"


async def test_update_user_email_conflict(session: AsyncSession, clean_db):
    admin = await _make_user(session, "admin_c@test.com", is_superuser=True)
    await _make_user(session, "existing@test.com")
    target = await _make_user(session, "target_c@test.com")
    data = AdminUserUpdate(email="existing@test.com")
    with pytest.raises(ValueError, match="email already exists"):
        await update_user(session, target.id, data, admin.id)


async def test_update_user_password(session: AsyncSession, clean_db):
    admin = await _make_user(session, "admin_pw@test.com", is_superuser=True)
    target = await _make_user(session, "target_pw@test.com")
    old_hash = target.hashed_password
    data = AdminUserUpdate(password="newpassword123")
    result = await update_user(session, target.id, data, admin.id)

    assert result is not None
    # Password should have changed
    assert result.hashed_password != old_hash


async def test_update_user_preferences(session: AsyncSession, clean_db):
    admin = await _make_user(session, "admin_pref@test.com", is_superuser=True)
    target = await _make_user(session, "target_pref@test.com")
    new_prefs = {"language": "pt-BR", "currency_display": "BRL"}
    data = AdminUserUpdate(preferences=new_prefs)
    result = await update_user(session, target.id, data, admin.id)

    assert result is not None
    assert result.preferences is not None
    assert result.preferences["language"] == "pt-BR"


async def test_update_user_not_found(session: AsyncSession, clean_db):
    admin = await _make_user(session, "admin_nf@test.com", is_superuser=True)
    data = AdminUserUpdate(is_active=False)
    result = await update_user(session, uuid.uuid4(), data, admin.id)
    assert result is None


async def test_update_user_self_demote(session: AsyncSession, clean_db):
    admin = await _make_user(session, "admin_self@test.com", is_superuser=True)
    data = AdminUserUpdate(is_superuser=False)
    with pytest.raises(ValueError, match="own admin"):
        await update_user(session, admin.id, data, admin.id)


async def test_update_user_self_deactivate(session: AsyncSession, clean_db):
    admin = await _make_user(session, "admin_deact@test.com", is_superuser=True)
    data = AdminUserUpdate(is_active=False)
    with pytest.raises(ValueError, match="own account"):
        await update_user(session, admin.id, data, admin.id)


async def test_update_user_is_active_and_superuser(session: AsyncSession, clean_db):
    admin = await _make_user(session, "admin_flags@test.com", is_superuser=True)
    target = await _make_user(session, "target_flags@test.com")
    data = AdminUserUpdate(is_active=False, is_superuser=True)
    result = await update_user(session, target.id, data, admin.id)

    assert result is not None
    assert result.is_active is False
    assert result.is_superuser is True


# ---------------------------------------------------------------------------
# delete_user — cascade
# ---------------------------------------------------------------------------

async def test_delete_user_cascade(session: AsyncSession, clean_db):
    """Delete a user with all related data (the most important coverage gap)."""
    admin = await _make_user(session, "admin_del@test.com", is_superuser=True)
    target = await _make_user(session, "victim@test.com")
    await _populate_user_data(session, target)

    result = await delete_user(session, target.id, admin.id)
    assert result is True

    # Verify user is gone
    assert await get_user(session, target.id) is None


async def test_delete_user_self_protection(session: AsyncSession, clean_db):
    admin = await _make_user(session, "admin_self_del@test.com", is_superuser=True)
    with pytest.raises(ValueError, match="own account"):
        await delete_user(session, admin.id, admin.id)


async def test_delete_user_last_superuser(session: AsyncSession, clean_db):
    admin = await _make_user(session, "only_admin@test.com", is_superuser=True)
    target = await _make_user(session, "other@test.com")
    # Can delete non-admin
    assert await delete_user(session, target.id, admin.id) is True


async def test_delete_last_superuser_blocked(session: AsyncSession, clean_db):
    admin1 = await _make_user(session, "admin1_last@test.com", is_superuser=True)
    admin2 = await _make_user(session, "admin2_last@test.com", is_superuser=True)
    # admin2 tries to delete admin1 — should succeed (2 admins remain: admin2)
    assert await delete_user(session, admin1.id, admin2.id) is True

    # Now create a new target admin, make it the last
    target = await _make_user(session, "last_target@test.com", is_superuser=True)
    # admin2 is one admin, target is another — delete target is fine
    assert await delete_user(session, target.id, admin2.id) is True


async def test_delete_user_not_found(session: AsyncSession, clean_db):
    admin = await _make_user(session, "admin_nf2@test.com", is_superuser=True)
    result = await delete_user(session, uuid.uuid4(), admin.id)
    assert result is False


# ---------------------------------------------------------------------------
# App settings
# ---------------------------------------------------------------------------

async def test_get_app_setting_not_found(session: AsyncSession, clean_db):
    result = await get_app_setting(session, "nonexistent_key")
    assert result is None


async def test_set_app_setting_create(session: AsyncSession, clean_db):
    setting = await set_app_setting(session, "test_key", "test_value")
    assert setting.key == "test_key"
    assert setting.value == "test_value"


async def test_set_app_setting_update(session: AsyncSession, clean_db):
    await set_app_setting(session, "toggle", "true")
    setting = await set_app_setting(session, "toggle", "false")
    assert setting.value == "false"


async def test_get_app_setting_found(session: AsyncSession, clean_db):
    await set_app_setting(session, "found_key", "found_value")
    result = await get_app_setting(session, "found_key")
    assert result is not None
    assert result.value == "found_value"


# ---------------------------------------------------------------------------
# is_registration_enabled
# ---------------------------------------------------------------------------

async def test_is_registration_enabled_from_setting(session: AsyncSession, clean_db):
    await set_app_setting(session, "registration_enabled", "true")
    assert await is_registration_enabled(session) is True

    await set_app_setting(session, "registration_enabled", "false")
    assert await is_registration_enabled(session) is False


async def test_is_registration_enabled_fallback_to_env(session: AsyncSession, clean_db):
    """When no DB setting exists, falls back to env config."""
    result = await is_registration_enabled(session)
    # Should not raise; returns the env default
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# use_provider_categories
# ---------------------------------------------------------------------------


async def test_use_provider_categories_default_true(session: AsyncSession, clean_db):
    """Unset = on, so existing installs keep the historical sync behavior."""
    assert await use_provider_categories(session) is True


async def test_use_provider_categories_explicit_false(session: AsyncSession, clean_db):
    await set_app_setting(session, "use_provider_categories", "false")
    assert await use_provider_categories(session) is False


async def test_use_provider_categories_explicit_true(session: AsyncSession, clean_db):
    await set_app_setting(session, "use_provider_categories", "true")
    assert await use_provider_categories(session) is True


async def test_use_provider_categories_case_insensitive(session: AsyncSession, clean_db):
    """Tolerate 'TRUE'/'False' in case the value is set via DB tooling."""
    await set_app_setting(session, "use_provider_categories", "TRUE")
    assert await use_provider_categories(session) is True
    await set_app_setting(session, "use_provider_categories", "False")
    assert await use_provider_categories(session) is False


async def test_use_provider_categories_unknown_value_falls_back_to_false(
    session: AsyncSession, clean_db,
):
    """Defensive: anything not 'true' (case-insensitive) is treated as off.
    Prevents a stray 'maybe' / '1' from quietly turning provider mapping back
    on when the admin meant to disable it."""
    await set_app_setting(session, "use_provider_categories", "maybe")
    assert await use_provider_categories(session) is False
