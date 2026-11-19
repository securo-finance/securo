import csv
import io
import uuid
import zipfile
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.transaction_category_line import TransactionCategoryLine
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember

START = date(2024, 1, 1)
END = date(2024, 12, 31)
EXPORT_URL = "/api/export/accountant"


def _headers(auth_headers: dict, workspace: Workspace) -> dict:
    return {**auth_headers, "X-Workspace-Id": str(workspace.id)}


async def _make_business(session: AsyncSession, workspace: Workspace, name: str) -> Workspace:
    workspace.kind = "business"
    workspace.name = name
    await session.commit()
    return workspace


async def _add_rate(
    session: AsyncSession,
    day: date,
    currency: str,
    rate: str,
    *,
    base: str = "USD",
) -> None:
    """Store one FX rate, as the daily sync would have."""
    session.add(
        FxRate(
            id=uuid.uuid4(),
            base_currency=base,
            quote_currency=currency,
            date=day,
            rate=Decimal(rate),
            source="test",
        )
    )
    await session.commit()


async def _add_transaction(
    session: AsyncSession,
    user: User,
    workspace: Workspace,
    account: Account,
    *,
    amount,
    tx_date: date = date(2024, 1, 10),
    tx_type: str = "debit",
    category_id=None,
    description: str = "Transaction",
    currency: str = "BRL",
    amount_primary=None,
    status: str = "posted",
    transfer_pair_id=None,
    payee=None,
    lines: list[tuple[uuid.UUID, str]] | None = None,
) -> Transaction:
    transaction = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        account_id=account.id,
        category_id=category_id,
        description=description,
        amount=Decimal(str(amount)),
        currency=currency,
        date=tx_date,
        type=tx_type,
        source="manual",
        status=status,
        has_category_lines=bool(lines),
        transfer_pair_id=transfer_pair_id,
        payee=payee,
        amount_primary=(
            Decimal(str(amount_primary)) if amount_primary is not None else None
        ),
    )
    session.add(transaction)
    await session.flush()
    for position, (line_category_id, line_amount) in enumerate(lines or []):
        session.add(
            TransactionCategoryLine(
                id=uuid.uuid4(),
                workspace_id=workspace.id,
                transaction_id=transaction.id,
                category_id=line_category_id,
                amount=Decimal(str(line_amount)),
                share_type="exact",
                position=position,
            )
        )
    await session.commit()
    return transaction


def _open_zip(response) -> zipfile.ZipFile:
    assert response.status_code == 200, response.text
    return zipfile.ZipFile(io.BytesIO(response.content))


def _read_csv(archive: zipfile.ZipFile, name: str) -> tuple[bytes, list[list[str]]]:
    raw = archive.read(name)
    assert raw.startswith(b"\xef\xbb\xbf"), "the CSV must start with a UTF-8 BOM"
    assert b"\r\n" in raw, "the CSV must use CRLF line endings"
    assert b"\n" not in raw.replace(b"\r\n", b""), "no bare LF line endings"
    text = raw.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=";"))
    return raw, rows


