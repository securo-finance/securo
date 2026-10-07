# RFC — Portfolio performance tab

Status: proposed · Builds on [RFC #235 (investment ledger)](rfc-235-investment-ledger.md)

## Problem

The Assets page shows what a portfolio is *worth*, not how well it *did*. A
chart of total value rises every time money is added, so it cannot answer the
questions an investor actually asks:

- What did my investments return over the last year, net of what I put in?
- How does that compare with the Ibovespa, the S&P 500 or the CDI?
- How did one wallet (say, one broker account) do on its own?

Brokers answer this with a **time-weighted return** (TWR). This RFC adds a
Performance tab that computes it from the data Securo already has, and
compares it with any market index the user searches for.

## What the user sees

- A **Performance** tab next to Holdings and Transactions.
- A period picker (3m, 6m, YTD, 1y, 3y, 5y) and a scope: the whole portfolio
  or any mix of wallets and individual holdings. Named selections can be saved
  as views.
- Up to five benchmarks, found by searching (Yahoo indices plus the B3 Índice
  DI). Each gets its own line, final return and excess over the portfolio.
- Dragging across the chart shows the return between any two dates.
- If a benchmark's data source fails, only that benchmark shows an error with
  a retry button. The portfolio's own return is always shown.

## Method: time-weighted return

The period is cut into intervals at every date where the portfolio is valued
or money moves. Each interval's growth is:

```
growth = (value at end + money taken out) / (value at start + money put in)
```

and the intervals are multiplied together. The result does not depend on
*when* or *how much* money was added: the same holdings show the same return
whether bought at once or over time. That is what makes it comparable with an
index, and it is the standard figure brokers report.

### Money in at the start of the day, money out at the end

The two directions are timed differently, on purpose:

- **Contributions (buys)** count from the start of the day. The new money is
  exposed to that day's price move, so it belongs in the base.
- **Withdrawals (sales)** count at the end of the day. Sale proceeds are the
  *result* of that day's price move, so they belong in the outcome.

Same-day buys and sales are kept apart rather than netted, so a day trade is
measured on what was put in, not on the net difference.

Worked example — 10 shares bought at 100, last valued at 110, all sold the
next day at 99:

| Timing used for the sale | Interval growth | Return |
|---|---|---|
| End of day (this RFC) | (0 + 990) / 1100 → then compounded | **−1%** |
| Start of day (naive) | 0 / (1100 − 990) | **−100%** |

The naive version subtracts the proceeds from the base *before* the drop that
produced them, which turns any sale below the last valuation into a total loss
and inflates partial sales.

## Where the cash flows come from

Securo holds investments in several shapes. Each needs its own rule for
telling *money moved* apart from *market moved*:

| Holding | Valuation | Cash flows |
|---|---|---|
| Market-priced (ticker) holdings with a ledger | quantity held × stored daily close | each buy/sell on its own date, at `quantity × price ± fee`, converted with that day's FX rate |
| Synced holdings whose provider trades reconcile | provider snapshots | each trade lands on the first snapshot whose share count reflects it, so a valuation update a day later is not mistaken for a gain or a loss |
| Synced holdings without usable trades | provider snapshots | change in reported share count × new unit price. A unit price below 60% or above ~167% of the previous snapshot's is taken as a split, not a purchase. A value change alone never counts as a purchase |
| Synced fixed income (CDB, LCI, Tesouro, COE…) | provider balance | a balance *decrease* is a redemption (withdrawal), so moving money to another holding is not a loss; a full redemption without trades pays out the last balance |
| Manual assets (property, private funds…) | user-entered values | first appearance is a contribution; a sale returns `sell_price` the day after `sell_date`, or the last value when no price was entered (an explicit 0 writes it off) |

**Gross values.** Performance uses pre-tax values: income tax withheld on a
redemption is not an investment loss. Migration `097` stores the provider's
gross value next to the net (withdrawable) one. Holdings totals keep using net.

**Exact trade cash.** Synced fixed-income trades carry unit prices such as
`0.01007794` on quantities in the hundreds of thousands. Migration `098`
widens `asset_transactions.price` so `quantity × price` reproduces the cash
the broker reported.

## Benchmarks

- **Yahoo** indices: dynamic search over Yahoo's index lookup, histories from
  adjusted daily closes.
- **Índice DI B3**: daily CDI rates compounded into an index. Sources are
  tried in order, so one outage does not break it: BCB SGS SOAP
  (`www3.bcb.gov.br`), the BCB SGS JSON API, then Ipeadata's mirror of the same
  series. A source returning fewer than two observations falls through to the
  next.
- Histories are cached for an hour, searches for fifteen minutes. Failed
  requests and histories too short to compare are never cached.
- Each benchmark reports `source_error` (`unavailable`, `rate_limited`,
  `no_data`) instead of failing the whole request. The frontend does not keep
  such a response fresh, so revisiting the tab retries.

The benchmark line is the index's own change since the period start: a
time-weighted portfolio return is independent of cash flows, so it is compared
with the index itself rather than with a shadow portfolio.

## Edge cases

Each row is a test in `backend/tests/test_portfolio_performance_edge_cases.py`,
with the expected return worked out by hand:

| Scenario | Expected |
|---|---|
| Everything sold above the last close (alongside a flat holding) | +10% |
| Everything sold below the last close (bought 1000, sold 990) | −1% |
| Half sold, the rest rallies (+10% then +10%) | +21% |
| Sold at a loss, bought back lower (−20%, then +20%) | −4% |
| Half the wallet goes bankrupt (close → 0), the rest gains 10% | −45% |
| Delisted holding written off by selling at 0 | −50% |
| Day trade with fees next to a flat holding | +2.40% |
| 2:1 split recorded as free shares (buy at price 0) | +10% |
| Manual asset sold above / at its purchase value | +30% / 0% |
| Manual asset sold without a price, last valued 20% up | +20% |
| Synced CDB fully redeemed with no trades, next to a flat holding | +0.5% |

## Known limitations

- **Dividends are not counted** (see below). On the ex-date the price drop
  shows as a loss and the cash paid out never appears, so dividend payers
  look worse than they did.
- **Splits** have no transaction kind. Recording the new shares as a buy at
  price 0 gives the right result, as tested above.
- **Delisted tickers** keep their last price until written off with a sale at 0.
- **Total wipeout.** Once the whole scope reaches zero, the time-weighted
  return stays at −100% even if new money arrives later. That is the
  definition of a compounded return, not a bug, but the UI could explain it.
- **Benchmark fairness.** The Ibovespa is a total-return index (dividends
  reinvested), so comparing it with a price-only portfolio is unfair to the
  portfolio until dividends land. `^GSPC` is price-only.

## Future work: dividends and other income

Not part of this change. Recorded here because it shapes the data model.

### The model

A dividend is money leaving a holding. The engine already treats a sale that
way, so the rule is the same: the dividend is a **withdrawal on the ex-date**,
at its gross amount. On the ex-date the price drops by roughly the dividend
and the withdrawal adds it back:

```
growth = (value after the drop + dividend) / value before   ≈ 1
```

That is the total return a broker reports. Income is recorded as a new
`dividend` kind in the trade ledger (cash amount, tax withheld, ex-date and
payment date). It leaves units and average price alone, unlike `amortization`,
a return of principal, which also lowers the cost basis.

The cash usually also arrives in a bank or brokerage account. Performance
reads income **only from the ledger, never from account transactions**, so the
same money showing up as income in the account cannot be counted twice.

### Where the data comes from

| Source | What it offers |
|---|---|
| Pluggy | Investment transactions of type `INTEREST` (dividends, JCP, FII income, coupons) and `AMORTIZATION`, per holding. Securo already downloads them and drops them today. |
| SimpleFIN | No transaction types at all. Holdings are snapshots (symbol, shares, value). A dividend is a cash row whose description may say "DIVIDEND" or "REINVEST". |
| Enable Banking | Bank accounts only. A dividend is a credit with free text, not linked to any holding. |
| Yahoo | Dividend and split history per ticker by ex-date, for Brazilian stocks, FIIs and US stocks. |

So the approach is one expected-income source plus confirmation:

1. **Expected income** = Yahoo's dividend per share × shares held on the
   ex-date. Shares come from the ledger, or from snapshots for holdings
   without one.
2. **Confirmation**, strongest first: typed provider records (Pluggy
   `INTEREST`); account rows whose text names the ticker (for example
   "JUROS S/ CAPITAL – À VISTA s/ BRASIL ON NM – BBAS3"); otherwise the
   expected income stays marked as an estimate the user can edit.
3. Always dated on the **ex-date**. Providers report the payment date, which
   for Brazilian JCP can be months later. Dating by payment would leave a fake
   dip in the chart for that whole gap.

Description matching must require a ticker, never a keyword alone: "Rendimentos"
is also how Brazilian banks label the daily yield on an account balance.

### Reinvested dividends on snapshot-only holdings

This is the subtle case and the reason for the confirmation step.

**What happens today.** For holdings with no trade history (SimpleFIN, and
Pluggy holdings whose trades do not reconcile), the engine only sees
snapshots: "N shares worth X". When the share count goes up it must decide
why, and it always concludes *the owner bought more*. It prices the new shares
and records them as a contribution. For a real purchase that is right: new
money is not performance.

**Why that is wrong for dividend reinvestment (DRIP).** Many US brokerages
reinvest dividends automatically. The new shares are paid for by the holding
itself, not by the owner. Example:

1. 100 shares of VOO at $500 are worth $50,000.
2. VOO pays $1.50 per share. The price drops to about $498.50 on the ex-date.
3. A few days later the broker uses the $150 to buy about 0.294 shares at $510.
4. The next snapshot shows 100.294 shares × $510 = $51,150.

| | Correct | Engine today |
|---|---|---|
| What the 0.294 new shares are | the $150 dividend, reinvested | $150 the owner added |
| Return | 51,150 / 50,000 → **+2.30%** | 51,150 / (50,000 + 150) → **+2.00%** |

The dividend is part of the return, but counted as a deposit it vanishes. On
an index fund that is roughly 1.5–2% a year of return that never shows.

**How it gets recognised.**

1. Compute the expected dividend for the ex-date: 100 × $1.50 = $150.
2. Look for a share increase after the ex-date, within a window: a few weeks
   for US stocks, longer for Brazil.
3. Split the increase:
   - **Up to the expected dividend** it is reinvested income, recorded as two
     events that cancel out: the dividend leaving on the ex-date and the same
     amount bought back on the reinvestment date. Neither is the owner's money.
     Recording both, rather than ignoring the share increase, also avoids a
     dip between the ex-date (price falls) and the reinvestment (shares arrive).
   - **Anything above it** is real new money and stays a contribution. If the
     owner also bought $1,000 that week, the $1,150 increase becomes $150
     reinvested plus $1,000 contributed.
   - **If it is a little less than expected**, the gap is usually tax withheld
     at source (30% on US dividends for a Brazilian resident). The smaller of
     the two is the reinvested part; the gap is tax.

**Where it gets fuzzy.**

- *Long gaps between snapshots.* If a holding is not synced for weeks, a
  reinvestment can merge with purchases. The split rule above still gives a
  sensible answer.
- *Cash swept into a money-market fund.* Some brokerages put cash dividends
  into a fund (such as SPAXX) that is itself a holding. The dividend from VOO
  then appears as new shares of a different holding and is counted as a
  deposit there. Matching across holdings by date and amount is less certain;
  start with recognised sweep funds only.
- *Splits in the same window* are already excluded: a large unit-price jump
  between snapshots is treated as a split, never a purchase.
- *Brazil.* Brazilian brokers do not reinvest automatically, so this mostly
  affects SimpleFIN. Holdings with a full trade history need no guessing.

Because this is a heuristic, it should run only on holdings without a trade
history, and every reclassification should be labelled in the UI ("treated as
a reinvested dividend") so the owner can see it and undo it.

### Suggested order

1. Ledger kind, engine rule and manual entry, with tests.
2. Pluggy `INTEREST` / `AMORTIZATION` import.
3. Yahoo expected income for ticker holdings.
4. Reinvestment recognition and description matching (SimpleFIN, Enable Banking).
5. Splits and delistings from the same corporate-action data.

## Data model changes

| Migration | Change |
|---|---|
| `097_asset_value_gross_amount` | nullable `asset_values.gross_amount`; backfills exact anchors for Pluggy holdings |
| `098_asset_transaction_price_precision` | `asset_transactions.price` from `Numeric(18,6)` to `Numeric(28,12)` |

Both are additive. `downgrade()` reverses each.
