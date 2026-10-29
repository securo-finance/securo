"""A provider 429 during a bank sync (issues #805 and #1054).

PSD2 caps unattended access at a few calls per day per account, so an
Enable Banking sync often gets HTTP 429 for one account while the others are
fine. The sync must:
- skip only the throttled account and keep what it imported for the others
  (no rollback of the whole run), reporting the run as "partial";
- not advance last_sync_at while an account was skipped;
- back off: the connection gets a cooldown (Retry-After when the provider
  sends one, otherwise a default) and the scheduled sync leaves it alone
  until the cooldown ends, instead of retrying every hour;
- count throttled runs apart from synced ones in the scheduled task.
"""

import contextlib
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.utils import format_datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.providers.base import (
    AccountData,
    ProviderRateLimited,
    TransactionData,
)
from app.providers.enable_banking import EnableBankingProvider, _parse_retry_after
from app.services.connection_service import rate_limited_until, sync_connection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_connection(
    session: AsyncSession,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    token: str = "fake",
    last_sync_at: datetime | None = None,
) -> BankConnection:
    conn = BankConnection(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        provider="test",
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name="Rate Limit Bank",
        credentials={"token": token},
        status="active",
        last_sync_at=last_sync_at or datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()
    await session.refresh(conn)
    return conn


def _mock_provider_two_accounts(
    *, rate_limit_account: str | None = None,
    rate_limit_transactions_for: str | None = None,
    rate_limit_session: bool = False,
) -> AsyncMock:
    """A mock provider with two healthy accounts plus configurable 429s.

    rate_limit_account: account whose details/balances 429 in get_accounts.
    rate_limit_transactions_for: account whose transactions 429.
    rate_limit_session: the whole get_accounts call 429s (consent level).
    """
    provider = AsyncMock()
    provider.refresh_credentials = AsyncMock(
        side_effect=lambda creds: dict(creds)
    )
    provider.get_institution_logo = AsyncMock(return_value=None)
    provider.trigger_refresh = AsyncMock(return_value="skipped")

    def _accounts(creds):
        if rate_limit_session:
            raise ProviderRateLimited("Enable Banking GET /sessions/x -> 429")
        result = []
        for ext_id in ("rl-acc-a", "rl-acc-b"):
            if ext_id == rate_limit_account:
                continue
            result.append(
                AccountData(
                    external_id=ext_id,
                    name=f"Account {ext_id[-1].upper()}",
                    type="checking",
                    balance=Decimal("100"),
                    currency="BRL",
                )
            )
        return result

    provider.get_accounts = AsyncMock(side_effect=_accounts)

    def _transactions(creds, account_external_id, since=None, payee_source="auto"):
        if account_external_id == rate_limit_transactions_for:
            raise ProviderRateLimited(
                f"Enable Banking GET /accounts/{account_external_id}/transactions -> 429"
            )
        return [
            TransactionData(
                external_id=f"rl-tx-{account_external_id}",
                description="MARKET GROCERY",
                amount=Decimal("42.50"),
                date=date.today(),
                type="debit",
                currency="BRL",
            )
        ]

    provider.get_transactions = AsyncMock(side_effect=_transactions)
    return provider


_SYNC_PATCHES = (
    "app.services.connection_service.detect_transfer_pairs",
    "app.services.connection_service.stamp_primary_amount",
    "app.services.connection_service.apply_rules_to_transaction",
)


@contextmanager
def _sync_patches(provider: AsyncMock):
    """Patch get_provider to `provider` and stub the transfer/rule helpers."""
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch("app.services.connection_service.get_provider", return_value=provider)
        )
        for target in _SYNC_PATCHES:
            stack.enter_context(patch(target, new_callable=AsyncMock))
        yield


# ---------------------------------------------------------------------------
# sync_connection
# ---------------------------------------------------------------------------


def _cooldown_seconds(conn: BankConnection) -> float:
    until = rate_limited_until(conn.settings)
    assert until is not None, conn.settings
    return (until - datetime.now(timezone.utc)).total_seconds()


@pytest.mark.asyncio
async def test_full_sync_advances_checkpoint_and_clears_cooldown(
    session: AsyncSession, test_user, test_workspace
):
    conn = await _make_connection(session, test_user.id, test_workspace.id)
    conn.settings = {"rate_limited_until": "2026-01-01T00:00:00+00:00", "payee_source": "auto"}
    await session.commit()
    before = conn.last_sync_at
    provider = _mock_provider_two_accounts()

    with _sync_patches(provider):
        result_conn, _, outcome = await sync_connection(
            session, conn.id, test_workspace.id, test_user.id
        )

    assert outcome == "refreshed"
    assert before is not None and result_conn.last_sync_at is not None
    assert result_conn.last_sync_at > before
    assert result_conn.settings == {"payee_source": "auto"}


