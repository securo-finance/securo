"""Plaid bank sync provider plugin for Securo (ADR 0023, ADR 0051).

Connects institutions through Plaid Hosted Link (`oauth_url` / `oauth_exchange`)
and syncs accounts, incremental `/transactions/sync` deltas, credit/loan
`/liabilities/get` terms and statement bills, and `/investments/holdings/get` +
`/investments/transactions/get` positions and trades.

Standard library only: requires no third-party wheels or lockfile. All outbound
requests go strictly to `production.plaid.com` or `sandbox.plaid.com`, honoring
the plugin runner's `HTTPS_PROXY` egress proxy automatically.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

PLAID_ENVIRONMENTS: dict[str, str] = {
    "production": "https://production.plaid.com",
    "sandbox": "https://sandbox.plaid.com",
}
PLAID_API_VERSION = "2020-09-14"
HTTP_TIMEOUT_SECONDS = 30.0
DEFAULT_DAYS_REQUESTED = 730
MAX_DAYS_REQUESTED = 730
SYNC_PAGE_COUNT = 500
MAX_SYNC_TRANSACTIONS = 20_000
MAX_SYNC_REMOVED = 20_000
MAX_SYNC_HOLDINGS = 5_000
MAX_SYNC_TRADES = 20_000
MAX_MUTATION_RETRIES = 3

# Plaid error codes that require the user to re-authenticate via Hosted Link
# Update Mode or fix their Plaid credentials.
_AUTH_ERROR_CODES = frozenset({
    "ITEM_LOGIN_REQUIRED",
    "ITEM_LOCKED",
    "INVALID_CREDENTIALS",
    "USER_SETUP_REQUIRED",
    "PASSWORD_RESET_REQUIRED",
    "MFA_NOT_SUPPORTED",
    "INSUFFICIENT_CREDENTIALS",
    "ADDITIONAL_CONSENT_REQUIRED",
    "INVALID_ACCESS_TOKEN",
    "USER_PERMISSION_REVOKED",
    "INVALID_API_KEYS",
    "INVALID_CLIENT_ID",
    "ITEM_NOT_FOUND",
})

# Plaid error codes indicating an optional product is unavailable or not
# consented for this item/institution — safe to skip when allow_unsupported=True.
_UNSUPPORTED_PRODUCT_CODES = frozenset({
    "PRODUCTS_NOT_SUPPORTED",
    "NO_INVESTMENT_ACCOUNTS",
    "NO_LIABILITY_ACCOUNTS",
    "NO_ACCOUNTS",
    "ACCESS_NOT_GRANTED",
    "ADDITIONAL_CONSENT_REQUIRED",
    "INVALID_PRODUCT",
})

_VALID_PRODUCTS = frozenset({"transactions", "investments", "liabilities"})
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_LOAN_TERM_RE = re.compile(r"(\d+)\s*(year|yr|month|mo)", re.IGNORECASE)

Requester = Callable[[str, dict[str, Any]], dict[str, Any]]


class NeedsAuth(Exception):
    """Credentials or item login require user action in Plaid Link Update Mode."""


class RateLimited(Exception):
    """Plaid rate-limited this request; Securo keeps the connection active and retries later."""


class PlaidAPIError(Exception):
    """Structured error returned by a Plaid API endpoint (never carries secrets)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        error_type: str = "",
        error_code: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.error_code = error_code


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _money_str(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):f}"


