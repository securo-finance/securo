"""Interactive Brokers Flex Web Service v3 provider.

The Flex service is a read-only, report-oriented API. A user supplies a Flex
token and an Activity Flex Query id; one generated XML document then provides
cash balances, non-trade cash activity and open positions. The provider keeps
that document in-memory for the duration of a Securo sync so accounts,
transactions and holdings never generate three separate upstream reports.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.agents.services.crypto import decrypt, encrypt
from app.core.config import get_settings
from app.providers.base import (
    AccountData,
    BankProvider,
    ConnectionData,
    HoldingData,
    ProviderRateLimited,
    ProviderTransientError,
    ProviderUserActionRequired,
    TransactionData,
    mask_last4,
)
from app.providers.favicon import favicon_url_for

logger = logging.getLogger(__name__)


class _RedactFlexTokenFilter(logging.Filter):
    """Remove Flex tokens from httpx's INFO-level request URL logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.args, tuple):
            return True
        sanitized: list[object] = []
        for value in record.args:
            if (
                isinstance(value, httpx.URL)
                and "/FlexWebService/" in value.path
                and "t" in value.params
            ):
                safe_params = [
                    (key, "[REDACTED]" if key == "t" else item)
                    for key, item in value.params.multi_items()
                ]
                value = value.copy_with(query=str(httpx.QueryParams(safe_params)).encode("ascii"))
            sanitized.append(value)
        record.args = tuple(sanitized)
        return True


_httpx_logger = logging.getLogger("httpx")
if not any(isinstance(item, _RedactFlexTokenFilter) for item in _httpx_logger.filters):
    _httpx_logger.addFilter(_RedactFlexTokenFilter())

IBKR_INITIAL_HISTORY_DAYS = 365
IBKR_HTTP_TIMEOUT = 60.0
IBKR_MAX_RESPONSE_BYTES = 20 * 1024 * 1024
IBKR_POLL_INTERVAL_SECONDS = 10
IBKR_MAX_POLLS = 30
IBKR_HELP_URL = "https://www.ibkrguides.com/brokerportal/performanceandstatements/flex3.htm"

_QUERY_ID_RE = re.compile(r"^[0-9]+$")
_DATE_PREFIX_RE = re.compile(
    r"^(\d{8}|\d{4}-\d{2}-\d{2}|\d{4}/\d{2}/\d{2}|\d{2}/\d{2}/\d{4})(?:$|[ T;])"
)

_USER_ACTION_ERRORS = {
    "1010": "flex_query_invalid",
    "1011": "flex_service_inactive",
    "1012": "credentials_expired",
    "1013": "flex_ip_restricted",
    "1014": "flex_query_invalid",
    "1015": "credentials_invalid",
    "1016": "flex_account_invalid",
    "1020": "flex_request_invalid",
}
_USER_ACTION_MESSAGES = {
    "1010": "The saved query is a legacy Flex Query. Convert it to an Activity Flex Query.",
    "1011": "IBKR Flex Web Service is inactive. Enable it and reconnect.",
    "1012": "The IBKR Flex token has expired. Generate a new token and reconnect.",
    "1013": "IBKR rejected this server's IP address. Update the token IP restriction.",
    "1014": "The IBKR Activity Flex Query ID is invalid. Verify it and reconnect.",
    "1015": "The IBKR Flex token is invalid. Generate a new token and reconnect.",
    "1016": "The account selected by this IBKR Flex Query is invalid.",
    "1020": "IBKR could not validate the Flex request. Verify the token and query ID.",
}
_TRANSIENT_ERRORS = {
    "1001",
    "1003",
    "1004",
    "1005",
    "1006",
    "1007",
    "1008",
    "1009",
    "1017",
    "1021",
}
_POLL_RETRY_ERRORS = _TRANSIENT_ERRORS - {"1017"} | {"1018", "1019"}