@pytest.mark.asyncio
async def test_transactions_429_keeps_other_accounts_and_backs_off(
    session: AsyncSession, test_user, test_workspace
):
    """One account's transactions 429: the other account's data is kept."""
    conn = await _make_connection(session, test_user.id, test_workspace.id)
    before = conn.last_sync_at
    provider = _mock_provider_two_accounts(rate_limit_transactions_for="rl-acc-a")

    with _sync_patches(provider):
        result_conn, _, outcome = await sync_connection(
            session, conn.id, test_workspace.id, test_user.id
        )

    assert outcome == "partial"
    assert result_conn.status == "active"
    external_ids = set(
        (
            await session.execute(
                select(Transaction.external_id).where(
                    Transaction.external_id.like("rl-tx-%")
                )
            )
        ).scalars()
    )
    assert external_ids == {"rl-tx-rl-acc-b"}
    # The throttled account's balance did come through; its opening balance
    # is reconciled so the ledger still adds up to it.
    throttled = (
        await session.execute(
            select(Account).where(
                Account.connection_id == conn.id, Account.external_id == "rl-acc-a"
            )
        )
    ).scalar_one()
    ledger = (
        await session.execute(
            select(
                func.sum(
                    case(
                        (Transaction.type == "credit", Transaction.amount),
                        else_=-Transaction.amount,
                    )
                )
            ).where(
                Transaction.account_id == throttled.id
            )
        )
    ).scalar_one()
    assert ledger == throttled.balance
    # Checkpoint kept for the skipped account, default cooldown set.
    assert result_conn.last_sync_at == before
    assert 5.9 * 3600 < _cooldown_seconds(result_conn) <= 6 * 3600


@pytest.mark.asyncio
async def test_accounts_429_leaves_throttled_row_and_honors_retry_after(
    session: AsyncSession, test_user, test_workspace
):
    """A 429 on an account's details or balance: the row is left as it was."""
    conn = await _make_connection(session, test_user.id, test_workspace.id)
    with _sync_patches(_mock_provider_two_accounts()):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)
    stored = (
        await session.execute(
            select(Account).where(
                Account.connection_id == conn.id, Account.external_id == "rl-acc-a"
            )
        )
    ).scalar_one()
    stored.balance = Decimal("77")
    await session.commit()
    await session.refresh(conn)
    before = conn.last_sync_at

    provider = _mock_provider_two_accounts(rate_limit_account="rl-acc-a")
    provider.rate_limited_account_ids = {"rl-acc-a"}
    provider.rate_limit_retry_after = 1800.0
    with _sync_patches(provider):
        result_conn, _, outcome = await sync_connection(
            session, conn.id, test_workspace.id, test_user.id
        )

    assert outcome == "partial"
    await session.refresh(stored)
    assert stored.balance == Decimal("77")
    assert result_conn.last_sync_at == before
    assert 1700 < _cooldown_seconds(result_conn) <= 1800


@pytest.mark.asyncio
async def test_session_429_sets_cooldown_from_retry_after(
    session: AsyncSession, test_user, test_workspace
):
    conn = await _make_connection(session, test_user.id, test_workspace.id)
    before = conn.last_sync_at
    provider = _mock_provider_two_accounts()
    provider.get_accounts = AsyncMock(
        side_effect=ProviderRateLimited("429", retry_after=600.0)
    )

    with _sync_patches(provider):
        result_conn, merged, outcome = await sync_connection(
            session, conn.id, test_workspace.id, test_user.id
        )

    assert (outcome, merged, result_conn.status) == ("rate_limited", 0, "active")
    assert result_conn.last_sync_at == before
    assert 500 < _cooldown_seconds(result_conn) <= 600


@pytest.mark.asyncio
async def test_retry_after_is_bounded(
    session: AsyncSession, test_user, test_workspace
):
    conn = await _make_connection(session, test_user.id, test_workspace.id)
    provider = _mock_provider_two_accounts(rate_limit_session=True)
    provider.get_accounts = AsyncMock(
        side_effect=ProviderRateLimited("429", retry_after=30 * 86400.0)
    )

    with _sync_patches(provider):
        result_conn, _, _ = await sync_connection(
            session, conn.id, test_workspace.id, test_user.id
        )

    assert _cooldown_seconds(result_conn) <= 24 * 3600