def _units_str(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    text = f"{quantized:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _rate_str(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    text = f"{quantized:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _iso_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()[:10]
    try:
        return dt.date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def _day_of_month(value: Any) -> int | None:
    iso = _iso_date(value)
    if not iso:
        return None
    return int(iso[8:10])


def _valid_currency(value: Any) -> str | None:
    if isinstance(value, str):
        code = value.strip().upper()
        if len(code) == 3 and code.isascii() and code.isalpha():
            return code
    return None


def _currency(value: Any, fallback: str = "USD") -> str:
    return _valid_currency(value) or fallback


def _mask_last4(value: Any) -> str | None:
    if not value:
        return None
    cleaned = "".join(ch for ch in str(value) if ch.isalnum())
    if not cleaned:
        return None
    return cleaned[-4:]


def _valid_isin(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().upper()
    return cleaned if _ISIN_RE.fullmatch(cleaned) else None


def _bool_config(config: Any, key: str, default: bool = True) -> bool:
    raw = config.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _parse_csv(raw: Any, default: list[str]) -> list[str]:
    if not raw or not str(raw).strip():
        return list(default)
    items = [part.strip() for part in str(raw).split(",") if part.strip()]
    return items or list(default)


def _days_requested(config: Any) -> int:
    raw = config.get("days_requested")
    if raw is None or str(raw).strip() == "":
        return DEFAULT_DAYS_REQUESTED
    try:
        days = int(str(raw).strip())
    except ValueError as exc:
        raise ValueError("days_requested must be an integer between 1 and 730") from exc
    return max(1, min(MAX_DAYS_REQUESTED, days))


def _base_url(config: Any) -> str:
    env = str(config.get("environment") or "production").strip().lower()
    if env not in PLAID_ENVIRONMENTS:
        raise ValueError("environment must be 'production' or 'sandbox'")
    return PLAID_ENVIRONMENTS[env]


def _default_requester(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Plaid-Version": PLAID_API_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw_body = ""
        try:
            raw_body = exc.read().decode("utf-8", errors="replace")
        except OSError:
            pass
        error_type = ""
        error_code = ""
        display = f"Plaid HTTP {exc.code}"
        try:
            parsed = json.loads(raw_body) if raw_body else {}
            if isinstance(parsed, dict):
                error_type = str(parsed.get("error_type") or "")
                error_code = str(parsed.get("error_code") or "")
                msg = parsed.get("display_message") or parsed.get("error_message")
                if msg:
                    display = f"{error_code or f'HTTP {exc.code}'}: {msg}"
        except ValueError:
            pass
        raise PlaidAPIError(
            display,
            status_code=exc.code,
            error_type=error_type,
            error_code=error_code,
        ) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Plaid API: {exc.reason}") from None


def _map_account_type(plaid_type: Any, plaid_subtype: Any) -> str:
    ptype = str(plaid_type or "").strip().lower()
    subtype = str(plaid_subtype or "").strip().lower()
    if ptype == "credit":
        return "credit_card"
    if ptype == "loan":
        return "loan"
    if ptype in ("investment", "brokerage"):
        return "investment"
    if ptype == "depository":
        if subtype in ("savings", "money market", "cd", "hsa"):
            return "savings"
        if subtype in ("paypal", "prepaid", "ebt"):
            return "wallet"
        return "checking"
    return "checking"


def _map_loan_kind(subtype: Any) -> str:
    sub = str(subtype or "").strip().lower()
    if "student" in sub:
        return "student"
    if "mortgage" in sub or "home equity" in sub:
        return "mortgage"
    if "auto" in sub:
        return "auto"
    if "personal" in sub or "consumer" in sub or "line of credit" in sub:
        return "personal"
    return "other"


def _parse_loan_term_months(raw_term: Any) -> int | None:
    if not isinstance(raw_term, str):
        return None
    match = _LOAN_TERM_RE.search(raw_term)
    if not match:
        return None
    count = int(match.group(1))
    unit = match.group(2).lower()
    months = count * 12 if unit.startswith(("year", "yr")) else count
    return months if 1 <= months <= 600 else None


def _months_between(start_iso: str | None, end_iso: str | None) -> int | None:
    if not start_iso or not end_iso:
        return None
    try:
        start = dt.date.fromisoformat(start_iso)
        end = dt.date.fromisoformat(end_iso)
    except ValueError:
        return None
    months = (end.year - start.year) * 12 + (end.month - start.month)
    return months if 1 <= months <= 600 else None


def _map_holding_type(security_type: Any) -> str:
    kind = str(security_type or "").strip().lower()
    if kind == "equity":
        return "stock"
    if kind == "etf":
        return "etf"
    if kind == "mutual fund":
        return "fund"
    if kind == "cryptocurrency":
        return "crypto"
    return "investment"


def _format_provider_category(txn: dict[str, Any]) -> str | None:
    pfc = txn.get("personal_finance_category")
    if isinstance(pfc, dict):
        primary = str(pfc.get("primary") or "").strip()
        detailed = str(pfc.get("detailed") or "").strip()
        if detailed and primary and detailed.startswith(f"{primary}_"):
            detail = detailed[len(primary) + 1 :].replace("_", " ").title()
            label = f"{primary.replace('_', ' ').title()} - {detail}"
        elif primary:
            label = primary.replace("_", " ").title()
        elif detailed:
            label = detailed.replace("_", " ").title()
        else:
            label = ""
        if label:
            return label[:100]
    legacy = txn.get("category")
    if isinstance(legacy, list) and legacy:
        label = " - ".join(str(part).strip() for part in legacy if part)
        if label:
            return label[:100]
    return None


def _build_transaction(txn: dict[str, Any]) -> dict[str, Any] | None:
    ext_id = str(txn.get("transaction_id") or "").strip()
    if not ext_id:
        return None
    raw_amount = _decimal(txn.get("amount"))
    if raw_amount is None:
        return None
    # Plaid sign convention: positive = outflow (debit), negative = inflow (credit).
    txn_type = "credit" if raw_amount < 0 else "debit"
    amount = raw_amount.copy_abs()
    txn_date = _iso_date(txn.get("date")) or _iso_date(txn.get("authorized_date"))
    if not txn_date:
        return None

    merchant = str(txn.get("merchant_name") or "").strip()
    name = str(txn.get("name") or "").strip()
    original = str(txn.get("original_description") or "").strip()
    description = (merchant or name or original or "Transaction")[:500]

    counterparties = txn.get("counterparties")
    counterparty_name = ""
    if isinstance(counterparties, list):
        for cp in counterparties:
            if isinstance(cp, dict) and str(cp.get("name") or "").strip():
                counterparty_name = str(cp["name"]).strip()
                break
    payee = (merchant or counterparty_name or None)
    if payee:
        payee = payee[:200]

    # Put the fuller bank statement text in notes when merchant_name shortened description.
    note_source = original or name
    notes = (
        note_source[:1000]
        if note_source and note_source.casefold() != description.casefold()
        else None
    )

    is_pending = bool(txn.get("pending"))
    status = "pending" if is_pending else "posted"
    replaces = str(txn.get("pending_transaction_id") or "").strip() or None
    if is_pending or replaces == ext_id:
        replaces = None

    record: dict[str, Any] = {
        "external_id": ext_id[:255],
        "description": description,
        "amount": _money_str(amount),
        "date": txn_date,
        "type": txn_type,
        "status": status,
    }
    currency = _valid_currency(txn.get("iso_currency_code")) or _valid_currency(
        txn.get("unofficial_currency_code")
    )
    if currency:
        record["currency"] = currency
    if payee:
        record["payee"] = payee
    if notes:
        record["notes"] = notes
    category = _format_provider_category(txn)
    if category:
        record["provider_category"] = category
    if replaces:
        record["replaces_external_id"] = replaces[:255]
    return record


class PlaidPlugin:
    """Securo provider plugin for the Plaid API."""

    def __init__(self, requester: Requester | None = None) -> None:
        self._requester: Requester = requester or _default_requester

    def _call_plaid(
        self,
        context: Any,
        endpoint: str,
        body: dict[str, Any],
        *,
        allow_unsupported: bool = False,
    ) -> dict[str, Any] | None:
        client_id = str(context.config.get("client_id") or "").strip()
        secret = str(context.secrets.get("secret") or "").strip()
        if not client_id or not secret:
            raise NeedsAuth("Plaid client_id and secret are required")
        url = f"{_base_url(context.config)}{endpoint}"
        payload = {"client_id": client_id, "secret": secret, **body}
        try:
            return self._requester(url, payload)
        except PlaidAPIError as exc:
            if exc.status_code == 429 or exc.error_type == "RATE_LIMIT_EXCEEDED":
                raise RateLimited(f"Plaid rate limit reached on {endpoint}") from None
            if allow_unsupported and exc.error_code in _UNSUPPORTED_PRODUCT_CODES:
                return None
            if exc.error_code in _AUTH_ERROR_CODES:
                raise NeedsAuth(
                    f"Plaid sign-in needed ({exc.error_code}); reconnect your institution."
                ) from None
            raise

    # ------------------------------------------------------------------
    # Hosted Link sign-in & Update Mode
    # ------------------------------------------------------------------

    def oauth_url(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        """Create a Plaid Hosted Link session (new item or Update Mode reconnect)."""
        redirect_uri = str(params.get("redirect_uri") or "").strip()
        client_user_id = str(context.securo.get("connection_id") or "securo-user")
        client_name = str(context.config.get("client_name") or "Securo").strip() or "Securo"
        country_codes = [c.upper() for c in _parse_csv(context.config.get("country_codes"), ["US"])]

        body: dict[str, Any] = {
            "client_name": client_name[:30],
            "language": "en",
            "country_codes": country_codes,
            "user": {"client_user_id": client_user_id},
            "hosted_link": {"completion_redirect_uri": redirect_uri} if redirect_uri else {},
        }

        existing_access_token = str(context.secrets.get("access_token") or "").strip()
        if existing_access_token:
            # Update Mode: fixes ITEM_LOGIN_REQUIRED without creating a new Item.
            body["access_token"] = existing_access_token
        else:
            primary_products = [
                p.lower()
                for p in _parse_csv(context.config.get("products"), ["transactions"])
                if p.lower() in _VALID_PRODUCTS
            ]
            if not primary_products:
                primary_products = ["transactions"]
            optional_products: list[str] = []
            if _bool_config(context.config, "enable_investments", True) and "investments" not in primary_products:
                optional_products.append("investments")
            if _bool_config(context.config, "enable_liabilities", True) and "liabilities" not in primary_products:
                optional_products.append("liabilities")
            if "transactions" not in primary_products:
                optional_products.append("transactions")
            body["products"] = primary_products
            if optional_products:
                body["optional_products"] = optional_products
            if "transactions" in primary_products or "transactions" in optional_products:
                body["transactions"] = {"days_requested": _days_requested(context.config)}

        response = self._call_plaid(context, "/link/token/create", body) or {}
        hosted_url = response.get("hosted_link_url")
        link_token = response.get("link_token")
        if not isinstance(hosted_url, str) or not hosted_url.startswith("https://") or not link_token:
            raise RuntimeError("Plaid did not return a Hosted Link URL")
        return {
            "url": hosted_url,
            "secrets": {"pending_link_token": str(link_token)},
        }

    def oauth_exchange(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        """Finish a Plaid Hosted Link session via `/link/token/get` and token exchange."""
        raw_params = params.get("params")
        callback_params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
        if str(callback_params.get("status") or "").lower() == "exited":
            raise RuntimeError("Plaid Link was closed before finishing")

        existing_access_token = str(context.secrets.get("access_token") or "").strip()
        pending_link_token = str(context.secrets.get("pending_link_token") or "").strip()

        public_token = str(params.get("code") or callback_params.get("public_token") or "").strip()
        institution_name: str | None = None
        institution_id: str | None = None

        if pending_link_token:
            token_info = self._call_plaid(
                context, "/link/token/get", {"link_token": pending_link_token}
            ) or {}
            sessions = token_info.get("link_sessions") or []
            if not sessions and not public_token:
                raise RuntimeError("Plaid Hosted Link session has not completed yet")
            if sessions:
                dict_sessions = [s for s in sessions if isinstance(s, dict)]
                finished_sessions = [
                    (idx, s)
                    for idx, s in enumerate(dict_sessions)
                    if s.get("finished_at")
                    or s.get("exit") is not None
                    or s.get("on_exit") is not None
                    or (
                        isinstance(s.get("results"), dict)
                        and bool(s["results"].get("item_add_results"))
                    )
                    or isinstance(s.get("on_success"), dict)
                ]
                if finished_sessions:
                    finished_sessions.sort(
                        key=lambda item: (str(item[1].get("finished_at") or ""), item[0])
                    )
                    latest = finished_sessions[-1][1]
                else:
                    latest = dict_sessions[-1] if dict_sessions else {}
                exit_info = (
                    latest.get("exit")
                    if latest.get("exit") is not None
                    else latest.get("on_exit")
                )
                if exit_info is not None:
                    err = exit_info.get("error") if isinstance(exit_info, dict) else None
                    if isinstance(err, dict) and err:
                        msg = (
                            err.get("display_message")
                            or err.get("error_message")
                            or "Link exited with error"
                        )
                        raise RuntimeError(f"Plaid Link failed: {msg}")
                    raise RuntimeError("Plaid Link was closed before finishing")

                results = latest.get("results") or {}
                item_adds = results.get("item_add_results") or []
                if item_adds and isinstance(item_adds[0], dict):
                    first = item_adds[0]
                    if not public_token:
                        public_token = str(first.get("public_token") or "").strip()
                    inst = first.get("institution")
                    if isinstance(inst, dict):
                        institution_name = str(inst.get("name") or "").strip() or None
                        institution_id = str(inst.get("institution_id") or "").strip() or None
                elif isinstance(latest.get("on_success"), dict):
                    on_success = latest["on_success"]
                    if not public_token:
                        public_token = str(on_success.get("public_token") or "").strip()
                    meta = on_success.get("metadata")
                    inst = meta.get("institution") if isinstance(meta, dict) else None
                    if isinstance(inst, dict):
                        institution_name = str(inst.get("name") or "").strip() or None
                        institution_id = str(inst.get("institution_id") or "").strip() or None
                elif existing_access_token:
                    # Update Mode completes without issuing a new public_token.
                    if not latest.get("finished_at"):
                        raise RuntimeError("Plaid Update Mode session did not finish")
                    return {
                        "secrets": {
                            "access_token": existing_access_token,
                            "pending_link_token": None,
                        }
                    }

        if not public_token:
            if existing_access_token:
                raise RuntimeError("Plaid Update Mode could not be verified")
            raise RuntimeError("Plaid Hosted Link did not return a public token")

        exchanged = self._call_plaid(
            context, "/item/public_token/exchange", {"public_token": public_token}
        ) or {}
        access_token = str(exchanged.get("access_token") or "").strip()
        item_id = str(exchanged.get("item_id") or "").strip()
        if not access_token:
            raise RuntimeError("Plaid did not return an access token")

        next_state = dict(params.get("connection_state") or {})
        if institution_name:
            next_state["institution_name"] = institution_name
        if institution_id:
            next_state["institution_id"] = institution_id

        return {
            "secrets": {
                "access_token": access_token,
                "item_id": item_id or None,
                "pending_link_token": None,
            },
            "state": next_state,
        }

    # ------------------------------------------------------------------
    # Sync pipeline
    # ------------------------------------------------------------------

    def _resolve_institution(
        self, context: Any, item: dict[str, Any], state: dict[str, Any]
    ) -> tuple[str | None, str | None]:
        inst_id = str(item.get("institution_id") or state.get("institution_id") or "").strip() or None
        inst_name = str(state.get("institution_name") or "").strip() or None
        if inst_id and not inst_name:
            country_codes = [c.upper() for c in _parse_csv(context.config.get("country_codes"), ["US"])]
            try:
                resp = self._call_plaid(
                    context,
                    "/institutions/get_by_id",
                    {"institution_id": inst_id, "country_codes": country_codes},
                    allow_unsupported=True,
                )
                inst = (resp or {}).get("institution")
                if isinstance(inst, dict) and inst.get("name"):
                    inst_name = str(inst["name"]).strip()
            except PlaidAPIError:
                pass
        return inst_id, inst_name

    def _sync_transactions(
        self,
        context: Any,
        access_token: str,
        initial_cursor: str | None,
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[str]], str | None, str]:
        """Pull incremental transaction updates from `/transactions/sync`.

        Returns ``(txns_by_account, removed_by_account, next_cursor, status)``
        where ``status`` is ``"ready"``, ``"not_ready"``, or ``"unsupported"``.
        """
        for _attempt in range(MAX_MUTATION_RETRIES):
            cursor = initial_cursor
            committed_cursor = initial_cursor
            txns_by_account: dict[str, dict[str, dict[str, Any]]] = {}
            removed_by_account: dict[str, set[str]] = {}
            total_txns = 0
            total_removed = 0
            mutation_restarted = False

            while True:
                req: dict[str, Any] = {
                    "access_token": access_token,
                    "count": SYNC_PAGE_COUNT,
                    "options": {
                        "include_original_description": True,
                        "include_personal_finance_category": True,
                    },
                }
                if cursor:
                    req["cursor"] = cursor
                try:
                    resp = self._call_plaid(
                        context, "/transactions/sync", req, allow_unsupported=True
                    )
                except PlaidAPIError as exc:
                    if exc.error_code == "PRODUCT_NOT_READY":
                        return {}, {}, initial_cursor, "not_ready"
                    if exc.error_code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                        mutation_restarted = True
                        break
                    raise
                if resp is None:
                    return {}, {}, initial_cursor, "unsupported"

                page_added = list(resp.get("added") or []) + list(resp.get("modified") or [])
                page_removed = list(resp.get("removed") or [])
                next_page_cursor = str(resp.get("next_cursor") or "")
                has_more = bool(resp.get("has_more"))

                if (
                    resp.get("transactions_update_status") == "NOT_READY"
                    and committed_cursor == initial_cursor
                    and total_txns == 0
                    and total_removed == 0
                    and not page_added
                    and not page_removed
                    and not has_more
                    and not next_page_cursor
                ):
                    return {}, {}, initial_cursor, "not_ready"

                # Respect Securo's contract caps by stopping at the previous
                # page boundary when adding this page would exceed the limit.
                if (
                    (committed_cursor != initial_cursor or total_txns > 0 or total_removed > 0)
                    and (
                        total_txns + len(page_added) > MAX_SYNC_TRANSACTIONS
                        or total_removed + len(page_removed) > MAX_SYNC_REMOVED
                    )
                ):
                    break

                for raw_txn in page_added:
                    if not isinstance(raw_txn, dict):
                        continue
                    acc_id = str(raw_txn.get("account_id") or "").strip()
                    built = _build_transaction(raw_txn)
                    if not acc_id or built is None:
                        continue
                    ext_id = built["external_id"]
                    bucket = txns_by_account.setdefault(acc_id, {})
                    if ext_id not in bucket:
                        total_txns += 1
                    bucket[ext_id] = built
                    if acc_id in removed_by_account and ext_id in removed_by_account[acc_id]:
                        removed_by_account[acc_id].discard(ext_id)
                        total_removed -= 1

                for rem in page_removed:
                    if not isinstance(rem, dict):
                        continue
                    rem_id = str(rem.get("transaction_id") or "").strip()
                    if not rem_id:
                        continue
                    acc_id = str(rem.get("account_id") or "").strip() or "*"
                    if acc_id in txns_by_account and rem_id in txns_by_account[acc_id]:
                        del txns_by_account[acc_id][rem_id]
                        total_txns -= 1
                    rem_bucket = removed_by_account.setdefault(acc_id, set())
                    if rem_id not in rem_bucket:
                        rem_bucket.add(rem_id)
                        total_removed += 1

                if has_more and (not next_page_cursor or next_page_cursor == cursor):
                    raise RuntimeError(
                        "Plaid /transactions/sync returned has_more=True without advancing next_cursor"
                    )
                cursor = next_page_cursor or cursor
                committed_cursor = cursor
                if not has_more:
                    break

            if not mutation_restarted:
                return (
                    {acc: list(rows.values()) for acc, rows in txns_by_account.items()},
                    {acc: sorted(ids) for acc, ids in removed_by_account.items()},
                    committed_cursor,
                    "ready",
                )

        raise RuntimeError("Plaid transactions changed repeatedly during pagination; retry on next sync")

    def _fetch_liabilities(
        self, context: Any, access_token: str
    ) -> dict[str, dict[str, Any]]:
        """Fetch `/liabilities/get`, returning ``{account_id: {"kind": ..., "data": ...}}``."""
        try:
            resp = self._call_plaid(
                context,
                "/liabilities/get",
                {"access_token": access_token},
                allow_unsupported=True,
            )
        except PlaidAPIError as exc:
            if exc.error_code == "PRODUCT_NOT_READY":
                return {}
            raise
        if not resp:
            return {}
        liabilities = resp.get("liabilities") or {}
        by_account: dict[str, dict[str, Any]] = {}
        for kind in ("credit", "student", "mortgage", "loan", "line_of_credit"):
            for entry in liabilities.get(kind) or []:
                if isinstance(entry, dict) and entry.get("account_id"):
                    by_account[str(entry["account_id"])] = {"kind": kind, "data": entry}
        return by_account

    def _fetch_investments(
        self,
        context: Any,
        access_token: str,
        accounts_by_id: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]] | None, dict[str, Decimal]]:
        """Fetch investment holdings and trades, returning ``(holdings, auto_invested_by_account)``."""
        try:
            resp = self._call_plaid(
                context,
                "/investments/holdings/get",
                {"access_token": access_token},
                allow_unsupported=True,
            )
        except PlaidAPIError as exc:
            if exc.error_code == "PRODUCT_NOT_READY":
                return None, {}
            raise
        if resp is None:
            return None, {}

        securities: dict[str, dict[str, Any]] = {
            str(s["security_id"]): s
            for s in resp.get("securities") or []
            if isinstance(s, dict) and s.get("security_id")
        }

        trades_by_key = self._fetch_investment_trades(context, access_token)

        holdings: list[dict[str, Any]] = []
        auto_invested: dict[str, Decimal] = {}
        seen_holding_ids: set[str] = set()

        for raw in resp.get("holdings") or []:
            if not isinstance(raw, dict):
                continue
            acc_id = str(raw.get("account_id") or "").strip()
            sec_id = str(raw.get("security_id") or "").strip()
            if not acc_id or not sec_id:
                continue
            sec = securities.get(sec_id) or {}
            # Leave cash-equivalent sweep funds inside the investment account's
            # cash balance rather than duplicating them as securities.
            if sec.get("is_cash_equivalent") or str(sec.get("type") or "").strip().lower() == "cash":
                continue

            current_value = _decimal(raw.get("institution_value"))
            if current_value is None:
                continue
            ext_id = f"{acc_id}:{sec_id}"[:255]
            if ext_id in seen_holding_ids:
                continue
            seen_holding_ids.add(ext_id)

            acc_info = accounts_by_id.get(acc_id) or {}
            acc_currency = acc_info.get("currency") or "USD"
            raw_currency = (
                raw.get("iso_currency_code")
                or raw.get("unofficial_currency_code")
                or sec.get("iso_currency_code")
                or sec.get("unofficial_currency_code")
            )
            if isinstance(raw_currency, str) and raw_currency.strip():
                currency = _valid_currency(raw_currency)
                if currency is None:
                    # Skip holdings in non-ISO 4+ char unofficial codes (e.g. USDT)
                    # that Securo's 3-letter Currency contract cannot represent.
                    continue
            else:
                currency = acc_currency
            holding: dict[str, Any] = {
                "external_id": ext_id,
                "name": str(sec.get("name") or sec.get("ticker_symbol") or sec_id).strip()[:255],
                "type": _map_holding_type(sec.get("type")),
                "currency": currency,
                "current_value": _units_str(current_value),
                "account_external_id": acc_id,
            }
            if acc_info.get("name"):
                holding["account_name"] = str(acc_info["name"])[:255]
            qty = _decimal(raw.get("quantity"))
            if qty is not None:
                holding["quantity"] = _units_str(qty)
            price = _decimal(raw.get("institution_price"))
            if price is not None and price >= 0:
                holding["unit_price"] = _units_str(price)
            cost_basis = _decimal(raw.get("cost_basis"))
            if cost_basis is not None and cost_basis >= 0:
                holding["purchase_price"] = _money_str(cost_basis)
            ticker = str(sec.get("ticker_symbol") or "").strip().upper()[:32]
            if ticker:
                holding["ticker"] = ticker
            isin = _valid_isin(sec.get("isin"))
            if isin:
                holding["isin"] = isin
            if trades_by_key is not None:
                holding["trades"] = trades_by_key.get((acc_id, sec_id), [])

            if len(holdings) < MAX_SYNC_HOLDINGS:
                holdings.append(holding)
                if current_value > 0 and currency == acc_currency:
                    auto_invested[acc_id] = auto_invested.get(acc_id, Decimal(0)) + current_value

        return holdings, auto_invested

    def _fetch_investment_trades(
        self, context: Any, access_token: str
    ) -> dict[tuple[str, str], list[dict[str, Any]]] | None:
        end_date = dt.datetime.now(dt.timezone.utc).date()
        start_date = end_date - dt.timedelta(days=_days_requested(context.config))
        trades_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
        seen_ids: set[str] = set()
        offset = 0
        total_trades = 0

        while True:
            try:
                resp = self._call_plaid(
                    context,
                    "/investments/transactions/get",
                    {
                        "access_token": access_token,
                        "start_date": start_date.isoformat(),
                        "end_date": end_date.isoformat(),
                        "options": {"count": SYNC_PAGE_COUNT, "offset": offset},
                    },
                    allow_unsupported=True,
                )
            except PlaidAPIError as exc:
                if exc.error_code == "PRODUCT_NOT_READY":
                    return None
                raise
            if resp is None:
                return None

            rows = resp.get("investment_transactions") or []
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                tx_type = str(raw.get("type") or "").strip().lower()
                if tx_type not in ("buy", "sell"):
                    continue
                acc_id = str(raw.get("account_id") or "").strip()
                sec_id = str(raw.get("security_id") or "").strip()
                ext_id = str(raw.get("investment_transaction_id") or "").strip()[:255]
                trade_date = _iso_date(raw.get("date"))
                qty = _decimal(raw.get("quantity"))
                price = _decimal(raw.get("price"))
                if not acc_id or not sec_id or not ext_id or not trade_date:
                    continue
                if ext_id in seen_ids or qty is None or qty == 0 or price is None or price < 0:
                    continue
                if total_trades >= MAX_SYNC_TRADES:
                    return trades_by_key
                seen_ids.add(ext_id)
                fee = _decimal(raw.get("fees"))
                trade: dict[str, Any] = {
                    "external_id": ext_id,
                    "kind": tx_type,
                    "date": trade_date,
                    "quantity": _units_str(qty.copy_abs()),
                    "price": _units_str(price),
                }
                if fee is not None and fee != 0:
                    trade["fee"] = _money_str(fee.copy_abs())
                trades_by_key.setdefault((acc_id, sec_id), []).append(trade)
                total_trades += 1

            offset += len(rows)
            total_available = int(resp.get("total_investment_transactions") or 0)
            if not rows or offset >= total_available:
                break

        return trades_by_key

    def _apply_liability(
        self,
        account_payload: dict[str, Any],
        liability_entry: dict[str, Any],
        currency: str,
    ) -> None:
        kind = liability_entry["kind"]
        data = liability_entry["data"]

        if kind == "credit" and account_payload["type"] == "credit_card":
            min_pay = _decimal(data.get("minimum_payment_amount"))
            if min_pay is not None and min_pay >= 0:
                account_payload["minimum_payment"] = _money_str(min_pay)
            due_day = _day_of_month(data.get("next_payment_due_date"))
            if due_day is not None:
                account_payload["payment_due_day"] = due_day
            close_day = _day_of_month(data.get("last_statement_issue_date"))
            if close_day is not None:
                account_payload["statement_close_day"] = close_day
            stmt_date = _iso_date(data.get("last_statement_issue_date"))
            stmt_balance = _decimal(data.get("last_statement_balance"))
            due_date = _iso_date(data.get("next_payment_due_date")) or stmt_date
            if stmt_date and stmt_balance is not None and due_date:
                bill: dict[str, Any] = {
                    "external_id": f"plaid-stmt-{account_payload['external_id']}-{stmt_date}"[:255],
                    "due_date": due_date,
                    "total_amount": _money_str(stmt_balance),
                    "currency": currency,
                }
                if min_pay is not None and min_pay >= 0:
                    bill["minimum_payment"] = _money_str(min_pay)
                account_payload["bills"] = [bill]

        elif kind == "student" and account_payload["type"] == "loan":
            account_payload["loan_kind"] = "student"
            repayment = data.get("repayment_plan")
            plan_type = (
                str(repayment.get("type") or "").strip().lower() or None
                if isinstance(repayment, dict)
                else None
            )
            if plan_type in (None, "standard", "extended standard"):
                account_payload["loan_amortization"] = "price"
            orig_amt = _decimal(data.get("origination_principal_amount"))
            if orig_amt is not None and orig_amt > 0:
                account_payload["loan_original_amount"] = _money_str(orig_amt)
            rate = _decimal(data.get("interest_rate_percentage"))
            if rate is not None and Decimal(0) <= rate <= Decimal(100):
                account_payload["loan_interest_rate"] = _rate_str(rate)
                account_payload["loan_rate_period"] = "yearly_nominal"
            orig_date = _iso_date(data.get("origination_date"))
            if not orig_date:
                disbursements = data.get("disbursement_dates") or []
                if isinstance(disbursements, list) and disbursements:
                    orig_date = _iso_date(disbursements[0])
            if orig_date:
                account_payload["loan_first_payment_date"] = orig_date
            term = _months_between(orig_date, _iso_date(data.get("expected_payoff_date")))
            if term is not None:
                account_payload["loan_term_months"] = term
            due_day = _day_of_month(data.get("next_payment_due_date"))
            if due_day is not None:
                account_payload["payment_due_day"] = due_day
            min_pay = _decimal(data.get("minimum_payment_amount"))
            if min_pay is not None and min_pay >= 0:
                account_payload["minimum_payment"] = _money_str(min_pay)

        elif kind == "mortgage" and account_payload["type"] == "loan":
            account_payload["loan_kind"] = "mortgage"
            account_payload["loan_amortization"] = "price"
            orig_amt = _decimal(data.get("origination_principal_amount"))
            if orig_amt is not None and orig_amt > 0:
                account_payload["loan_original_amount"] = _money_str(orig_amt)
            interest = data.get("interest_rate") if isinstance(data.get("interest_rate"), dict) else {}
            rate = _decimal(interest.get("percentage"))
            if rate is not None and Decimal(0) <= rate <= Decimal(100):
                account_payload["loan_interest_rate"] = _rate_str(rate)
                account_payload["loan_rate_period"] = "yearly_nominal"
            orig_date = _iso_date(data.get("origination_date"))
            if orig_date:
                account_payload["loan_first_payment_date"] = orig_date
            term = _parse_loan_term_months(data.get("loan_term"))
            if term is None:
                term = _months_between(orig_date, _iso_date(data.get("maturity_date")))
            if term is not None:
                account_payload["loan_term_months"] = term
            due_day = _day_of_month(data.get("next_payment_due_date"))
            if due_day is not None:
                account_payload["payment_due_day"] = due_day
            next_pmt = _decimal(data.get("next_monthly_payment"))
            if next_pmt is not None and next_pmt >= 0:
                account_payload["minimum_payment"] = _money_str(next_pmt)

        elif kind == "loan" and account_payload["type"] == "loan":
            freq = str(data.get("payment_frequency") or "").strip().lower() or None
            if freq in (None, "monthly"):
                account_payload["loan_amortization"] = "price"
            orig_amt = _decimal(data.get("origination_principal_amount"))
            if orig_amt is not None and orig_amt > 0:
                account_payload["loan_original_amount"] = _money_str(orig_amt)
            interest = data.get("interest_rate") if isinstance(data.get("interest_rate"), dict) else {}
            rate = _decimal(interest.get("percentage"))
            if rate is not None and Decimal(0) <= rate <= Decimal(100):
                account_payload["loan_interest_rate"] = _rate_str(rate)
                account_payload["loan_rate_period"] = "yearly_nominal"
            orig_date = _iso_date(data.get("origination_date"))
            if orig_date:
                account_payload["loan_first_payment_date"] = orig_date
            raw_term = data.get("loan_term_months")
            term: int | None = (
                int(raw_term)
                if isinstance(raw_term, int) and not isinstance(raw_term, bool) and 1 <= raw_term <= 600
                else None
            )
            if term is None:
                term = _months_between(orig_date, _iso_date(data.get("maturity_date")))
            if term is not None:
                account_payload["loan_term_months"] = term
            due_day = _day_of_month(data.get("next_payment_due_date"))
            if due_day is not None:
                account_payload["payment_due_day"] = due_day
            next_pmt = _decimal(data.get("next_payment_amount"))
            if next_pmt is not None and next_pmt >= 0:
                account_payload["minimum_payment"] = _money_str(next_pmt)

        elif kind == "line_of_credit":
            due_day = _day_of_month(data.get("next_payment_due_date"))
            if due_day is not None:
                account_payload["payment_due_day"] = due_day
            min_pay = _decimal(data.get("minimum_payment_amount"))
            if min_pay is None:
                min_pay = _decimal(data.get("next_payment_amount"))
            if min_pay is not None and min_pay >= 0:
                account_payload["minimum_payment"] = _money_str(min_pay)
            if account_payload["type"] == "credit_card":
                limit = _decimal(data.get("credit_limit"))
                if limit is not None and limit >= 0 and "credit_limit" not in account_payload:
                    account_payload["credit_limit"] = _money_str(limit)
                close_day = _day_of_month(data.get("last_statement_issue_date"))
                if close_day is not None:
                    account_payload["statement_close_day"] = close_day
                stmt_date = _iso_date(data.get("last_statement_issue_date"))
                stmt_balance = _decimal(data.get("last_statement_balance"))
                due_date = _iso_date(data.get("next_payment_due_date")) or stmt_date
                if stmt_date and stmt_balance is not None and due_date:
                    bill = {
                        "external_id": f"plaid-stmt-{account_payload['external_id']}-{stmt_date}"[:255],
                        "due_date": due_date,
                        "total_amount": _money_str(stmt_balance),
                        "currency": currency,
                    }
                    if min_pay is not None and min_pay >= 0:
                        bill["minimum_payment"] = _money_str(min_pay)
                    account_payload["bills"] = [bill]

    def sync(self, params: dict[str, Any], context: Any) -> dict[str, Any]:
        """Run one incremental Plaid sync and return contract v1 records."""
        access_token = str(context.secrets.get("access_token") or "").strip()
        if not access_token:
            raise NeedsAuth("Complete Plaid Hosted Link sign-in before syncing")

        state = dict(params.get("state") or {})
        accounts_resp = self._call_plaid(context, "/accounts/get", {"access_token": access_token}) or {}
        raw_accounts = accounts_resp.get("accounts") or []
        raw_item = accounts_resp.get("item")
        item: dict[str, Any] = raw_item if isinstance(raw_item, dict) else {}
        inst_id, inst_name = self._resolve_institution(context, item, state)
        if inst_id:
            state["institution_id"] = inst_id
        if inst_name:
            state["institution_name"] = inst_name

        accounts_by_id: dict[str, dict[str, Any]] = {}
        has_txn_accounts = False
        has_liability_accounts = False
        has_investment_accounts = False

        for raw in raw_accounts:
            if not isinstance(raw, dict) or not raw.get("account_id"):
                continue
            acc_id = str(raw["account_id"]).strip()
            acc_type = _map_account_type(raw.get("type"), raw.get("subtype"))
            balances = raw.get("balances") if isinstance(raw.get("balances"), dict) else {}
            currency = _currency(
                balances.get("iso_currency_code") or balances.get("unofficial_currency_code"),
                "USD",
            )
            name = str(raw.get("name") or raw.get("official_name") or "Account").strip()[:255]
            accounts_by_id[acc_id] = {
                "raw": raw,
                "type": acc_type,
                "currency": currency,
                "name": name,
                "balances": balances,
            }
            if acc_type in ("checking", "savings", "credit_card", "wallet", "loan"):
                has_txn_accounts = True
            if acc_type in ("credit_card", "loan"):
                has_liability_accounts = True
            if acc_type == "investment":
                has_investment_accounts = True

        # 1. Incremental transactions via /transactions/sync
        txns_by_account: dict[str, list[dict[str, Any]]] = {}
        removed_by_account: dict[str, list[str]] = {}
        txns_status = "unsupported"
        if has_txn_accounts:
            txns_by_account, removed_by_account, next_cursor, txns_status = self._sync_transactions(
                context, access_token, state.get("transactions_cursor")
            )
            if next_cursor:
                state["transactions_cursor"] = next_cursor
            elif (
                txns_status == "not_ready"
                and not state.get("transactions_cursor")
                and not has_liability_accounts
                and not has_investment_accounts
            ):
                # Initial history is still being prepared by Plaid; retry next run.
                return {"state": state, "data": None}

        # 2. Credit & loan liabilities
        liabilities_by_account: dict[str, dict[str, Any]] = {}
        if has_liability_accounts and _bool_config(context.config, "enable_liabilities", True):
            liabilities_by_account = self._fetch_liabilities(context, access_token)

        # 3. Investment holdings & trades
        holdings_payload: list[dict[str, Any]] | None = None
        auto_invested_by_account: dict[str, Decimal] = {}
        if has_investment_accounts and _bool_config(context.config, "enable_investments", True):
            holdings_payload, auto_invested_by_account = self._fetch_investments(
                context, access_token, accounts_by_id
            )

        wildcard_removed = removed_by_account.get("*", [])
        accounts_payload: list[dict[str, Any]] = []

        for acc_id, info in accounts_by_id.items():
            raw = info["raw"]
            balances = info["balances"]
            acc_type = info["type"]
            currency = info["currency"]
            current_balance = _decimal(balances.get("current"))
            available_balance = _decimal(balances.get("available"))
            balance = current_balance if current_balance is not None else (available_balance or Decimal(0))

            entry: dict[str, Any] = {
                "external_id": acc_id[:255],
                "name": info["name"],
                "type": acc_type,
                "balance": _money_str(balance),
                "currency": currency,
            }
            persistent_id = str(raw.get("persistent_account_id") or "").strip()
            if persistent_id:
                entry["stable_external_id"] = persistent_id[:255]
            mask = _mask_last4(raw.get("mask"))
            if mask:
                entry["masked_number"] = mask
            if inst_id:
                entry["institution_external_id"] = inst_id[:255]
            if inst_name:
                entry["institution_name"] = inst_name[:255]

            if acc_type == "credit_card":
                limit = _decimal(balances.get("limit"))
                if limit is not None and limit >= 0:
                    entry["credit_limit"] = _money_str(limit)
                # Only report available_credit when current balance is absent;
                # Securo derives debt as `credit_limit - available_credit` when both are set.
                if current_balance is None and available_balance is not None and available_balance >= 0:
                    entry["available_credit"] = _money_str(available_balance)
            elif acc_type == "loan":
                entry["loan_kind"] = _map_loan_kind(raw.get("subtype"))
            elif acc_type == "investment":
                auto_inv = auto_invested_by_account.get(acc_id)
                if auto_inv is not None and auto_inv > 0:
                    entry["auto_invested_balance"] = _money_str(auto_inv)

            liability = liabilities_by_account.get(acc_id)
            if liability is not None:
                self._apply_liability(entry, liability, currency)

            if acc_type in ("checking", "savings", "credit_card", "wallet", "loan"):
                if txns_status == "ready":
                    acc_txns = txns_by_account.get(acc_id, [])
                    tx_ext_ids = {t["external_id"] for t in acc_txns}
                    acc_removed = [
                        rid
                        for rid in dict.fromkeys([*removed_by_account.get(acc_id, []), *wildcard_removed])
                        if rid not in tx_ext_ids
                    ]
                    entry["incremental"] = True
                    entry["transactions"] = acc_txns
                    if acc_removed:
                        entry["removed_transaction_ids"] = acc_removed
                elif txns_status == "unsupported":
                    # Institution/item does not support /transactions/sync; mark an empty
                    # incremental list so balances/liabilities sync and last_sync_at advances.
                    entry["incremental"] = True
                    entry["transactions"] = []
            else:
                # Investment accounts do not use /transactions/sync (trades travel
                # under holdings); mark an empty incremental list so Securo advances
                # last_sync_at normally.
                entry["incremental"] = True
                entry["transactions"] = []

            accounts_payload.append(entry)

        data: dict[str, Any] = {
            "contract": 1,
            "accounts": accounts_payload,
        }
        if holdings_payload is not None:
            data["holdings"] = holdings_payload

        return {"state": state, "data": data}


plugin = PlaidPlugin()
