"""Tests for the Monarch CSV import preset (design packet §3.4, §5, §8).

Covers the spec/detection module (T1) and the parse_csv preset
integration (T3): sign vectors, date pinning, tags, account_hint,
zero-amount rejection and the golden fixtures. Pure parse-level tests —
no DB.
"""

import json
import uuid as _uuid
from datetime import date as _date
from decimal import Decimal as _Decimal
from pathlib import Path

import pytest
from sqlalchemy import func as _func
from sqlalchemy import select as _select

from app.models.transaction import Transaction as _Transaction
from app.schemas.transaction import TransactionImport as _TransactionImport
from app.services.category_defaults import DEFAULT_CATEGORIES as _DEFAULT_CATEGORIES
from app.services.category_service import (
    create_default_categories as _create_default_categories,
)
from app.services.import_category_mapping import (
    MonarchCategoryMapper,
    normalize_category_name,
)
from app.services.import_presets import (
    MONARCH,
    PresetShape,
    detect_import_preset,
    detect_monarch_shape,
    resolve_preset_alias,
    resolve_preset_columns,
    split_monarch_category,
)
from app.services.import_service import import_transactions as _import_transactions
from app.services.import_service import parse_csv

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "import"

# §3.1 combined export: every documented column, Account present.
COMBINED_HEADERS = [
    "Date",
    "Account",
    "Merchant/Description",
    "Category",
    "Amount",
    "Notes",
    "Tags",
]

# §3.2 per-account export: identical minus Account.
PER_ACCOUNT_HEADERS = [
    "Date",
    "Merchant/Description",
    "Category",
    "Amount",
    "Notes",
    "Tags",
]


class TestMonarchPresetDetection:
    def test_combined_headers_classify_shape_not_provenance(self):
        """Shape is useful only after explicit Monarch confirmation."""
        assert detect_import_preset(COMBINED_HEADERS) is None
        assert detect_monarch_shape(COMBINED_HEADERS) is PresetShape.COMBINED

    def test_per_account_headers_classify_shape_not_provenance(self):
        """Even the full documented signature cannot establish its origin."""
        assert detect_import_preset(PER_ACCOUNT_HEADERS) is None
        assert detect_monarch_shape(PER_ACCOUNT_HEADERS) is PresetShape.PER_ACCOUNT

    def test_monarch_preset_declines_unknown_headers(self):
        """Unknown or generic signatures decline — no crash, generic CSV
        path takes over (§3.4: a preset never guesses past its evidence)."""
        assert detect_import_preset(["col1", "col2", "col3"]) is None
        assert detect_monarch_shape(["col1", "col2", "col3"]) is None
        # A plain generic CSV (no Monarch-specific marker) must NOT be
        # detected: auto-detection would silently flip its sign convention.
        assert detect_import_preset(["date", "description", "amount"]) is None
        assert detect_import_preset([]) is None

    def test_monarch_alias_table_resolves_header_drift(self):
        """Documented header drift resolves through the alias table (§3.4):
        Merchant / Payee / Transaction Date variants all land on their
        Securo field, keeping the file's original header casing."""
        drifted = [
            "Transaction Date",
            "Account Name",
            "Merchant",
            "Category",
            "Amount",
            "Memo",
            "Tag",
        ]
        resolved = resolve_preset_columns(MONARCH, drifted)
        assert resolved["date"] == "Transaction Date"
        assert resolved["account"] == "Account Name"
        assert resolved["description"] == "Merchant"
        assert resolved["category"] == "Category"
        assert resolved["amount"] == "Amount"
        assert resolved["notes"] == "Memo"
        assert resolved["tags"] == "Tag"
        # Drifted signature still detects as the combined shape.
        assert detect_monarch_shape(drifted) is PresetShape.COMBINED

        # Payee variant for the description column; case/whitespace-insensitive.
        assert (
            resolve_preset_alias(MONARCH.column_aliases["description"], ["PAYEE"])
            == "PAYEE"
        )
        assert resolve_preset_alias(MONARCH.column_aliases["date"], ["DATE "]) == "DATE "

    def test_monarch_preset_spec_is_late_bound_data(self):
        """Sign convention and date pin are data on the spec, not code (§3.4,
        §5.2, §5.4): flip enabled, strict MM/DD/YYYY default."""
        assert MONARCH.flip_amount is True
        assert MONARCH.default_date_format == "MM/DD/YYYY"
        assert MONARCH.name == "monarch"
        # Alias table is data a maintainer can extend in one line.
        assert isinstance(MONARCH.column_aliases["description"], tuple)
        assert "merchant/description" in MONARCH.column_aliases["description"]


