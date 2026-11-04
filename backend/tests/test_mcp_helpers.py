"""Pin what the MCP argument parsers accept and reject.

`parse_date` and `parse_uuid` lean on `date.fromisoformat` and `uuid.UUID`,
which take more than the canonical forms an agent is told to send. Every
tool that takes a date or an id goes through them, so these tests record
the current contract. Tightening or loosening it should be a deliberate
change that updates this file, not a side effect of a refactor.
"""
import uuid
from datetime import date, datetime

import pytest

from mcp_server.tools._helpers import parse_date, parse_uuid, parse_uuid_list

CANONICAL = "a1b2c3d4-e5f6-4789-abcd-ef0123456789"
ID = uuid.UUID(CANONICAL)
OTHER = uuid.UUID("b2c3d4e5-f6a7-4890-bcde-f01234567890")
DAY = date(2026, 10, 29)


# --- parse_date --------------------------------------------------------------

@pytest.mark.parametrize("value", [None, ""])
def test_parse_date_treats_missing_as_none(value):
    assert parse_date(value) is None


def test_parse_date_passes_dates_through_and_truncates_datetimes():
    assert parse_date(DAY) == DAY
    assert parse_date(datetime(2026, 10, 29, 23, 59)) == DAY


@pytest.mark.parametrize("value", [
    "2026-10-29",
    # Also ISO 8601, though not the YYYY-MM-DD the tool schemas ask for.
    "20261029",
    "2026-W44-4",
    "2026W444",
])
def test_parse_date_accepts_iso_8601_dates(value):
    assert parse_date(value) == DAY


@pytest.mark.parametrize("value", [
    "29/10/2026",
    "10/29/2026",
    "2026-10",
    "2026-302",  # ordinal dates are not accepted
    "2026-10-29T10:00:00",  # a datetime string is refused, not truncated
    " 2026-10-29",
    "2026-13-01",
    "2026-02-30",
    "yesterday",
    " ",
])
def test_parse_date_rejects_everything_else(value):
    with pytest.raises(ValueError):
        parse_date(value)


# --- parse_uuid --------------------------------------------------------------

@pytest.mark.parametrize("value", [None, ""])
def test_parse_uuid_treats_missing_as_none(value):
    assert parse_uuid(value) is None


def test_parse_uuid_passes_uuids_through():
    assert parse_uuid(ID) is ID


@pytest.mark.parametrize("value", [
    CANONICAL,
    CANONICAL.upper(),
    # uuid.UUID drops every hyphen before reading 32 hex digits, so missing
    # or misplaced hyphens still resolve to the same id.
    CANONICAL.replace("-", ""),
    "a1b2c3d4e5f64789-abcdef0123456789",
    "{" + CANONICAL + "}",
    "urn:uuid:" + CANONICAL,
])
def test_parse_uuid_accepts_the_forms_uuid_reads(value):
    assert parse_uuid(value) == ID


@pytest.mark.parametrize("value", [
    CANONICAL[:-1],
    CANONICAL + "0",
    " " + CANONICAL,
    "not-a-uuid",
    "123",
    pytest.param(123, id="int-123"),
])
def test_parse_uuid_rejects_everything_else(value):
    with pytest.raises(ValueError):
        parse_uuid(value)


# --- parse_uuid_list ---------------------------------------------------------

def test_parse_uuid_list_wraps_a_single_value_and_drops_blanks():
    assert parse_uuid_list(CANONICAL) == [ID]
    assert parse_uuid_list([CANONICAL, "", None]) == [ID]


def test_parse_uuid_list_keeps_every_valid_entry_in_order():
    """Callers filter or aggregate by every id they get, so a list cut short
    would silently skip rows instead of raising."""
    assert parse_uuid_list([str(OTHER), "", CANONICAL]) == [OTHER, ID]
    assert parse_uuid_list((ID, OTHER)) == [ID, OTHER]


@pytest.mark.parametrize("value", [None, [], [""], [None]])
def test_parse_uuid_list_returns_none_when_nothing_is_left(value):
    assert parse_uuid_list(value) is None


def test_parse_uuid_list_rejects_a_bad_entry_instead_of_dropping_it():
    with pytest.raises(ValueError):
        parse_uuid_list([CANONICAL, "not-a-uuid"])