@dataclass(frozen=True)
class _ParsedReport:
    account_id: str
    account_alias: str | None
    account_name: str | None
    base_currency: str
    cash_balances: dict[str, Decimal]
    statement_funds: list[dict[str, str]]
    positions: list[dict[str, str]]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(root: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [element for element in root.iter() if _local_name(element.tag) == name]


def _first(root: ElementTree.Element, *names: str) -> ElementTree.Element | None:
    wanted = set(names)
    return next(
        (element for element in root.iter() if _local_name(element.tag) in wanted),
        None,
    )


def _child_text(root: ElementTree.Element, *names: str) -> str | None:
    element = _first(root, *names)
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _attrs(element: ElementTree.Element) -> dict[str, str]:
    return {key.rsplit("}", 1)[-1]: value for key, value in element.attrib.items()}


def _value(data: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        raw = data.get(key)
        if raw is not None and str(raw).strip() != "":
            return str(raw).strip()
    return None


def _has_field(data: dict[str, str], *keys: str) -> bool:
    """Return whether the query selected at least one equivalent XML field.

    IBKR emits selected-but-empty attributes as ``field=""``. Checking key
    presence instead of truthiness therefore distinguishes an unavailable
    value (valid for e.g. an option without an ISIN) from a field the user did
    not add to the Flex query at all.
    """
    return any(key in data for key in keys)


def _missing_selected_fields(data: dict[str, str], fields: dict[str, tuple[str, ...]]) -> list[str]:
    return [label for label, aliases in fields.items() if not _has_field(data, *aliases)]


def _query_error(message: str, *, code: str = "flex_query_missing_field") -> NoReturn:
    raise ProviderUserActionRequired(message, code=code, help_url=IBKR_HELP_URL)


def _decimal(value: Any, *, field: str, required: bool = False) -> Decimal | None:
    if value is None or str(value).strip() in {"", "--"}:
        if required:
            raise ValueError(f"IBKR Flex report is missing required field: {field}")
        return None
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"IBKR Flex field {field} is not a valid number") from exc
    if not parsed.is_finite():
        raise ValueError(f"IBKR Flex field {field} is not a finite number")
    return parsed


def _date(value: Any, *, field: str, required: bool = False) -> date | None:
    if value is None or not str(value).strip():
        if required:
            raise ValueError(f"IBKR Flex report is missing required field: {field}")
        return None
    value_text = str(value).strip()
    match = _DATE_PREFIX_RE.match(value_text)
    if not match:
        raise ValueError(f"IBKR Flex field {field} is not a valid date")
    raw = match.group(1)
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"IBKR Flex field {field} is not a valid date")


def _statement_amount(row: dict[str, str]) -> Decimal:
    signed = _decimal(
        _value(row, "amount"),
        field="Statement of Funds / Amount",
    )
    if signed is not None:
        return signed
    credit = _decimal(
        _value(row, "credit"),
        field="Statement of Funds / Credit",
    )
    debit = _decimal(
        _value(row, "debit"),
        field="Statement of Funds / Debit",
    )
    if credit is None and debit is None:
        raise ValueError("IBKR Flex Statement of Funds must include Amount or Debit/Credit")
    return (credit or Decimal("0")) - abs(debit or Decimal("0"))


def _iso_currency(value: Any) -> str | None:
    code = str(value or "").strip().upper()
    if len(code) == 3 and code.isalpha():
        return code
    return None


def _xml_bytes(payload: str | bytes) -> bytes:
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    if len(raw) > IBKR_MAX_RESPONSE_BYTES:
        raise ValueError("IBKR Flex response is too large")
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("IBKR Flex report contains unsupported XML declarations")
    return raw


def _parse_element(payload: str | bytes) -> ElementTree.Element:
    return ElementTree.fromstring(_xml_bytes(payload))


