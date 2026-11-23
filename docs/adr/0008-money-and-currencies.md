# 0008. Money is exact, every row keeps its own currency

- Status: Accepted
- Date: 2026-03-24
- Origin: #29 (multi-currency), #353 (no invented 1:1 rates when stamping)

## Context

People hold accounts in more than one currency, and totals have to add up to the cent. Floats can't do that, and a conversion stored with a made-up rate is worse than no conversion.

## Decision

- Amounts are `Numeric(15, 2)` columns and `Decimal` in Python. FX rates are `Numeric(20, 10)`; asset prices and quantities use 6 decimals. No money column is a `Float`.
- Every account and transaction keeps its own `currency` (ISO code, default `USD`). A transaction also stores `amount_primary` and `fx_rate_used`: its value in the user's primary currency, stamped when it is written.
- Rates live in the global `fx_rates` table (ADR 0002), base always USD, one row per (base, quote, date). Cross rates go through USD. `backend/app/services/fx_rate_service.py` looks for the exact date, then fetches on demand, then takes the closest date; future dates use today's rate.
- When no rate exists, `stamp_primary_amount` leaves `amount_primary` NULL and reads use `COALESCE(amount_primary, amount)`; `restamp_fallback_fx` fills it later. Live conversions in `convert()` fall back to 1:1 and the UI shows a warning.
- The default currency is USD (`settings.default_currency`); BRL and other currencies are first class.

## Consequences

- New money columns use `Numeric(15, 2)` and `Decimal`; new arithmetic never goes through `float`.
- Sums across currencies use `amount_primary`, never `amount`.
- `amount_primary` is in the primary currency of the row's owner (`user_id`), and members of a shared workspace can display different currencies. A read that sums or shows it to a viewer goes through `backend/app/services/viewer_currency.py`, which converts the rows of owners in another currency at the row's date and leaves the rest as stored; the stored stamp is never rewritten for a viewer. A group share of a row from another workspace that has no stamp is read from the row's own currency, at the row's date and with the same stored rates, so stamping it does not move the share.
- Rates are refreshed on demand by default; scheduled sync is opt-in (ADR 0004).

## Enforced by

`tests/test_fx_rates.py`, `tests/test_currencies_api.py`. Nothing checks that every money column is `Numeric`.