@pytest.mark.asyncio
async def test_zip_holds_the_two_csv_files_with_the_expected_name(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_workspace: Workspace,
):
    await _make_business(session, test_workspace, "Padaria São João")

    response = await client.get(
        EXPORT_URL,
        params={"start_date": START.isoformat(), "end_date": END.isoformat()},
        headers=_headers(auth_headers, test_workspace),
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert (
        'filename="securo-accountant-padaria-sao-joao-2024-01-01_2024-12-31.zip"'
        in response.headers["content-disposition"]
    )

    with _open_zip(response) as archive:
        assert set(archive.namelist()) == {"transactions.csv", "profit-and-loss.csv"}


@pytest.mark.asyncio
async def test_transactions_csv_has_one_row_per_category_line(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
    test_categories: list[Category],
):
    await _make_business(session, test_workspace, "Padaria São João")

    await _add_transaction(
        session,
        test_user,
        test_workspace,
        test_account,
        amount="150.00",
        tx_date=date(2024, 1, 5),
        tx_type="credit",
        category_id=test_categories[2].id,
        description="PIX RECEBIDO",
        payee="João da Silva",
    )
    await _add_transaction(
        session,
        test_user,
        test_workspace,
        test_account,
        amount="100.00",
        tx_date=date(2024, 1, 10),
        description="Mercado; centro",
        lines=[(test_categories[0].id, "60.00"), (test_categories[1].id, "40.00")],
    )

    response = await client.get(
        EXPORT_URL,
        params={"start_date": START.isoformat(), "end_date": END.isoformat()},
        headers=_headers(auth_headers, test_workspace),
    )

    with _open_zip(response) as archive:
        raw, rows = _read_csv(archive, "transactions.csv")

    assert rows[0] == [
        "Date",
        "Account",
        "Amount",
        "Currency",
        "Category",
        "Payee",
        "Document",
        "Direction",
        "Status",
    ]
    assert len(rows) == 4  # header plus one plain row and two split rows
    assert b'"Mercado; centro"' in raw

    by_amount = {row[2]: row for row in rows[1:]}
    assert set(by_amount) == {"150,00", "-60,00", "-40,00"}

    income = by_amount["150,00"]
    assert income[0] == "05/01/2024"
    assert income[1] == test_account.name
    assert income[3] == "BRL"
    assert income[4] == "Receita"
    assert income[5] == "João da Silva"
    assert income[6] == "PIX RECEBIDO"
    assert income[7] == "in"
    assert income[8] == "posted"

    assert by_amount["-60,00"][4] == "Alimentação"
    assert by_amount["-60,00"][7] == "out"
    assert by_amount["-40,00"][4] == "Transporte"
    assert by_amount["-40,00"][7] == "out"


@pytest.mark.asyncio
async def test_profit_and_loss_groups_by_category_and_excludes_transfers(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
    test_categories: list[Category],
):
    await _make_business(session, test_workspace, "Padaria São João")

    transfer_category = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Transferência",
        treat_as_transfer=True,
    )
    session.add(transfer_category)
    await session.commit()

    # Income, a split expense, a foreign-currency expense and two transfer
    # legs that must stay out of the P&L.
    await _add_transaction(
        session,
        test_user,
        test_workspace,
        test_account,
        amount="1000.00",
        tx_date=date(2024, 2, 1),
        tx_type="credit",
        category_id=test_categories[2].id,
        description="SALARIO",
    )
    await _add_transaction(
        session,
        test_user,
        test_workspace,
        test_account,
        amount="100.00",
        tx_date=date(2024, 2, 2),
        description="MERCADO",
        lines=[(test_categories[0].id, "60.00"), (test_categories[1].id, "40.00")],
    )
    # The stored amount_primary belongs to the saver's display currency and
    # must not reach the P&L; the USD line converts at the rate of its own day.
    await _add_rate(session, date(2024, 2, 3), "BRL", "5.0")
    await _add_transaction(
        session,
        test_user,
        test_workspace,
        test_account,
        amount="50.00",
        tx_date=date(2024, 2, 3),
        category_id=test_categories[1].id,
        currency="USD",
        amount_primary="275.00",
        description="SOFTWARE",
    )
    transfer_pair_id = uuid.uuid4()
    await _add_transaction(
        session,
        test_user,
        test_workspace,
        test_account,
        amount="500.00",
        tx_date=date(2024, 2, 4),
        category_id=test_categories[1].id,
        description="TRANSFER OUT",
        transfer_pair_id=transfer_pair_id,
    )
    await _add_transaction(
        session,
        test_user,
        test_workspace,
        test_account,
        amount="500.00",
        tx_date=date(2024, 2, 4),
        tx_type="credit",
        category_id=test_categories[1].id,
        description="TRANSFER IN",
        transfer_pair_id=transfer_pair_id,
    )
    await _add_transaction(
        session,
        test_user,
        test_workspace,
        test_account,
        amount="200.00",
        tx_date=date(2024, 2, 5),
        category_id=transfer_category.id,
        description="INVESTMENT",
    )

    response = await client.get(
        EXPORT_URL,
        params={"start_date": START.isoformat(), "end_date": END.isoformat()},
        headers=_headers(auth_headers, test_workspace),
    )

    with _open_zip(response) as archive:
        _, transaction_rows = _read_csv(archive, "transactions.csv")
        _, rows = _read_csv(archive, "profit-and-loss.csv")

    # Both legs of the transfer stay in the transaction ledger even though
    # neither counts toward the P&L.
    ledger = {(row[6], row[2]) for row in transaction_rows[1:]}
    assert ("TRANSFER OUT", "-500,00") in ledger
    assert ("TRANSFER IN", "500,00") in ledger

    assert rows[0] == ["Section", "Category", "Amount", "Currency"]
    buckets = {(row[0], row[1]): row[2] for row in rows[1:] if row[0] != "result"}

    assert buckets[("income", "Receita")] == "1000,00"
    assert buckets[("expense", "Alimentação")] == "60,00"
    # 40 from the split line plus 50 USD at 5,0 on the row's date. The stored
    # amount_primary of 275 is the display currency and is not used here.
    assert buckets[("expense", "Transporte")] == "290,00"
    assert all(label != "Transferência" for _, label in buckets)
    assert len(buckets) == 3
    assert all(row[3] == "BRL" for row in rows[1:])

    result = rows[-1]
    assert result == ["result", "", "650,00", "BRL"]