# --- T3: parse_csv preset integration (§5, §8.1, §8.2) -------------------


def _monarch_csv(rows: list[str], headers: list[str] | None = None) -> bytes:
    header = ",".join(headers or PER_ACCOUNT_HEADERS)
    return ("\n".join([header, *rows]) + "\n").encode("utf-8")


def _cell(value: str) -> str:
    """CSV-quote a cell when it contains a delimiter."""
    return f'"{value}"' if "," in value else value


def _parse_single(amount_cell: str, category: str = "Shopping"):
    """Parse one Monarch per-account row with the given raw amount cell."""
    content = _monarch_csv([
        f"03/15/2026,Synthetic Merchant,{category},{_cell(amount_cell)},,"
    ])
    return parse_csv(content, preset=MONARCH)


@pytest.mark.parametrize(
    "amount_cell,category,expected_type,expected_amount",
    [
        ("-1200.00", "Income: Paycheck", "credit", "1200.00"),
        ("45.00", "Food & Dining: Restaurants", "debit", "45.00"),
        ("-0.05", "Income: Interest", "credit", "0.05"),
        ("1234.56", "Shopping", "debit", "1234.56"),
        ("1,234.56", "Shopping", "debit", "1234.56"),
        ("-1,234.56", "Income: Paycheck", "credit", "1234.56"),
        ("(12.50)", "Fees & Charges", "credit", "12.50"),
        ("\u22125.00", "Transportation", "credit", "5.00"),
    ],
    ids=[
        "income_negative",
        "expense_positive",
        "tiny_income",
        "decimal_expense",
        "thousands",
        "thousands_income",
        "paren_negative",
        "unicode_minus",
    ],
)
def test_monarch_sign_normalization(
    amount_cell, category, expected_type, expected_amount
):
    """§5.3 sign vectors: Monarch income-negative → Securo credit + abs."""
    transactions, failed_rows = _parse_single(amount_cell, category)
    assert failed_rows == []
    assert len(transactions) == 1
    txn = transactions[0]
    assert txn.type == expected_type
    assert str(txn.amount) == expected_amount


def test_monarch_zero_amount_reported_as_failed_row():
    """§5.3 zero_amount: never a silent zero-value row."""
    transactions, failed_rows = _parse_single("0.00")
    assert transactions == []
    assert len(failed_rows) == 1
    assert failed_rows[0].error_reason == "zero_amount"
    assert failed_rows[0].raw_value == "0.00"


def test_monarch_empty_amount_reported_as_failed_row():
    """§5.3 empty_amount → the `no_amount` failure reason."""
    transactions, failed_rows = _parse_single("")
    assert transactions == []
    assert len(failed_rows) == 1
    assert failed_rows[0].error_reason == "no_amount"


def test_monarch_garbage_amount_reported_as_failed_row():
    """§5.3 garbage_amount → the `invalid_amount` failure reason."""
    transactions, failed_rows = _parse_single("N/A")
    assert transactions == []
    assert len(failed_rows) == 1
    assert failed_rows[0].error_reason == "invalid_amount"


def test_monarch_deterministic_date_pinning():
    """§5.4: pinned MM/DD/YYYY parses 03/05 as Mar 5 and 12/25 as Dec 25 —
    the 12/25 row is the disambiguator; no per-row format mixing."""
    content = _monarch_csv([
        "03/05/2026,Synthetic A,Shopping,10.00,,",
        "12/25/2026,Synthetic B,Shopping,20.00,,",
    ])
    transactions, failed_rows = parse_csv(content, preset=MONARCH)
    assert failed_rows == []
    assert [t.date for t in transactions] == [_date(2026, 3, 5), _date(2026, 12, 25)]