def _parse_xml(xml_text: str | bytes) -> _ParsedReport:
    """Parse and validate the exact Activity Flex sections Securo consumes."""
    try:
        root = _parse_element(xml_text)
    except ElementTree.ParseError as exc:
        raise ProviderTransientError("IBKR Flex returned malformed report XML") from exc

    statements = _children(root, "FlexStatement")
    if len(statements) != 1:
        raise ProviderUserActionRequired(
            "The IBKR Flex query must return exactly one account. "
            "Create one Flex query per IBKR account.",
            code="flex_query_account_count",
            help_url=IBKR_HELP_URL,
        )
    statement = statements[0]
    statement_data = _attrs(statement)

    account_info = _first(statement, "AccountInformation")
    if account_info is None:
        _query_error(
            "The IBKR Flex query is missing the Account Information section.",
            code="flex_query_missing_section",
        )
    info = {**statement_data, **_attrs(account_info)}
    account_id = _value(info, "accountId", "accountID")
    base_currency = _iso_currency(_value(info, "currency", "baseCurrency"))
    if not account_id or not base_currency:
        _query_error("Account Information must include Account ID and Currency.")
    if not _has_field(info, "accountAlias", "acctAlias", "name", "accountName"):
        _query_error("Account Information must include Account Name or Account Alias.")

    cash_report = _first(statement, "CashReport")
    if cash_report is None:
        _query_error(
            "The IBKR Flex query is missing Cash Report with currency breakout.",
            code="flex_query_missing_section",
        )
    cash_balances: dict[str, Decimal] = {}
    base_summary: Decimal | None = None
    for row in cash_report.iter():
        if row is cash_report or _local_name(row.tag) not in {
            "CashReportCurrency",
            "CashReportLine",
        }:
            continue
        data = _attrs(row)
        raw_currency = (_value(data, "currency") or "").upper()
        ending_raw = _value(data, "endingCash", "endingSettledCash")
        if raw_currency == "BASE_SUMMARY":
            base_summary = _decimal(
                ending_raw,
                field="Cash Report / Ending Cash",
            )
            continue
        currency = _iso_currency(raw_currency)
        if not currency:
            continue  # excludes segment totals
        ending = _decimal(
            ending_raw,
            field="Cash Report / Ending Cash",
            required=True,
        )
        cash_balances[currency] = ending or Decimal("0")
    # Single-currency Flex reports may contain only BASE_SUMMARY. In that
    # shape the summary is already denominated in Account Information's base
    # currency. Never use it when native rows exist, because then it is a
    # converted total that would double-count those balances.
    if not cash_balances and base_summary is not None:
        cash_balances[base_currency] = base_summary
    if not cash_balances:
        _query_error(
            "Cash Report must include Currency and Ending Cash at currency-breakout level.",
        )

    funds = _first(statement, "StmtFunds", "StatementOfFunds")
    if funds is None:
        _query_error(
            "The IBKR Flex query is missing Statement of Funds.",
            code="flex_query_missing_section",
        )
    statement_funds = [
        _attrs(row)
        for row in funds.iter()
        if row is not funds and _local_name(row.tag) in {"StmtFund", "StatementOfFundsLine"}
    ]
    funds_fields = {
        "Currency": ("currency",),
        "Date": ("date", "reportDate"),
        "Amount or Debit/Credit": ("amount", "debit", "credit"),
        "Activity Description or Code": (
            "activityDescription",
            "description",
            "activityCode",
        ),
        "Transaction ID": ("transactionID", "transactionId"),
        "Trade ID": ("tradeID", "tradeId"),
        "Symbol": ("symbol",),
        "Balance": ("balance",),
    }
    for row in statement_funds:
        missing = _missing_selected_fields(row, funds_fields)
        if missing:
            _query_error(
                "Statement of Funds is missing required selected field(s): "
                + ", ".join(missing)
                + "."
            )
        transaction_id = _value(row, "transactionID", "transactionId")
        is_trade = bool(_value(row, "tradeID", "tradeId", "tradeQuantity"))
        if transaction_id and not is_trade:
            if not _iso_currency(_value(row, "currency")):
                raise ValueError("IBKR Flex Statement of Funds transaction has an invalid Currency")
            _date(
                _value(row, "date", "reportDate"),
                field="Statement of Funds / Date",
                required=True,
            )
            _statement_amount(row)

    open_positions = _first(statement, "OpenPositions")
    if open_positions is None:
        _query_error(
            "The IBKR Flex query is missing Open Positions.",
            code="flex_query_missing_section",
        )
    positions: list[dict[str, str]] = []
    position_keys: set[tuple[str, str, str]] = set()
    position_fields = {
        "Conid": ("conid",),
        "Symbol": ("symbol",),
        "Description": ("description",),
        "Asset Class": ("assetCategory", "assetClass"),
        "Currency": ("currency",),
        "Position": ("position", "quantity"),
        "Multiplier": ("multiplier",),
        "Mark Price": ("markPrice",),
        "Position Value": ("positionValue",),
        "Cost Basis Money": ("costBasisMoney",),
        "ISIN": ("isin",),
        "Side": ("side",),
        "Open Date/Time": ("openDateTime",),
    }
    for row in open_positions.iter():
        if row is open_positions or _local_name(row.tag) != "OpenPosition":
            continue
        data = _attrs(row)
        detail = (_value(data, "levelOfDetail") or "SUMMARY").upper()
        if detail not in {"SUMMARY", "S"}:
            _query_error(
                "Open Positions must use Summary level of detail, not tax lots.",
                code="flex_query_position_detail",
            )
        missing = _missing_selected_fields(data, position_fields)
        if missing:
            _query_error(
                "Open Positions is missing required selected field(s): " + ", ".join(missing) + "."
            )
        conid = _value(data, "conid")
        currency = _iso_currency(_value(data, "currency"))
        model = _value(data, "model") or "default"
        if not conid or not currency:
            _query_error("Open Positions contains a row without Conid or Currency.")
        _decimal(
            _value(data, "position", "quantity"),
            field="Open Positions / Position",
            required=True,
        )
        _decimal(
            _value(data, "multiplier"),
            field="Open Positions / Multiplier",
            required=True,
        )
        _decimal(
            _value(data, "markPrice"),
            field="Open Positions / Mark Price",
            required=True,
        )
        _decimal(
            _value(data, "positionValue"),
            field="Open Positions / Position Value",
            required=True,
        )
        _decimal(
            _value(data, "costBasisMoney"),
            field="Open Positions / Cost Basis Money",
            required=True,
        )
        open_date = _value(data, "openDateTime")
        if open_date:
            _date(open_date, field="Open Positions / Open Date")
        position_key = (account_id, model, conid)
        if position_key in position_keys:
            _query_error(
                "Open Positions contains duplicate Summary rows for the same account, "
                "model, and conid. Disable lot-level or alternate grouping."
            )
        position_keys.add(position_key)
        positions.append(data)

    return _ParsedReport(
        account_id=account_id,
        account_alias=_value(info, "accountAlias", "acctAlias"),
        account_name=_value(info, "name", "accountName"),
        base_currency=base_currency,
        cash_balances=cash_balances,
        statement_funds=statement_funds,
        positions=positions,
    )


