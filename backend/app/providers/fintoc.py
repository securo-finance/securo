import logging
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

import httpx

from app.core.config import get_settings
from app.providers.base import (
    AccountData,
    BankProvider,
    ConnectionData,
    ConnectTokenData,
    InstitutionData,
    InstitutionListData,
    ProviderRateLimited,
    RefreshOutcome,
    SessionExpiredError,
    TransactionData,
)

logger = logging.getLogger(__name__)

FINTOC_API_BASE = "https://api.fintoc.com/v1"

# Fintoc movements only contain settled (posted) entries — no pending state.
_DEFAULT_SYNC_DAYS = 90


def _map_account_type(fintoc_type: str) -> str:
    """Map Fintoc account type string to Securo account type.

    vista_account / sight_account (Cuenta Vista / Cuenta RUT) is Chile's most common
    demand-deposit account — structurally equivalent to a checking account.
    """
    mapping = {
        "checking_account": "checking",
        "savings_account": "savings",
        "vista_account": "checking",
        "sight_account": "checking",
        "credit_card": "credit_card",
        "line_of_credit": "credit_card",
    }
    return mapping.get(fintoc_type, "checking")


def _build_account_data(acc: dict) -> AccountData:
    """Map a Fintoc account payload to AccountData."""
    balance_obj = acc.get("balance") or {}
    balance = Decimal(str(balance_obj.get("available") or balance_obj.get("current") or 0))
    return AccountData(
        external_id=acc["id"],
        name=acc.get("official_name") or acc.get("name") or acc["id"],
        type=_map_account_type(acc.get("type", "")),
        balance=balance,
        currency=(acc.get("currency") or "CLP").upper(),
    )


def _parse_next_link(link_header: str) -> str | None:
    """Extract the URL with rel="next" from an RFC 5988 Link header."""
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part or "rel=next" in part:
            url_part = part.split(";")[0].strip()
            if url_part.startswith("<") and url_part.endswith(">"):
                return url_part[1:-1]
    return None


def _build_transaction_data(mov: dict) -> TransactionData:
    """Map a Fintoc movement payload to TransactionData.

    CLP amounts from Fintoc are whole integer pesos — no scaling needed.
    Charge = money leaving the account (debit); deposit = money entering (credit).
    """
    tx_type = "debit" if Decimal(str(mov.get("amount", 0))) < 0 else "credit"
    # post_date is the settlement date; fall back to transaction_date if absent.
    tx_date_str = mov.get("post_date") or mov.get("transaction_date")
    try:
        tx_date = date.fromisoformat(str(tx_date_str)[:10])
    except (TypeError, ValueError):
        tx_date = date.today()

    return TransactionData(
        external_id=str(mov["id"]),
        description=mov.get("description") or "",
        amount=Decimal(str(mov.get("amount", 0))),
        date=tx_date,
        type=tx_type,
        currency=(mov.get("currency") or "CLP").upper(),
        status="posted",
        raw_data=mov,
    )