def test_monarch_date_format_user_override_wins():
    """§5.4: an explicit date-format choice beats the preset's pin."""
    content = _monarch_csv([
        "2026-03-05,Synthetic A,Shopping,10.00,,",
    ])
    # Pinned MM/DD/YYYY cannot parse ISO — the row fails ...
    transactions, failed_rows = parse_csv(content, preset=MONARCH)
    assert transactions == []
    assert failed_rows[0].error_reason == "invalid_date"
    # ... until the user's explicit override wins.
    transactions, failed_rows = parse_csv(
        content, date_format="YYYY-MM-DD", preset=MONARCH
    )
    assert failed_rows == []
    assert transactions[0].date == _date(2026, 3, 5)


def test_monarch_tags_become_notes_hashtags():
    """§5.5: Tags land as canonical #hashtags in notes (A5 delimiters)."""
    content = _monarch_csv([
        '03/15/2026,Synthetic A,Shopping,10.00,,"groceries; family"',
        '03/16/2026,Synthetic B,Shopping,11.00,,"travel,summer"',
    ])
    transactions, _ = parse_csv(content, preset=MONARCH)
    assert transactions[0].notes == "#groceries #family"
    assert transactions[1].notes == "#travel #summer"


def test_monarch_tags_preserve_existing_notes():
    """§5.5: existing notes come first, tags appended after (never dropped)."""
    content = _monarch_csv([
        '03/15/2026,Synthetic A,Housing,950.00,monthly rent,"home; fixed"',
    ])
    transactions, _ = parse_csv(content, preset=MONARCH)
    assert transactions[0].notes == "monthly rent #home #fixed"


def test_monarch_missing_tags_column_is_noop():
    """§5.5/A5: no Tags column → notes pass through untouched."""
    headers = ["Date", "Merchant/Description", "Category", "Amount", "Notes"]
    content = _monarch_csv(
        ["03/15/2026,Synthetic A,Shopping,10.00,some note"], headers=headers
    )
    transactions, failed_rows = parse_csv(content, preset=MONARCH)
    assert failed_rows == []
    assert transactions[0].notes == "some note"


def test_monarch_account_hint_preserved_on_rows():
    """§7.2: the combined export's Account cell rides each row as
    account_hint; the per-account shape leaves it None."""
    combined = _monarch_csv(
        [
            "03/15/2026,Joint Checking,Synthetic A,Shopping,10.00,,",
            "03/16/2026,Rewards Card,Synthetic B,Shopping,11.00,,",
        ],
        headers=COMBINED_HEADERS,
    )
    transactions, _ = parse_csv(combined, preset=MONARCH)
    assert [t.account_hint for t in transactions] == ["Joint Checking", "Rewards Card"]

    per_account = _monarch_csv(["03/15/2026,Synthetic A,Shopping,10.00,,"])
    transactions, _ = parse_csv(per_account, preset=MONARCH)
    assert transactions[0].account_hint is None


def test_monarch_category_composite_passthrough():
    """§4.1: the raw "Group: Category" string rides category_name unchanged;
    the split happens in the mapping layer, not the parser."""
    content = _monarch_csv([
        "03/15/2026,Synthetic A,Food & Dining: Restaurants,10.00,,",
        "03/16/2026,Synthetic B,Groceries,11.00,,",
    ])
    transactions, _ = parse_csv(content, preset=MONARCH)
    assert transactions[0].category_name == "Food & Dining: Restaurants"
    assert transactions[1].category_name == "Groceries"


def _fixture_headers(name: str) -> list[str]:
    return (FIXTURES / f"{name}.csv").read_text().splitlines()[0].split(",")