class IbkrFlexProvider(BankProvider):
    def __init__(self) -> None:
        self._report: _ParsedReport | None = None

    @property
    def name(self) -> str:
        return "ibkr"

    @property
    def flow_type(self) -> str:
        return "token"

    def get_oauth_url(self, *args, **kwargs):  # type: ignore[override]
        raise NotImplementedError("IBKR Flex uses a token and query id, not OAuth")

    async def handle_oauth_callback(self, code: str) -> ConnectionData:
        raise NotImplementedError("Use the token connection endpoint for IBKR Flex")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=IBKR_HTTP_TIMEOUT,
            follow_redirects=False,
            headers={"Accept": "application/xml", "User-Agent": "Securo/IBKR-Flex"},
        )

    @staticmethod
    def _token(credentials: dict) -> str:
        encrypted = (credentials or {}).get("flex_token_enc")
        if not isinstance(encrypted, str):
            return ""
        return decrypt(encrypted) or ""

    @staticmethod
    def _query_id(credentials: dict) -> str:
        return str((credentials or {}).get("query_id") or "").strip()

    @staticmethod
    def _response_error(root: ElementTree.Element) -> tuple[str | None, str | None]:
        return (
            _child_text(root, "ErrorCode"),
            _child_text(root, "ErrorMessage"),
        )

    @staticmethod
    def _raise_flex_error(code: str | None, _message: str | None) -> None:
        # Never surface the upstream message verbatim: an intermediary or
        # test endpoint could echo request parameters containing the token.
        # The stable code determines a safe, actionable local message.
        if code in _USER_ACTION_ERRORS:
            raise ProviderUserActionRequired(
                _USER_ACTION_MESSAGES[code],
                code=_USER_ACTION_ERRORS[code],
                help_url=IBKR_HELP_URL,
            )
        if code == "1018":
            raise ProviderRateLimited("IBKR Flex rate limit reached. Try again later.")
        if code == "1019":
            raise ProviderTransientError("IBKR Flex is still generating the statement.")
        if code in _TRANSIENT_ERRORS:
            raise ProviderTransientError(
                f"IBKR Flex could not generate the statement (error {code}). Try again later."
            )
        # Unknown server-side codes should not permanently break an otherwise
        # healthy connection. Known credential/query failures are handled
        # above; treating future codes as transient is the safer default.
        raise ProviderTransientError(
            f"IBKR Flex returned error {code or 'unknown'}. Try again later."
        )

    def _validate_statement_url(self, url: str, *, allow_configured_host: bool = False) -> str:
        parsed = urlsplit(url)
        configured = urlsplit(get_settings().ibkr_flex_api_url)
        host = (parsed.hostname or "").lower()
        configured_host = (configured.hostname or "").lower()
        allowed = (
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and (
                host == "interactivebrokers.com"
                or host.endswith(".interactivebrokers.com")
                or (allow_configured_host and configured_host and host == configured_host)
            )
        )
        if not allowed:
            raise ValueError("IBKR Flex returned an untrusted statement URL")
        return url

    @staticmethod
    def _parse_envelope(xml_text: str | bytes) -> ElementTree.Element:
        try:
            return _parse_element(xml_text)
        except (ElementTree.ParseError, ValueError) as exc:
            raise ProviderTransientError("IBKR Flex returned malformed response XML") from exc

    @staticmethod
    def _validate_connection_input(token: str, query_id: str) -> None:
        if not token:
            raise ValueError("IBKR Flex token is required")
        if len(token) > 4096:
            raise ValueError("IBKR Flex token is too long")
        if not query_id:
            raise ValueError("IBKR Flex query id is required")
        if len(query_id) > 32 or not _QUERY_ID_RE.fullmatch(query_id):
            raise ValueError("IBKR Flex query id must contain only digits")

    async def _fetch_report(
        self, token: str, query_id: str, from_date: date, to_date: date
    ) -> _ParsedReport:
        base_url = get_settings().ibkr_flex_api_url.rstrip("/")
        statement_url = self._validate_statement_url(
            f"{base_url}/GetStatement",
            allow_configured_host=True,
        )
        params = {
            "t": token,
            "q": query_id,
            "v": "3",
            "fd": from_date.strftime("%Y%m%d"),
            "td": to_date.strftime("%Y%m%d"),
        }
        try:
            async with self._client() as client:
                response = await client.get(f"{base_url}/SendRequest", params=params)
                response.raise_for_status()
                envelope = self._parse_envelope(response.content)
                if (_child_text(envelope, "Status") or "").lower() != "success":
                    self._raise_flex_error(*self._response_error(envelope))
                reference = _child_text(envelope, "ReferenceCode")
                returned_url = _child_text(envelope, "Url", "url")
                if not reference:
                    raise ProviderTransientError("IBKR Flex did not return a reference code")
                # IBKR commonly returns a different download host (gdcdyn)
                # from the request host (ndcdyn). Official examples and mature
                # clients use this response URL, so validate it before use and
                # only fall back to the configured base when it is absent.
                if returned_url:
                    statement_url = self._validate_statement_url(returned_url)

                last_retry_code: str | None = None
                for _attempt in range(IBKR_MAX_POLLS):
                    # Ten-second spacing gives large reports up to five minutes
                    # to finish and stays comfortably below IBKR's limit of ten
                    # requests per token in any minute (including SendRequest).
                    await asyncio.sleep(IBKR_POLL_INTERVAL_SECONDS)
                    try:
                        report_response = await client.get(
                            statement_url,
                            params={"t": token, "q": reference, "v": "3"},
                        )
                        report_response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        status = exc.response.status_code
                        if status == 429:
                            last_retry_code = "1018"
                            logger.warning("IBKR Flex GetStatement was rate limited; will retry")
                            continue
                        if status in {401, 403}:
                            raise ProviderUserActionRequired(
                                "IBKR Flex rejected the credentials or source IP.",
                                code="credentials_invalid",
                                help_url=IBKR_HELP_URL,
                            ) from exc
                        # The report host occasionally returns a transient
                        # gateway/service response while the statement is
                        # being materialized. Keep polling on the same paced
                        # schedule instead of starting another Flex job.
                        if status in {408, 425} or status >= 500:
                            logger.warning(
                                "IBKR Flex GetStatement HTTP status %s; will retry",
                                status,
                            )
                            last_retry_code = f"http_{status}"
                            continue
                        raise ProviderTransientError(
                            f"IBKR Flex statement retrieval failed with HTTP {status}"
                        ) from exc
                    except httpx.TransportError as exc:
                        # Do not log str(exc): httpx exceptions can include the
                        # request URL, whose query string contains the token.
                        logger.warning(
                            "IBKR Flex GetStatement transport failure (%s); will retry",
                            type(exc).__name__,
                        )
                        last_retry_code = "transport"
                        continue
                    report_content = report_response.content
                    report_prefix = report_content.lstrip(b"\xef\xbb\xbf \t\r\n")
                    if report_prefix and not report_prefix.startswith(b"<"):
                        first_line = report_prefix.splitlines()[0][:4096]
                        if any(delimiter in first_line for delimiter in (b",", b"\t", b";")):
                            raise ProviderUserActionRequired(
                                "The Activity Flex Query output format must be XML, not Text/CSV. "
                                "Edit the saved query in IBKR, select XML, and reconnect.",
                                code="flex_query_output_format",
                                help_url=IBKR_HELP_URL,
                            )
                        raise ProviderTransientError(
                            "IBKR Flex returned an unexpected non-XML response"
                        )
                    report_root = self._parse_envelope(report_content)
                    if _local_name(report_root.tag) in {"FlexQueryResponse", "FlexStatements"}:
                        return _parse_xml(report_content)
                    code, message = self._response_error(report_root)
                    if code in _POLL_RETRY_ERRORS:
                        last_retry_code = code
                        continue
                    self._raise_flex_error(code, message)
        except ProviderUserActionRequired:
            raise
        except (ProviderRateLimited, ProviderTransientError, ValueError):
            raise
        except httpx.HTTPError as exc:
            # Never include the request URL: it contains the Flex token.
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            logger.warning(
                "IBKR Flex SendRequest HTTP failure (%s, status=%s)",
                type(exc).__name__,
                status,
            )
            if status == 429:
                raise ProviderRateLimited(
                    "IBKR Flex temporarily rate limited report generation"
                ) from exc
            if status in {401, 403}:
                raise ProviderUserActionRequired(
                    "IBKR Flex rejected the credentials or source IP.",
                    code="credentials_invalid",
                    help_url=IBKR_HELP_URL,
                ) from exc
            raise ProviderTransientError("IBKR Flex HTTP request failed") from exc

        if last_retry_code == "1018":
            raise ProviderRateLimited(
                "IBKR Flex remained rate limited while retrieving the statement"
            )
        raise ProviderTransientError(
            "IBKR Flex statement did not finish generating within five minutes"
        )

    async def connect_with_token(
        self, token: str, parameters: dict | None = None
    ) -> ConnectionData:
        cleaned_token = token.strip()
        query_id = str((parameters or {}).get("query_id") or "").strip()
        self._validate_connection_input(cleaned_token, query_id)

        # Activity Flex data is finalized once per day after IBKR's close.
        # Requesting the still-open current day can leave generation in 1019
        # indefinitely, so always end at the last completed calendar day.
        report_end = date.today() - timedelta(days=1)
        initial_start = report_end - timedelta(days=IBKR_INITIAL_HISTORY_DAYS - 1)
        self._report = await self._fetch_report(
            cleaned_token,
            query_id,
            initial_start,
            report_end,
        )
        encrypted_token = encrypt(cleaned_token)
        if not encrypted_token:
            raise RuntimeError("Could not encrypt IBKR Flex token")
        credentials = {
            "flex_token_enc": encrypted_token,
            "query_id": query_id,
        }
        label = self._report.account_alias or self._report.account_name or self._report.account_id
        return ConnectionData(
            external_id=f"ibkr:{self._report.account_id}",
            institution_name=f"Interactive Brokers - {label}",
            credentials=credentials,
            accounts=self._accounts_from_report(self._report),
            logo_url=favicon_url_for("https://www.interactivebrokers.com"),
        )

    async def prepare_sync(self, credentials: dict, since: date | None = None) -> None:
        token = self._token(credentials)
        query_id = self._query_id(credentials)
        if not token or not query_id:
            raise ProviderUserActionRequired(
                "IBKR Flex token or query id is missing. Reconnect the account.",
                code="credentials_invalid",
                help_url=IBKR_HELP_URL,
            )
        report_end = date.today() - timedelta(days=1)
        initial_start = report_end - timedelta(days=IBKR_INITIAL_HISTORY_DAYS - 1)
        start = max(
            since or initial_start,
            initial_start,
        )
        start = min(start, report_end)
        self._report = await self._fetch_report(token, query_id, start, report_end)

    async def _ensure_report(self, credentials: dict) -> _ParsedReport:
        if self._report is None:
            await self.prepare_sync(credentials)
        assert self._report is not None
        return self._report

    @staticmethod
    def _accounts_from_report(report: _ParsedReport) -> list[AccountData]:
        label = report.account_alias or report.account_name or report.account_id
        return [
            AccountData(
                external_id=f"{report.account_id}:{currency}",
                name=f"{label} Cash ({currency})",
                type="investment",
                balance=balance,
                currency=currency,
                masked_number=mask_last4(report.account_id),
            )
            for currency, balance in sorted(report.cash_balances.items())
        ]

    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        return self._accounts_from_report(await self._ensure_report(credentials))

    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: date | None = None,
        payee_source: str = "auto",
    ) -> list[TransactionData]:
        report = await self._ensure_report(credentials)
        expected_prefix = f"{report.account_id}:"
        if not account_external_id.startswith(expected_prefix):
            return []
        currency = account_external_id[len(expected_prefix) :].upper()
        transactions: list[TransactionData] = []
        seen: set[str] = set()
        for raw in report.statement_funds:
            row_currency = _iso_currency(_value(raw, "currency"))
            if row_currency != currency:
                continue
            if _value(raw, "tradeID", "tradeId", "tradeQuantity"):
                continue  # executions stay out of Securo spending activity
            transaction_id = _value(raw, "transactionID", "transactionId")
            if not transaction_id:
                continue  # starting/ending balance and aggregate rows
            txn_date = _date(
                _value(raw, "date", "reportDate"),
                field="Statement of Funds / Date",
                required=True,
            )
            if txn_date is None or (since and txn_date < since):
                continue
            signed = _statement_amount(raw)
            external_id = f"ibkr:{report.account_id}:{currency}:{transaction_id}"
            if external_id in seen:
                continue
            seen.add(external_id)
            description = (
                _value(raw, "activityDescription", "description", "activityCode")
                or "IBKR cash activity"
            )[:500]
            symbol = _value(raw, "symbol")
            transactions.append(
                TransactionData(
                    external_id=external_id[:255],
                    description=description,
                    amount=abs(signed),
                    date=txn_date,
                    type="credit" if signed >= 0 else "debit",
                    currency=currency,
                    status="posted",
                    payee=(symbol or "Interactive Brokers")[:255],
                    raw_data=raw,
                )
            )
        return transactions

    async def get_holdings(self, credentials: dict) -> list[HoldingData]:
        report = await self._ensure_report(credentials)
        holdings: list[HoldingData] = []
        for raw in report.positions:
            conid = _value(raw, "conid")
            currency = _iso_currency(_value(raw, "currency"))
            position_value = _decimal(
                _value(raw, "positionValue"),
                field="Open Positions / Position Value",
                required=True,
            )
            if not conid or not currency or position_value is None:
                raise ValueError(
                    "IBKR Flex Open Positions must include Conid, Currency and Position Value"
                )
            model = _value(raw, "model") or "default"
            quantity = _decimal(
                _value(raw, "position", "quantity"),
                field="Open Positions / Position",
                required=True,
            )
            mark_price = _decimal(_value(raw, "markPrice"), field="Open Positions / Mark Price")
            cost_basis = _decimal(
                _value(raw, "costBasisMoney"), field="Open Positions / Cost Basis Money"
            )
            symbol = _value(raw, "symbol")
            name = _value(raw, "description", "symbol") or f"IBKR position {conid}"
            holdings.append(
                HoldingData(
                    external_id=f"{report.account_id}:{model}:{conid}",
                    name=name,
                    currency=currency,
                    current_value=position_value,
                    quantity=quantity,
                    unit_price=mark_price,
                    purchase_price=cost_basis,
                    purchase_date=_date(
                        _value(raw, "openDateTime"), field="Open Positions / Open Date"
                    ),
                    isin=_value(raw, "isin"),
                    ticker=symbol[:32] if symbol else None,
                    metadata={
                        "account_id": report.account_id,
                        "model": None if model == "default" else model,
                        "conid": conid,
                        "asset_class": _value(raw, "assetCategory", "assetClass"),
                        "side": _value(raw, "side"),
                        "multiplier": _value(raw, "multiplier"),
                        "mark_price": str(mark_price) if mark_price is not None else None,
                        "security_id": _value(raw, "securityID", "securityId"),
                        "cusip": _value(raw, "cusip"),
                        "figi": _value(raw, "figi"),
                        "listing_exchange": _value(raw, "listingExchange"),
                        "underlying_conid": _value(raw, "underlyingConid"),
                        "underlying_symbol": _value(raw, "underlyingSymbol"),
                        "strike": _value(raw, "strike"),
                        "expiry": _value(raw, "expiry"),
                        "put_call": _value(raw, "putCall"),
                        "raw_data": raw,
                    },
                )
            )
        return holdings

    async def get_institution_logo(self, credentials: dict) -> str | None:
        return favicon_url_for("https://www.interactivebrokers.com")

    async def refresh_credentials(self, credentials: dict) -> dict:
        if not self._token(credentials) or not self._query_id(credentials):
            raise ProviderUserActionRequired(
                "IBKR Flex credentials are missing. Reconnect the account.",
                code="credentials_invalid",
                help_url=IBKR_HELP_URL,
            )
        return credentials
