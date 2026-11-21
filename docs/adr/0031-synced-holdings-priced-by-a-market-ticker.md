# 0031. A synced holding can be priced by a market ticker; the provider keeps its position

- Status: Accepted
- Date: 2026-11-20
- Origin: issue #1731 (Pluggy reports the B3 symbol of exchange-traded positions, and Securo ignored it)
- Extends: [0006](0006-bank-sync-providers.md), [0016](0016-outbound-calls-and-no-telemetry.md)

## Context

Pluggy's `/investments` reports the B3 trading symbol of stocks, units, FIIs, BDRs and ETFs in `code` (`PETR4`, `HGLG11`). The sync stored none of it, so these holdings had no ticker, logo or market price, and could not be told apart from a CDB. People added the same positions again by hand as `PETR4.SA` to get a price, and the portfolio then counted them twice.

A market price and a provider-owned position are not exclusive: the bank knows how many shares someone holds, the market knows what a share is worth today.

## Decision

- A provider can mark a holding's ticker as quotable (`HoldingData.ticker_quotable`, a Yahoo Finance symbol). Pluggy does so for `EQUITY`/`ETF` positions in BRL whose `code` is a B3 symbol, as `<code>.SA` (the odd-lot `F` dropped), and tags them `stock`, `fund` (FIIs) or `etf` (`HoldingData.asset_type`). Fixed income, funds and pensions keep the generic `investment`: their `code` is a contract number or a CNPJ.
- The sync links a quotable ticker only after a live quote confirms it and the quote's currency matches the holding's (`_QuotableTickerLinker` in `backend/app/services/connection_service.py`, `link_market_ticker` in `backend/app/services/asset_service.py`). The holding then has `valuation_method="market_price"`: units from the provider, price, logo and daily refresh from the quote. A failed lookup keeps the provider's valuation and is retried on the next sync; a rate limit stops lookups for the rest of that sync.
- A holding that already has a ticker is never re-linked by the sync, so a ticker the user chose wins. `POST /api/assets/{asset_id}/link-ticker` links or re-links one by hand, for holdings the detection could not read; only holdings of a bank connection accept it.
- `DELETE /api/assets/{asset_id}/link-ticker` is the way back: the holding returns to `valuation_method="manual"`, its cached quote is cleared (out of the daily refresh) and today's point is rewritten from the provider's last reported value, kept in `external_metadata` for this. The ticker stays as a label, which is what keeps the sync from linking it again; linking by hand prices it from the market again.
- A market-priced synced holding's daily point stores the quote it was valued at (`AssetValue.price`), like a manual ticker holding's, so the history is recomputed from the ledger when trades move units across dates. A raw (non-quotable) label from the provider never replaces the ticker of a market-priced holding.
- The connection keeps owning the position: units are rewritten on every sync, the row stays read-only, and buys by ticker and order imports never consolidate onto it.
- A market-priced synced holding keeps its own ledger only when it holds trades the user entered (another source); one priced by a linked ticker still takes the provider's trades, since only its price source changed.
- This amends the Yahoo Finance row of ADR 0016:

| Call | Where | Happens only when |
|---|---|---|
| Yahoo Finance | `providers/market_price.py`, `api/assets.py`, `services/connection_service.py` | someone searches a symbol, previews a quote or links a ticker; daily for assets valued at market price; during a bank sync, once per synced holding whose provider reports a quotable ticker, until it is linked |

## Consequences

- A bank sync with exchange-traded holdings makes one quote request per unlinked holding, to the host the daily price refresh already uses, and none once they are linked.
- A provider that reports raw broker symbols (SimpleFIN) is unchanged: its ticker stays a label unless it opts in with `ticker_quotable`.
- A wrong symbol can only come from a quote in the holding's currency; the user corrects it by linking another ticker, and the sync leaves that choice alone.

## Enforced by

`backend/tests/test_synced_holding_ticker.py` (detection, confirmation, currency check, retry, rate limit, the user's ticker surviving the sync, the link and unlink endpoints and their guards) and `backend/tests/test_plugin_trades.py` (which market-priced holdings take the provider's trades).