def test_monarch_fixture_shapes_match_spec():
    """§3.5 fixture-validation gate: fixture headers resolve through the
    alias table and the detected shape matches the expected enum. Swapping
    these synthetic files for anonymized real exports must stay green except
    for documented deltas."""
    assert detect_monarch_shape(_fixture_headers("monarch-combined")) is PresetShape.COMBINED
    assert (
        detect_monarch_shape(_fixture_headers("monarch-account-checking"))
        is PresetShape.PER_ACCOUNT
    )

    # Fixtures themselves parse through the preset to the documented counts.
    combined = parse_csv(
        FIXTURES.joinpath("monarch-combined.csv").read_bytes(), preset=MONARCH
    )
    per_account = parse_csv(
        FIXTURES.joinpath("monarch-account-checking.csv").read_bytes(), preset=MONARCH
    )
    # 14 parsed rows + 3 documented failure rows each (17-line fixture).
    assert len(combined[0]) == 14 and len(combined[1]) == 3
    assert len(per_account[0]) == 14 and len(per_account[1]) == 3


@pytest.mark.parametrize("name", ["monarch-combined", "monarch-account-checking"])
def test_monarch_golden(name):
    """Golden-file pin (§8.1): parser output equals the committed sidecar —
    exact list[TransactionImport] + FailedRow serialization, dates ISO,
    amounts as strings. Deterministic, no DB."""
    content = FIXTURES.joinpath(f"{name}.csv").read_bytes()
    transactions, failed_rows = parse_csv(content, preset=MONARCH)
    actual = {
        "transactions": [t.model_dump(mode="json") for t in transactions],
        "failed_rows": [f.model_dump(mode="json") for f in failed_rows],
    }
    expected = json.loads(FIXTURES.joinpath(f"{name}.expected.json").read_text())
    assert actual == expected


def test_monarch_golden_combined_csv():
    """§8.2 named case: combined-shape golden comparison."""
    test_monarch_golden("monarch-combined")


def test_monarch_golden_per_account_csv():
    """§8.2 named case: per-account golden comparison."""
    test_monarch_golden("monarch-account-checking")


# --- T4: dry-run summary via the API (§7.3, §8.2) ------------------------


def _preview_dry_run(client, auth_headers, account_id: str, name: str = "monarch-combined"):
    return client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={"file": (f"{name}.csv", FIXTURES.joinpath(f"{name}.csv").read_bytes(), "text/csv")},
        data={"preset": "monarch", "dry_run": "true", "account_id": account_id},
    )


async def test_monarch_dry_run_reports_counts_without_writing(
    client, auth_headers, session, test_account, test_user, test_workspace
):
    """§8.2: dry-run returns rows-in/would-import/dupes/failed/unmapped and
    the transactions table is unchanged (row count asserted directly).

    The workspace is seeded with the default taxonomy first — production
    workspaces are created with defaults (§4), and an empty workspace has
    nothing to map category values to."""
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from app.models.transaction import Transaction
    from app.services.category_service import create_default_categories

    await create_default_categories(
        session, test_user.id, lang="en", workspace_id=test_workspace.id
    )

    before = (
        await session.execute(sa_select(func.count()).select_from(Transaction))
    ).scalar_one()

    resp = await _preview_dry_run(client, auth_headers, str(test_account.id))
    assert resp.status_code == 200
    summary = resp.json()["dry_run"]
    assert summary["rows_in"] == 17
    assert summary["would_import"] == 14
    assert summary["would_skip_dupes"] == 0
    assert summary["failed_rows"] == 3
    # Every distinct Monarch category value in the fixture resolves through
    # the §4.2 chain (alias table first), so nothing is unmapped.
    assert summary["unmapped_categories"] == 0

    await session.rollback()
    after = (
        await session.execute(sa_select(func.count()).select_from(Transaction))
    ).scalar_one()
    assert after == before