async def _two_same_day_pix(session, conn, user_id, workspace_id, external_id):
    """Two real same-day payments of the same amount on one account, one of
    them already paired as a transfer: the shape the phantom heuristic would
    mistake for a duplicate when it has no live ids to tell them apart."""
    from app.services.account_service import sync_opening_balance_for_connected_account

    account = (
        await session.execute(
            select(Account).where(
                Account.connection_id == conn.id, Account.external_id == external_id
            )
        )
    ).scalar_one()
    for ext, pair in (("pix-paired", uuid.uuid4()), ("pix-unpaired", None)):
        session.add(Transaction(
            id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id,
            account_id=account.id, external_id=f"{external_id}-{ext}",
            description="PIX SENT", amount=Decimal("50.00"), date=date.today(),
            type="debit", status="posted", source="sync", currency="BRL",
            transfer_pair_id=pair, created_at=datetime.now(timezone.utc),
        ))
    await session.flush()
    await sync_opening_balance_for_connected_account(session, account)
    account_id = account.id
    await session.commit()
    return account_id


async def _ledger_rows(session, account_id):
    rows = (
        await session.execute(
            select(Transaction.id, Transaction.amount, Transaction.type).where(
                Transaction.account_id == account_id
            )
        )
    ).all()
    return sorted((str(r.id), r.amount, r.type) for r in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "throttled_on", ["transactions", "details", "details_after_reauth"]
)
async def test_throttled_account_is_left_out_of_phantom_cleanup(
    session: AsyncSession, test_user, test_workspace, throttled_on
):
    """A throttled account has no live ids this run: the phantom cleanup must
    skip it rather than delete a real unpaired row and re-reconcile."""
    conn = await _make_connection(session, test_user.id, test_workspace.id)
    with _sync_patches(_mock_provider_two_accounts()):
        await sync_connection(session, conn.id, test_workspace.id, test_user.id)
    account_id = await _two_same_day_pix(
        session, conn, test_user.id, test_workspace.id, "rl-acc-a"
    )
    before = await _ledger_rows(session, account_id)

    if throttled_on == "transactions":
        provider = _mock_provider_two_accounts(rate_limit_transactions_for="rl-acc-a")
    else:
        provider = _mock_provider_two_accounts(rate_limit_account="rl-acc-a")
        # After a reauthorisation the provider reports the throttled account
        # under its new session uid, while the stored row keeps the old one.
        provider.rate_limited_account_ids = (
            {"rl-acc-a"} if throttled_on == "details" else {"rl-acc-a-new-uid"}
        )
    with _sync_patches(provider):
        _, _, outcome = await sync_connection(
            session, conn.id, test_workspace.id, test_user.id
        )

    assert outcome == "partial"
    assert await _ledger_rows(session, account_id) == before


@pytest.mark.asyncio
async def test_huge_retry_after_does_not_overflow(
    session: AsyncSession, test_user, test_workspace
):
    """A finite but absurd Retry-After is capped, not an OverflowError."""
    conn = await _make_connection(session, test_user.id, test_workspace.id)
    provider = _mock_provider_two_accounts()
    provider.get_accounts = AsyncMock(
        side_effect=ProviderRateLimited("429", retry_after=1e15)
    )

    with _sync_patches(provider):
        result_conn, _, outcome = await sync_connection(
            session, conn.id, test_workspace.id, test_user.id
        )

    assert outcome == "rate_limited"
    assert 23.9 * 3600 < _cooldown_seconds(result_conn) <= 24 * 3600


@pytest.mark.asyncio
async def test_sync_endpoint_reports_partial_outcome(
    client, auth_headers, session: AsyncSession, test_user, test_workspace
):
    conn = await _make_connection(session, test_user.id, test_workspace.id)
    provider = _mock_provider_two_accounts(rate_limit_transactions_for="rl-acc-a")

    with _sync_patches(provider):
        response = await client.post(
            f"/api/connections/{conn.id}/sync", headers=auth_headers
        )

    assert response.status_code == 200
    assert response.json()["sync_outcome"] == "partial"


# ---------------------------------------------------------------------------
# Enable Banking provider
# ---------------------------------------------------------------------------