@pytest.mark.asyncio
async def test_profit_and_loss_is_in_the_workspace_currency_not_the_exporters_display(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
    test_categories: list[Category],
):
    """The P&L follows the workspace currency, whatever the exporter displays.

    A BRL business exported by someone whose display currency is USD used to
    come out in USD: the P&L summed ``amount_primary``, which is stamped in
    the display currency of whoever saved the row. Two people with different
    preferences exported different numbers for the same books (#240).
    """
    test_user.preferences = {
        **(test_user.preferences or {}),
        "currency_display": "USD",
    }
    await _make_business(session, test_workspace, "R1614 Biz")
    await _add_rate(session, date(2024, 11, 3), "BRL", "5.0")
    await _add_transaction(
        session,
        test_user,
        test_workspace,
        test_account,
        amount="500.00",
        tx_date=date(2024, 11, 3),
        tx_type="credit",
        category_id=test_categories[2].id,
        # The same 500 BRL row stamped for a USD display: 100,00 USD.
        amount_primary="100.00",
        description="R1614 Venda Nov",
    )

    response = await client.get(
        EXPORT_URL,
        params={"start_date": "2024-11-01", "end_date": "2024-11-19"},
        headers=_headers(auth_headers, test_workspace),
    )

    with _open_zip(response) as archive:
        _, rows = _read_csv(archive, "profit-and-loss.csv")

    assert ["income", "Receita", "500,00", "BRL"] in rows
    assert rows[-1] == ["result", "", "500,00", "BRL"]


@pytest.mark.asyncio
async def test_foreign_line_without_a_rate_is_reported_instead_of_summed(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
    test_categories: list[Category],
):
    """A line with no stored rate keeps its own currency and stays out.

    Reading it 1:1 would mix currencies in one total (250,25 BRL plus 100 USD
    came out as 350,25). The file names the currency and leaves the line out
    of the result instead (#240).
    """
    await _make_business(session, test_workspace, "Padaria São João")
    await _add_transaction(
        session,
        test_user,
        test_workspace,
        test_account,
        amount="500.00",
        tx_date=date(2024, 10, 1),
        tx_type="credit",
        category_id=test_categories[2].id,
        description="VENDA OUT",
    )
    await _add_transaction(
        session,
        test_user,
        test_workspace,
        test_account,
        amount="100.00",
        tx_date=date(2024, 10, 2),
        category_id=test_categories[0].id,
        currency="USD",
        description="SEM COTACAO",
    )

    response = await client.get(
        EXPORT_URL,
        params={"start_date": "2024-10-01", "end_date": "2024-10-31"},
        headers=_headers(auth_headers, test_workspace),
    )

    with _open_zip(response) as archive:
        _, rows = _read_csv(archive, "profit-and-loss.csv")

    assert ["income", "Receita", "500,00", "BRL"] in rows
    # Reported in its own currency, flagged as unconverted, not in the total.
    assert ["unconverted_expense", "Alimentação", "100,00", "USD"] in rows
    assert rows[-1] == ["result", "", "500,00", "BRL"]