async def test_monarch_dry_run_counts_match_actual_import(
    client, auth_headers, test_account
):
    """§8.2: what dry-run promises is what the import delivers — fresh file
    imports every row; the same file again would skip every row (P1)."""
    first = await _preview_dry_run(client, auth_headers, str(test_account.id))
    assert first.json()["dry_run"]["would_import"] == 14
    assert first.json()["dry_run"]["would_skip_dupes"] == 0

    preview = await client.post(
        "/api/transactions/import/preview",
        headers=auth_headers,
        files={
            "file": (
                "monarch-combined.csv",
                FIXTURES.joinpath("monarch-combined.csv").read_bytes(),
                "text/csv",
            )
        },
        data={"preset": "monarch"},
    )
    rows = preview.json()["transactions"]
    resp = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "transactions": rows,
            "filename": "monarch-combined.csv",
            "detected_format": "csv",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["imported"] == 14
    assert resp.json()["skipped"] == 0

    second = await _preview_dry_run(client, auth_headers, str(test_account.id))
    assert second.json()["dry_run"]["would_import"] == 0
    assert second.json()["dry_run"]["would_skip_dupes"] == 14

    again = await client.post(
        "/api/transactions/import",
        headers=auth_headers,
        json={
            "account_id": str(test_account.id),
            "transactions": rows,
            "filename": "monarch-combined.csv",
            "detected_format": "csv",
        },
    )
    assert again.status_code == 201
    assert again.json()["imported"] == 0
    assert again.json()["skipped"] == 14


# --- T5: category mapping service (§4.1-§4.4, §8.2) ----------------------


async def _seeded_mapper(session, user, workspace, lang="en") -> MonarchCategoryMapper:
    await _create_default_categories(
        session, user.id, lang=lang, workspace_id=workspace.id
    )
    return await MonarchCategoryMapper.load(session, workspace.id)


def test_monarch_category_group_split_composite():
    """§4.1: composite splits on the first ': ', bare stays category-only,
    empty carries no signal at all."""
    assert split_monarch_category("Food & Dining: Restaurants") == (
        "Food & Dining",
        "Restaurants",
    )
    assert split_monarch_category("Groceries") == (None, "Groceries")
    assert split_monarch_category("") == (None, None)
    assert split_monarch_category(None) == (None, None)
    # First colon wins even if a later segment contains one.
    assert split_monarch_category("A: B: C") == ("A", "B: C")


async def test_monarch_category_alias_table_hits_default_keys(
    session, test_user, test_workspace
):
    """§4.2 seed table, row by row: every documented Monarch value lands on
    the Securo internal key's own row (matched via display names here
    because the workspace was seeded with EN defaults)."""
    mapper = await _seeded_mapper(session, test_user, test_workspace)

    seed = [
        # (Monarch value, expected Securo internal key)
        ("Income: Paycheck", "salary"),          # category refinement
        ("Income: Bonus", "salary"),             # group default
        ("Housing: Rent", "housing"),
        ("Food & Dining: Restaurants", "food"),
        ("Food & Dining: Groceries", "groceries"),  # refinement beats group
        ("Groceries: Produce", "groceries"),      # group default
        ("Transportation: Fuel", "transport"),
        ("Shopping: Electronics", "shopping"),
        ("Entertainment: Movies", "leisure"),
        ("Health & Fitness: Gym", "health"),
        ("Medical: Pharmacy", "health"),
        ("Personal Care: Haircut", "personal_care"),
        ("Education: Books", "education"),
        ("Bills & Utilities: Electric", "subscriptions"),
        ("Bills & Utilities: Subscriptions", "subscriptions"),
        ("Subscriptions: Streaming", "subscriptions"),
        ("Taxes: Filing Fee", "other"),           # group default (key column)
        ("Fees & Charges: Wire Fee", "other"),
        ("Taxes", "taxes"),                       # bare category refinement
        ("Gifts & Donations: Charity", "donations"),
        ("Transfer: Credit Card Payment", "transfers"),
        ("Investments: Brokerage", "investments"),
        ("Paycheck", "salary"),                   # bare refinement
        ("Anything Else: Glarble", None),         # unmapped, never forced
        ("", None),
    ]
    for value, expected_key in seed:
        row = mapper.suggest(value)
        if expected_key is None:
            assert row is None, value
        else:
            assert row is not None, value
            assert row.name == _DEFAULT_CATEGORIES[expected_key]["names"]["en"], value


