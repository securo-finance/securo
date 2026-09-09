"""Up Bank provider.

Up Bank (https://up.com.au) provides a simple REST API for accessing
accounts, transactions, and account metadata. Users authenticate using a
Personal Access Token (PAT) generated from their Up app settings.
More information here: https://developer.up.com.au/

How to use the Up Bank provider in Securo:
1. Generate a PAT from your Up's app (check steps here: https://developer.up.com.au/)
2. Securo -> Accounts -> Connect Bank -> Select Up Bank -> paste the token

Note:
1. Initially it may take a bit of time to fetch all accounts and transactions, 
   depending on the number of transactions you have. 
   Subsequent syncs will be faster, as Securo only fetches new transactions since the last sync.
2. If you use PAT that expires (eg, after 48 hours), you will need paste new token to reconnect for syncing.

Note about Round-up Transactions:
Up Bank allows users to round up transactions to the nearest dollar and save
the difference. The API returns this as a `roundUp` attribute on the main
transaction, not as a separate transaction record. This provider creates
separate TransactionData records for round-up amounts so they appear as
individual transactions in Securo, using the original transaction ID + "-ru"
as a unique identifier. These transactions are also identified as Transfers in Securo, 
since those small transfers to your Savings account.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

import httpx
import asyncio

from app.providers.base import (
    AccountData,
    BankProvider,
    ConnectionData,
    ProviderUserActionRequired,
    SessionExpiredError,
    TransactionData,
)

logger = logging.getLogger(__name__)

UP_FAVICON_URL = "https://up.com.au/favicon.ico"
UP_API_HELP_URL = "https://developer.up.com.au/"
UP_API_URL = "https://api.up.com.au"
UP_API_ACCOUNTS = "/api/v1/accounts"
UP_API_TIMEOUT = 15
UP_MAX_RETRIES = 3
UP_RETRY_BACKOFF = 30
PAGE_SIZE = 100
DEFAULT_CURRENCY = "AUD"

class UpBankProvider(BankProvider):

    ### FIELDS 
    # Bank identifier    
    @property
    def name(self) -> str:
        return "up"

    # Authentication flow type
    @property
    def flow_type(self) -> str:        
        return "token" # user provides a PAT directly

    
    ### Helper methods
    # Helper method for making authenticated requests to Up API
    @staticmethod
    def _extract_token(credentials: dict) -> str:        
        token = (credentials or {}).get("pat") or ""
        if not token:
            raise SessionExpiredError("Up Bank PAT token is missing")
        return token

    # Helper method to parse Up's ISO 8601 date string to Python date
    @staticmethod
    def _parse_date(date_str: Optional[str]) -> date:
        """Parse Up's ISO 8601 date to Python date."""
        if not date_str:
            return date.today()
        try:
            # Extract YYYY-MM-DD from ISO 8601 string
            date_part = date_str[:10]
            return datetime.strptime(date_part, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return date.today()

    # Helper method of making requests with retries
    async def _request(
        self,
        credentials: dict,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict:        
        
        token = self._extract_token(credentials)
        headers = {
            "Authorization": f"Bearer {token}"            
        }
        
        # Attempts to connect in case of failure
        for attempt in range(1, UP_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(
                    base_url=UP_API_URL,
                    timeout=UP_API_TIMEOUT,
                    headers=headers,
                ) as client:
                    resp = await client.request(method, path, **kwargs)
                    
                # Handle 429 rate limit
                if resp.status_code == 429:
                    if attempt == UP_MAX_RETRIES:
                        raise RuntimeError(f"Up Bank API rate limited after {UP_MAX_RETRIES} attempts")
                    
                    retry_after = resp.headers.get("Retry-After")
                    wait = int(retry_after) if retry_after else UP_RETRY_BACKOFF * (2 ** (attempt - 1))
                    await asyncio.sleep(wait)
                    continue
                
                # Handle other errors - auth or permission or any other 400 errors
                if resp.status_code == 401:
                    raise ProviderUserActionRequired(
                        "Up Bank PAT token is invalid or expired",
                        code="credentials_invalid",
                        help_url=UP_API_HELP_URL,
                    )                
                elif resp.status_code == 403:
                    raise ProviderUserActionRequired(
                        "Up Bank PAT token does not have required permissions",
                        code="credentials_insufficient_scope",
                        help_url=UP_API_HELP_URL,
                    )
                elif resp.status_code >= 400:
                    raise RuntimeError(f"Up Bank API error ({resp.status_code}): {resp.text[:200]}")
                
                # Success - return json data
                return resp.json() or {}
                
            # Any other error
            except httpx.HTTPError as exc:
                if attempt == UP_MAX_RETRIES:
                    raise RuntimeError(f"Up Bank API request failed: {exc}") from exc
                
                wait = UP_RETRY_BACKOFF * (2 ** (attempt - 1))
                await asyncio.sleep(wait)
                continue
        
        raise RuntimeError(f"Failed after {UP_MAX_RETRIES} attempts")

    # Helper method to parse accounts from Up API json response
    def _parse_accounts(self, data: dict) -> list[AccountData]:
        """Parse accounts from Up API response."""
        accounts = []
        for account in data.get("data", []):
            attrs = account.get("attributes", {})
            account_type = attrs.get("accountType", "").lower()
            
            # Map Up account types to standard types
            if account_type == "saver":
                normalized_type = "savings"
            elif account_type == "transactional":
                normalized_type = "checking"
            else:
                normalized_type = account_type or "checking"

            balance_obj = attrs.get("balance", {})
            balance = Decimal(str(balance_obj.get("value", 0)))
            currency = balance_obj.get("currencyCode", DEFAULT_CURRENCY)
            
            accounts.append(
                AccountData(
                    external_id=account.get("id", ""),
                    name=attrs.get("displayName", "Unknown"),
                    type=normalized_type,
                    balance=balance,
                    currency=currency,
                    masked_number=attrs.get("accountNumber", "")[-4:] if attrs.get("accountNumber") else None,
                )
            )
        return accounts        

    ### Unused abstract methods from BaseProvider
    # Not used by UP provider
    def get_oauth_url(self, *args, **kwargs):
        raise NotImplementedError("Up Bank uses PAT token paste flow, not OAuth")

    # Not used by UP provider
    async def refresh_credentials(self, credentials: dict) -> dict:
        return credentials        


    ### Core Methods
    # Authenticate using PAT and create UP bank accounts in Securo
    async def handle_oauth_callback(self, code: str) -> ConnectionData:        
        token = code.strip() if code else ""
        if not token:
            raise ProviderUserActionRequired(
                "Up Bank PAT token is empty",
                code="invalid_token",
                help_url=UP_API_HELP_URL,
            )

        credentials = {"pat": token}
        
        # Initial call to fetch accounts (and to validate the token)
        accounts = await self.get_accounts(credentials)  
        
        return ConnectionData(
            external_id=f"up-{token[:8]}",
            institution_name="Up Bank",
            credentials=credentials,
            accounts=accounts,
            logo_url=UP_FAVICON_URL,
        )

    # Fetch accounts for the authenticated user
    async def get_accounts(self, credentials: dict) -> list[AccountData]:
        """Fetch accounts for the authenticated user."""
        data = await self._request(credentials, "GET", UP_API_ACCOUNTS)
        accounts = self._parse_accounts(data)
        return accounts
    
    # Fetch transactions for a specific account, including round-up transactions
    async def get_transactions(
        self,
        credentials: dict,
        account_external_id: str,
        since: Optional[date] = None,
        payee_source: str = "auto",
    ) -> list[TransactionData]:

        # Build params for initial request
        params: dict[str, Any] = {"page[size]": PAGE_SIZE}

        # Use by securo later to fetch only the recent transactions
        if since: 
            since_iso = f"{since}T00:00:00.000Z"
            params["filter[since]"] = since_iso            

        transactions = []
        url = f"{UP_API_ACCOUNTS}/{account_external_id}/transactions"

        # Continue loop until the "next" link is not available anymore in the response json
        while url:
            # Build kwargs conditionally so we NEVER pass params={} 
            # since the next url has its own query string            
            kwargs: dict[str, Any] = {}
            if params:
                kwargs["params"] = params
                
            # Fetch transactions
            data = await self._request(credentials, "GET", url, **kwargs)
            params = None  # Set params to None as page links embed their own params

            # Loop through transactions and add them into transactions
            for txn in data.get("data", []):            
                attrs = txn.get("attributes", {})
                amount_obj = attrs.get("amount", {})
                amount = Decimal(str(amount_obj.get("value", 0)))

                # Determine type and status matching sync script
                txn_type = "credit" if amount > 0 else "debit"
                txn_date = self._parse_date(attrs.get("createdAt"))
                cleared = attrs.get("status") == "SETTLED"
                txn_status = "posted" if cleared else "pending"
                currency = amount_obj.get("currencyCode", DEFAULT_CURRENCY)
                payee = attrs.get("rawText", attrs.get("description", "Unknown"))

                # Main transaction
                transactions.append(
                    TransactionData(
                        external_id=txn.get("id", ""),
                        description=attrs.get("description", ""),
                        amount=abs(amount),
                        date=txn_date,
                        type=txn_type,
                        currency=currency,
                        payee=payee,
                        status=txn_status,
                    )
                )

                # Check for Round-up transaction and add them into transactions
                round_up = attrs.get("roundUp")
                if round_up:
                    round_up_amount_obj = round_up.get("amount", {})                    
                    round_up_amount = Decimal(str(round_up_amount_obj.get("value", 0)))
                     
                    transactions.append(
                        TransactionData(
                            external_id=f"{txn.get('id', '')}-ru",
                            description=f"Round Up: {attrs.get('description', '')}",
                            amount=round_up_amount,
                            date=txn_date,
                            type="debit", # Round ups are always debit
                            currency=round_up_amount_obj.get("currencyCode", DEFAULT_CURRENCY),
                            payee=f"Round Up: {attrs.get('rawText', attrs.get('description', 'Unknown'))}",
                            status=txn_status,
                        )
                    )

            # Extract next page URL outside of the transaction loop
            url = data.get("links", {}).get("next")

        return transactions
      