@pytest.mark.asyncio
async def test_another_workspace_is_absent(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
    test_categories: list[Category],
):
    await _make_business(session, test_workspace, "Padaria São João")
    await _add_transaction(
        session,
        test_user,
        test_workspace,
        test_account,
        amount="100.00",
        tx_date=date(2024, 3, 1),
        tx_type="credit",
        category_id=test_categories[2].id,
        description="IN WORKSPACE",
    )

    other_workspace = Workspace(
        id=uuid.uuid4(),
        name="Outra",
        kind="personal",
        created_by_user_id=test_user.id,
        default_currency="BRL",
    )
    session.add(other_workspace)
    await session.flush()
    session.add(
        WorkspaceMember(
            id=uuid.uuid4(),
            workspace_id=other_workspace.id,
            user_id=test_user.id,
            role="owner",
        )
    )
    other_account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=other_workspace.id,
        name="Conta Secreta",
        type="checking",
        balance=Decimal("0.00"),
        currency="BRL",
    )
    other_category = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=other_workspace.id,
        name="Segredo",
    )
    session.add_all([other_account, other_category])
    await session.flush()
    await _add_transaction(
        session,
        test_user,
        other_workspace,
        other_account,
        amount="999.00",
        tx_date=date(2024, 3, 2),
        tx_type="credit",
        category_id=other_category.id,
        description="OTHER WORKSPACE",
    )

    response = await client.get(
        EXPORT_URL,
        params={"start_date": START.isoformat(), "end_date": END.isoformat()},
        headers=_headers(auth_headers, test_workspace),
    )

    with _open_zip(response) as archive:
        transactions_raw, _ = _read_csv(archive, "transactions.csv")
        _, profit_and_loss = _read_csv(archive, "profit-and-loss.csv")

    text = transactions_raw.decode("utf-8-sig")
    assert "IN WORKSPACE" in text
    assert "OTHER WORKSPACE" not in text
    assert "Conta Secreta" not in text
    assert "999,00" not in text
    assert all("Segredo" not in row for row in profit_and_loss)


@pytest.mark.asyncio
async def test_personal_workspace_is_refused(
    client: AsyncClient,
    auth_headers: dict,
    test_workspace: Workspace,
):
    response = await client.get(
        EXPORT_URL,
        params={"start_date": START.isoformat(), "end_date": END.isoformat()},
        headers=_headers(auth_headers, test_workspace),
    )

    assert response.status_code == 403
    assert "business" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_viewer_may_export(
    client: AsyncClient,
    session: AsyncSession,
    viewer_auth_headers: dict,
    test_workspace: Workspace,
):
    await _make_business(session, test_workspace, "Padaria São João")

    response = await client.get(
        EXPORT_URL,
        params={"start_date": START.isoformat(), "end_date": END.isoformat()},
        headers=viewer_auth_headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"


@pytest.mark.asyncio
async def test_range_validation(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_workspace: Workspace,
):
    await _make_business(session, test_workspace, "Padaria São João")
    headers = _headers(auth_headers, test_workspace)

    missing = await client.get(EXPORT_URL, headers=headers)
    assert missing.status_code == 422

    missing_end = await client.get(
        EXPORT_URL, params={"start_date": START.isoformat()}, headers=headers
    )
    assert missing_end.status_code == 422

    inverted = await client.get(
        EXPORT_URL,
        params={"start_date": "2024-12-31", "end_date": "2024-01-01"},
        headers=headers,
    )
    assert inverted.status_code == 422

    future = await client.get(
        EXPORT_URL,
        params={
            "start_date": app_today().isoformat(),
            "end_date": (app_today() + timedelta(days=5)).isoformat(),
        },
        headers=headers,
    )
    assert future.status_code == 422

    too_wide = await client.get(
        EXPORT_URL,
        params={"start_date": "2000-01-01", "end_date": "2024-12-31"},
        headers=headers,
    )
    assert too_wide.status_code == 422


@pytest.mark.asyncio
async def test_text_columns_open_as_text_never_as_a_formula(
    client: AsyncClient,
    session: AsyncSession,
    auth_headers: dict,
    test_user: User,
    test_workspace: Workspace,
    test_account: Account,
):
    """A payee, description or category that begins like a formula is written
    with a leading apostrophe, so the spreadsheet reads it as text.

    Without it, a bank statement is enough to put live content on the
    accountant's machine: CSV injection, CWE-1236.
    """
    await _make_business(session, test_workspace, "Padaria São João")
    category = Category(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="=SUM(A1:A9)",
    )
    session.add(category)
    await session.flush()
    await _add_transaction(
        session,
        test_user,
        test_workspace,
        test_account,
        amount="100.00",
        tx_date=date(2024, 1, 10),
        category_id=category.id,
        description="-2+3",
        payee='+HYPERLINK("x")',
    )

    response = await client.get(
        EXPORT_URL,
        params={"start_date": START.isoformat(), "end_date": END.isoformat()},
        headers=_headers(auth_headers, test_workspace),
    )

    with _open_zip(response) as archive:
        _, rows = _read_csv(archive, "transactions.csv")
        _, profit_and_loss = _read_csv(archive, "profit-and-loss.csv")

    row = rows[1]
    assert row[4] == "'=SUM(A1:A9)"
    assert row[5] == '\'+HYPERLINK("x")'
    assert row[6] == "'-2+3"

    # The P&L reads the same category name, so it is neutralised there too.
    assert ["expense", "'=SUM(A1:A9)", "100,00", "BRL"] in profit_and_loss