async def test_monarch_category_refinement_beats_group_default(
    session, test_user, test_workspace
):
    """§4.2 rule 1: 'Food & Dining: Groceries' maps to Groceries, not Food."""
    mapper = await _seeded_mapper(session, test_user, test_workspace)
    row = mapper.suggest("Food & Dining: Groceries")
    assert row is not None
    assert row.name == "Groceries"
    assert row.name != "Food & Dining"


async def test_monarch_category_normalized_name_match(
    session, test_user, test_workspace
):
    """§4.2 step 2/3: '&'-style variants match after normalization
    ('food and dining: restaurants' vs the 'Food & Dining' names)."""
    mapper = await _seeded_mapper(session, test_user, test_workspace)

    # Step 2: workspace's own names, &→and + case/whitespace-insensitive.
    row = mapper.suggest("food and dining: restaurants")
    assert row is not None and row.name == "Food & Dining"
    row = mapper.suggest("FOOD  & DINING: Restaurants")
    assert row is not None and row.name == "Food & Dining"
    assert normalize_category_name("Food & Dining") == "food and dining"


async def test_monarch_category_localized_workspace_maps_via_internal_keys(
    session, test_user, test_workspace
):
    """R5/§4.2: a pt-BR workspace shows pt-BR display names, yet Monarch
    values land on the correct rows — matching runs through internal keys
    (step 1 alias, step 3 EN names), never display-name equality."""
    mapper = await _seeded_mapper(session, test_user, test_workspace, lang="pt-BR")

    # Step 1: alias table → key → the pt-BR row for that key.
    row = mapper.suggest("Income: Paycheck")
    assert row is not None
    assert row.name == "Salário & Renda"  # pt-BR name of internal key "salary"

    # Step 3: 'food and dining' misses the alias table and the pt-BR names,
    # then hits the EN default name → internal key → the pt-BR row.
    row = mapper.suggest("food and dining: restaurants")
    assert row is not None
    assert row.name == "Alimentação"  # pt-BR name of internal key "food"


async def test_monarch_unmapped_category_contributes_no_category(
    session, test_user, test_workspace, test_account, test_categories, test_rules
):
    """§4.4: an unmapped value contributes NO category — the row lands
    category_id IS NULL (Securo's uncategorized state) when no rule fills
    it, rules may still assign one, and the mapping never sets
    force_uncategorized (that stays an explicit user choice)."""
    mapper = await MonarchCategoryMapper.load(session, test_workspace.id)
    assert mapper.suggest("Mystery Ledger: Glarble") is None

    unmapped = _TransactionImport(
        description="Obscure Vendor",
        amount=_Decimal("10.00"),
        date=_date(2026, 5, 1),
        type="debit",
        category_name="Mystery Ledger: Glarble",
    )
    imported, _, _, log_id = await _import_transactions(
        session, test_workspace.id, test_user.id, test_account.id,
        [unmapped], "csv", detected_format="csv",
    )
    assert imported == 1
    txn = (await session.execute(
        _select(_Transaction).where(_Transaction.import_id == log_id)
    )).scalar_one()
    assert txn.category_id is None          # uncategorized, not forced
    assert unmapped.force_uncategorized is False  # mapping never forces

    # Rules may still fill an unmapped row (established CSV precedence).
    ruled = _TransactionImport(
        description="UBER TRIP",
        amount=_Decimal("25.50"),
        date=_date(2026, 5, 2),
        type="debit",
        category_name="Mystery Ledger: Glarble",
    )
    imported, _, _, log_id = await _import_transactions(
        session, test_workspace.id, test_user.id, test_account.id,
        [ruled], "csv", detected_format="csv",
    )
    assert imported == 1
    txn = (await session.execute(
        _select(_Transaction).where(_Transaction.import_id == log_id)
    )).scalar_one()
    # The description rule beat the unmapped CSV category — rules fill
    # where the mapping deliberately contributes nothing.
    assert txn.category_id == test_categories[1].id