def _eb_request_mock(
    *,
    uids: list[str],
    details_429: dict[str, float | None] | None = None,
    balances_429: dict[str, float | None] | None = None,
):
    details_429 = details_429 or {}
    balances_429 = balances_429 or {}

    def _request(method, path, *, params=None, json_body=None):
        if path.startswith("/sessions/"):
            return {
                "accounts": list(uids),
                "accounts_data": [
                    {"uid": u, "identification_hash": f"hash-{u}"} for u in uids
                ],
            }
        uid = path.split("/")[2]
        if path.endswith("/details"):
            if uid in details_429:
                raise ProviderRateLimited(path, retry_after=details_429[uid])
            return {
                "uid": uid,
                "display_name": f"Account {uid}",
                "cash_account_type": "CACC",
                "currency": "EUR",
            }
        if path.endswith("/balances"):
            if uid in balances_429:
                raise ProviderRateLimited(path, retry_after=balances_429[uid])
            return {
                "balances": [
                    {
                        "balance_type": "CLBD",
                        "balance_amount": {"amount": "100.00", "currency": "EUR"},
                    }
                ]
            }
        raise AssertionError(f"unexpected request: {method} {path}")

    return AsyncMock(side_effect=_request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uids", [["uid-a", "uid-b", "uid-c"], ["uid-c", "uid-b", "uid-a"]]
)
async def test_enable_banking_get_accounts_skips_throttled_accounts(uids):
    provider = EnableBankingProvider()
    provider._request = _eb_request_mock(
        uids=uids, details_429={"uid-a": 60.0}, balances_429={"uid-b": None}
    )

    accounts = await provider.get_accounts({"session_id": "sess-1"})

    assert [a.external_id for a in accounts] == ["uid-c"]
    assert accounts[0].balance == Decimal("100.00")
    assert provider.rate_limited_account_ids == {"uid-a", "uid-b"}
    assert provider.rate_limit_retry_after == 60.0


@pytest.mark.asyncio
async def test_enable_banking_session_429_propagates():
    provider = EnableBankingProvider()
    provider._request = AsyncMock(side_effect=ProviderRateLimited("429"))

    with pytest.raises(ProviderRateLimited):
        await provider.get_accounts({"session_id": "sess-1"})


@pytest.mark.parametrize(
    ("header", "expected"),
    [(None, None), ("120", 120.0), ("-5", 0.0), ("soon", None), ("inf", None)],
)
def test_parse_retry_after_seconds(header, expected):
    assert _parse_retry_after(header) == expected


def test_parse_retry_after_http_date():
    when = datetime.now(timezone.utc) + timedelta(hours=1)
    seconds = _parse_retry_after(format_datetime(when, usegmt=True))
    assert seconds is not None and 3500 < seconds <= 3600


# ---------------------------------------------------------------------------
# Scheduled task
# ---------------------------------------------------------------------------


async def _run_sync_all(session: AsyncSession, provider: AsyncMock) -> dict:
    from app.tasks.sync_tasks import _sync_all

    with (
        _sync_patches(provider),
        patch("app.tasks.sync_tasks._make_session_maker") as maker,
        patch(
            "app.tasks.sync_tasks.get_timezone",
            new=AsyncMock(return_value=timezone.utc),
        ),
    ):
        engine = AsyncMock()
        maker.return_value = (engine, _SessionMakerProxy(session))
        return await _sync_all()


@pytest.mark.asyncio
async def test_sync_all_counts_throttled_runs_and_skips_them_until_cooldown_ends(
    session: AsyncSession, test_user, test_workspace
):
    stale = datetime.now(timezone.utc) - timedelta(hours=5)
    await _make_connection(
        session, test_user.id, test_workspace.id, token="tok-healthy", last_sync_at=stale
    )
    throttled = await _make_connection(
        session, test_user.id, test_workspace.id, token="tok-throttled", last_sync_at=stale
    )
    provider = _mock_provider_two_accounts()

    def _accounts(creds):
        if creds.get("token") == "tok-throttled":
            raise ProviderRateLimited("429")
        return []

    provider.get_accounts = AsyncMock(side_effect=_accounts)

    first = await _run_sync_all(session, provider)
    assert (first["synced"], first["rate_limited"], first["backing_off"]) == (1, 1, 0)

    # An hour later the throttled connection is still stale, but in cooldown:
    # the scheduler must not call the provider for it again.
    provider.get_accounts.reset_mock()
    second = await _run_sync_all(session, provider)
    assert (second["rate_limited"], second["backing_off"]) == (0, 1)
    assert provider.get_accounts.await_count == 0

    # Once the cooldown is over it is synced again.
    await session.refresh(throttled)
    throttled.settings = {
        **(throttled.settings or {}),
        "rate_limited_until": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    }
    await session.commit()
    third = await _run_sync_all(session, provider)
    assert (third["rate_limited"], third["backing_off"]) == (1, 0)


class _SessionMakerProxy:
    """Yields the test session so _sync_all's rows come from the test DB."""

    def __init__(self, session: AsyncSession):
        self._session = session

    def __call__(self):
        return _SessionCtx(self._session)


class _SessionCtx:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False