class FintocProvider(BankProvider):
    """Bank account linking provider for Chilean banks via Fintoc (https://fintoc.com).

    Uses a widget-based connection flow (FintocLink) identical in structure to Pluggy.
    All API calls are made with httpx.AsyncClient — the synchronous fintoc pip package
    is intentionally not used to maintain async consistency with other providers.
    """

    @property
    def name(self) -> str:
        return "fintoc"

    @property
    def flow_type(self) -> str:
        return "widget"

    def _get_headers(self) -> dict:
        return {"Authorization": get_settings().fintoc_secret_key}

    def _raise_for_fintoc(self, response: httpx.Response) -> None:
        """Translate Fintoc HTTP error codes into provider exceptions."""
        if response.status_code == 401:
            raise SessionExpiredError("Fintoc link token is invalid or has been revoked")
        if response.status_code == 429:
            raise ProviderRateLimited("Fintoc rate limit exceeded")
        response.raise_for_status()

    async def get_oauth_url(
        self,
        redirect_uri: str,
        state: str,
        flow_params: Optional[dict] = None,
    ) -> str:
        raise NotImplementedError("Fintoc uses widget flow, not OAuth redirect")

    async def create_connect_token(
        self,
        client_user_id: str,
        item_id: str | None = None,
    ) -> ConnectTokenData:
        """Create a FintocLink widget token by calling the Link Intent endpoint."""
        async with httpx.AsyncClient(headers=self._get_headers(), timeout=30) as client:
            response = await client.post(
                f"{FINTOC_API_BASE}/link_intents",
                json={
                    "product": "movements",
                    "holder_type": "individual",
                    "country": "cl",
                },
            )
            self._raise_for_fintoc(response)
            data = response.json()
        return ConnectTokenData(access_token=data["widget_token"])

    async def handle_oauth_callback(self, code: str) -> ConnectionData:
        """Exchange the Fintoc widget exchange_token for a permanent link_token.

        `code` is the exchange_token delivered by the widget's onSuccess callback
        (linkIntent.exchangeToken). The exchange endpoint returns the full Link object
        including link_token, accounts, and institution.
        """
        async with httpx.AsyncClient(headers=self._get_headers(), timeout=30) as client:
            response = await client.get(
                f"{FINTOC_API_BASE}/links/exchange",
                params={"exchange_token": code},
            )
            self._raise_for_fintoc(response)
            link_data = response.json()

        raw_accounts = link_data.get("accounts", [])
        accounts = [_build_account_data(acc) for acc in raw_accounts]

        inst = link_data.get("institution") or {}
        institution_name = inst.get("name") or "Chilean Bank"

        link_token = link_data["link_token"]
        link_id = link_data["id"]

        return ConnectionData(
            external_id=link_id,
            institution_name=institution_name,
            credentials={"link_token": link_token},
            accounts=accounts,
        )

    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        link_token = credentials.get("link_token") or credentials.get("link_token_enc", "")
        async with httpx.AsyncClient(headers=self._get_headers(), timeout=30) as client:
            response = await client.get(
                f"{FINTOC_API_BASE}/accounts",
                params={"link_token": link_token},
            )
            self._raise_for_fintoc(response)
            raw = response.json()
            logger.info("Fintoc accounts raw response: %s", raw)
            return [_build_account_data(acc) for acc in raw]

    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: Optional[date] = None,
        payee_source: str = "auto",
    ) -> list[TransactionData]:
        """Fetch movements for one account, handling cursor-based pagination."""
        link_token = credentials.get("link_token") or credentials.get("link_token_enc", "")
        since_date = since or (date.today() - timedelta(days=_DEFAULT_SYNC_DAYS))

        params: dict = {
            "link_token": link_token,
            "since": since_date.isoformat(),
        }

        results: list[TransactionData] = []
        async with httpx.AsyncClient(headers=self._get_headers(), timeout=60) as client:
            while True:
                response = await client.get(
                    f"{FINTOC_API_BASE}/accounts/{account_external_id}/movements",
                    params=params,
                )
                self._raise_for_fintoc(response)
                body = response.json()

                movements = body if isinstance(body, list) else body.get("data", [])
                results.extend(_build_transaction_data(mov) for mov in movements)

                # Fintoc paginates via RFC 5988 Link header (rel="next"), not a body cursor.
                next_url = _parse_next_link(response.headers.get("link", ""))
                if not next_url:
                    break
                # Extract the page number from the next URL and carry it forward.
                page_match = re.search(r"[?&]page=(\d+)", next_url)
                if not page_match:
                    break
                params = {**params, "page": page_match.group(1)}

        return results

    async def refresh_credentials(self, credentials: dict) -> dict:
        """No-op: Fintoc link tokens do not expire on a timer.

        Token invalidity surfaces as SessionExpiredError via _raise_for_fintoc()
        on the next API call (401 response).
        """
        return credentials

    async def trigger_refresh(self, credentials: dict) -> RefreshOutcome:
        """Fintoc returns live bank data on each API call — no explicit refresh needed."""
        return "skipped"

    async def list_institutions(self, country: Optional[str] = None) -> InstitutionListData:
        """Return Fintoc-supported institutions for Chile (or the specified country)."""
        target_country = country or "cl"
        async with httpx.AsyncClient(headers=self._get_headers(), timeout=30) as client:
            response = await client.get(
                f"{FINTOC_API_BASE}/institutions",
                params={"country": target_country},
            )
            self._raise_for_fintoc(response)
            raw = response.json()

        institutions = [
            InstitutionData(
                name=inst.get("id") or inst.get("name", ""),
                display_name=inst.get("name", ""),
                country=inst.get("country", target_country),
                logo=inst.get("logo_url") or inst.get("logo"),
            )
            for inst in (raw if isinstance(raw, list) else raw.get("data", []))
        ]
        return InstitutionListData(countries=[target_country], institutions=institutions)