async def test_monarch_hidden_category_never_suggested(
    session, test_user, test_workspace
):
    """§4.2: hidden categories — directly or through their hidden group —
    are never suggested."""
    mapper = await _seeded_mapper(session, test_user, test_workspace)

    groceries = next(
        c for c in mapper._categories if c.name == "Groceries"
    )
    groceries.is_hidden = True
    await session.commit()
    mapper = await MonarchCategoryMapper.load(session, test_workspace.id)
    assert mapper.suggest("Groceries") is None
    assert mapper.suggest("Food & Dining: Groceries") is None
    # Unrelated non-hidden suggestions still work.
    assert mapper.suggest("Housing: Rent") is not None

    # Hiding the GROUP hides it from suggestion too (get_categories term).
    food_group = next(
        g for g in mapper._groups if g.name == "Food & Dining"
    )
    food_group.is_hidden = True
    await session.commit()
    mapper = await MonarchCategoryMapper.load(session, test_workspace.id)
    assert mapper.suggest("Food & Dining: Restaurants") is None


# --- T9: golden + dedupe-contract sweep (§6.2 P1–P5, §8.2) ------------------
#
# P6 lives in the pre-existing test_import_tolerates_duplicate_external_id_rows
# (issue #911 pin). P4 is the #886 pin — see its docstring and §6.3.
# Every test here consumes dedupe behavior; none of them changes it.


async def _import_golden(
    session, test_user, test_workspace, test_account, **kwargs
) -> tuple[int, int]:
    """Parse + import the combined golden fixture. Returns (imported, skipped)."""
    content = FIXTURES.joinpath("monarch-combined.csv").read_bytes()
    transactions, _failed = parse_csv(content, preset=MONARCH)
    imported, skipped, _excluded, _log_id = await _import_transactions(
        session,
        test_workspace.id,
        test_user.id,
        test_account.id,
        transactions,
        "csv",
        filename="monarch-combined.csv",
        detected_format="csv",
        **kwargs,
    )
    return imported, skipped


async def test_monarch_reimport_is_idempotent(
    session, test_user, test_workspace, test_account
):
    """P1 (§6.2): running the golden file twice imports once — the second
    run recognizes and skips every previously imported row."""
    first = await _import_golden(session, test_user, test_workspace, test_account)
    assert first == (14, 0)
    second = await _import_golden(session, test_user, test_workspace, test_account)
    assert second == (0, 14)


async def test_monarch_same_day_identical_rows_all_imported(
    session, test_user, test_workspace, test_account
):
    """P2 (§6.2): N identical same-day rows in one file all import — the
    golden fixture's Fernwood Coffee 4.99 ×2 pair must both land."""
    imported, skipped = await _import_golden(session, test_user, test_workspace, test_account)
    assert (imported, skipped) == (14, 0)
    count = (
        await session.execute(
            _select(_func.count())
            .select_from(_Transaction)
            .where(
                _Transaction.description == "Fernwood Coffee",
                _Transaction.date == _date(2026, 3, 16),
            )
        )
    ).scalar_one()
    assert count == 2


async def test_monarch_duplicate_consuming_synced_row_once(
    session, test_user, test_workspace, test_account
):
    """P3 (§6.2): an incoming row equal to a SYNCED transaction is skipped
    and consumes it once — the second identical incoming row imports."""
    session.add(
        _Transaction(
            id=_uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            external_id="provider-fernwood",
            description="Fernwood Coffee",
            amount=_Decimal("4.99"),
            date=_date(2026, 3, 16),
            type="debit",
            source="sync",
            status="posted",
        )
    )
    await session.commit()

    content = _monarch_csv(
        [
            "03/16/2026,Fernwood Coffee,Food & Dining: Coffee Shops,4.99,morning coffee,",
            "03/16/2026,Fernwood Coffee,Food & Dining: Coffee Shops,4.99,morning coffee,",
        ]
    )
    transactions, _failed = parse_csv(content, preset=MONARCH)
    imported, skipped, _excluded, _log_id = await _import_transactions(
        session,
        test_workspace.id,
        test_user.id,
        test_account.id,
        transactions,
        "csv",
        filename="monarch-pair.csv",
        detected_format="csv",
    )
    # First incoming row consumes the synced duplicate; second imports.
    assert (imported, skipped) == (1, 1)
    count = (
        await session.execute(
            _select(_func.count())
            .select_from(_Transaction)
            .where(
                _Transaction.description == "Fernwood Coffee",
                _Transaction.date == _date(2026, 3, 16),
            )
        )
    ).scalar_one()
    assert count == 2  # synced row (consumed once) + newly imported row


