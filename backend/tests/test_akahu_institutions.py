from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.providers.akahu import AkahuProvider
from app.providers.base import ConnectionData
from app.services.account_service import get_account_with_balances
from app.services.connection_service import handle_oauth_callback, sync_connection


@pytest.mark.parametrize("scenario", ["oauth", "sync_new", "sync_existing", "sync_custom"])
async def test_akahu_institution_icons_preserve_account_names(
    session, test_user, test_workspace, scenario
):
    accounts, institution_name, logo_url = AkahuProvider._parse_accounts(
        {
            "items": [
                {
                    "_id": "acc_checking",
                    "name": "Every day",
                    "type": "CHECKING",
                    "balance": {"current": 100, "currency": "NZD"},
                    "connection": {
                        "_id": "conn_bank",
                        "name": "Kiwibank",
                        "logo": "https://example.com/kiwibank.png",
                    },
                },
                {
                    "_id": "acc_wallet",
                    "name": "Sharesies NZD Wallet",
                    "type": "WALLET",
                    "balance": {"current": 50, "currency": "NZD"},
                    "connection": {
                        "_id": "conn_broker",
                        "name": "Sharesies",
                        "logo": "https://example.com/sharesies.png",
                    },
                },
            ]
        }
    )
    provider = AsyncMock()
    provider.handle_oauth_callback.return_value = ConnectionData(
        external_id="user_test",
        institution_name=institution_name,
        logo_url=logo_url,
        credentials={"token": "fake"},
        accounts=accounts,
    )
    provider.refresh_credentials.return_value = {"token": "fake"}
    provider.get_accounts.return_value = accounts
    provider.get_transactions.return_value = []
    provider.get_holdings.return_value = []
    provider.get_bills.return_value = []
    expected_display_name = "My chosen name" if scenario == "sync_custom" else None
    original_ids = {}

    with patch("app.services.connection_service.get_provider", return_value=provider):
        if scenario == "oauth":
            connection = await handle_oauth_callback(
                session, test_workspace.id, test_user.id, "auth-code", provider_name="akahu"
            )
        else:
            connection = BankConnection(
                user_id=test_user.id,
                workspace_id=test_workspace.id,
                provider="akahu",
                external_id="user_test",
                institution_name=institution_name,
                logo_url=logo_url,
                credentials={"token": "fake"},
                status="active",
            )
            session.add(connection)
            await session.flush()
            if scenario in {"sync_existing", "sync_custom"}:
                for data in accounts:
                    account = Account(
                        user_id=test_user.id,
                        workspace_id=test_workspace.id,
                        connection_id=connection.id,
                        external_id=data.external_id,
                        name=data.name,
                        display_name=expected_display_name,
                        type=data.type,
                        currency=data.currency,
                        balance=Decimal("0"),
                    )
                    session.add(account)
                    await session.flush()
                    original_ids[data.external_id] = account.id
            await session.commit()
            await sync_connection(session, connection.id, test_workspace.id, test_user.id)

        await sync_connection(session, connection.id, test_workspace.id, test_user.id)

    stored = (
        (await session.execute(select(Account).where(Account.connection_id == connection.id)))
        .scalars()
        .all()
    )
    assert len(stored) == len(accounts)
    by_external_id = {account.external_id: account for account in stored}
    for data in accounts:
        account = by_external_id[data.external_id]
        if data.external_id in original_ids:
            assert account.id == original_ids[data.external_id]
        assert account.institution_id is not None
        payload = await get_account_with_balances(session, account.id, test_workspace.id)
        assert payload is not None
        assert payload["name"] == data.name
        assert payload["display_name"] == expected_display_name
        assert payload["institution_name"] == data.institution_name
        assert payload["institution_logo_url"] == data.institution_logo_url