@pytest.mark.xfail(
    strict=False,
    reason="issue #886: second identical same-day row judged a duplicate of the placeholder path",
)
async def test_monarch_placeholder_same_day_twice(
    session, test_user, test_workspace, test_account
):
    """P4 (§6.2): N identical same-day rows matching a recurring placeholder
    must all land — the placeholder upgrades for one and the rest import;
    never a silent drop. Pinned end-state: (imported, skipped) == (2, 0).

    Observed at pin 9af6a01f against unmodified dedupe: (1, 1) — the second
    identical same-day row is silently dropped, because the field-key
    statement has no status/source filter and matches it against the
    placeholder path (design packet §6.3; issue #886). Marked
    xfail(strict=False) per the §6.3 protocol — the pinned assertion above is
    NOT weakened, and #886 is NOT fixed inside PR-3. Acceptance check after
    the upstream fix: this test XPASSes (strict=False tolerates it) and the
    marker comes off.
    """
    from app.schemas.recurring_transaction import RecurringTransactionCreate
    from app.services.recurring_transaction_service import (
        create_recurring_transaction,
    )

    bill = await create_recurring_transaction(
        session,
        test_workspace.id,
        test_user.id,
        RecurringTransactionCreate(
            description="Fernwood Coffee",
            amount=_Decimal("4.99"),
            currency=test_account.currency,
            type="debit",
            frequency="monthly",
            start_date=_date(2026, 1, 16),
            account_id=test_account.id,
        ),
    )
    session.add(
        _Transaction(
            id=_uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=test_account.id,
            description="Fernwood Coffee",
            amount=_Decimal("4.99"),
            currency=test_account.currency,
            date=_date(2026, 3, 16),
            type="debit",
            source="recurring",
            status="pending",
            recurring_transaction_id=bill.id,
        )
    )
    await session.commit()

    content = _monarch_csv(
        [
            "03/16/2026,Fernwood Coffee,Food & Dining: Coffee Shops,4.99,morning coffee,",
            "03/16/2026,Fernwood Coffee,Food & Dining: Coffee Shops,4.99,morning coffee,",
        ]
    )
    transactions, _failed = parse_csv(content, preset=MONARCH)
    imported, skipped, _excluded, _log_id = await _import_transactions(
        session,
        test_workspace.id,
        test_user.id,
        test_account.id,
        transactions,
        "csv",
        filename="monarch-pair.csv",
        detected_format="csv",
    )
    assert (imported, skipped) == (2, 0)


async def test_monarch_detect_duplicates_false_allows_reimport(
    session, test_user, test_workspace, test_account
):
    """P5 (§6.2): with Detect duplicates off (CSV-only toggle), re-importing
    the same file creates duplicates instead of skipping them."""
    first = await _import_golden(session, test_user, test_workspace, test_account)
    assert first == (14, 0)
    again = await _import_golden(
        session, test_user, test_workspace, test_account, detect_duplicates=False
    )
    assert again == (14, 0)
    count = (
        await session.execute(
            _select(_func.count())
            .select_from(_Transaction)
            .where(
                _Transaction.description == "Fernwood Coffee",
                _Transaction.date == _date(2026, 3, 16),
            )
        )
    ).scalar_one()
    assert count == 4  # two identical rows imported twice, on purpose
