# 0027. Risk metrics on the performance series

Status: proposed

## Context

The Performance tab (#1096) answers "how much did I earn?" but not "how
bumpy was the ride, and did the return pay for it?". The same window can be up
12% with 8% or with 30% volatility, and the deepest fall matters more than the
final number for anyone who had to live through it.

The daily, flow-adjusted time-weighted return index per scope already exists
on the server, and so does a formatted daily series per benchmark. Risk
statistics need nothing else.

## Decision

Add a pure module, `backend/app/services/risk_metrics.py`, used by the API,
the MCP server and later reports:

- simple daily returns, not log returns: the performance service produces a
  time-weighted index from simple returns, and mixing the two definitions
  changes the result by a few percent on long windows.
- 252 periods per year by default, configurable for calendar-daily series.
- annualized volatility: sample standard deviation of daily returns × √252.
- Sharpe: mean daily excess return over the daily risk-free rate, divided by
  the daily standard deviation, × √252.
- Sortino: mean daily excess return over the daily minimum acceptable return,
  divided by the downside deviation below that target, × √252.
- Calmar: annualized return over the absolute maximum drawdown.
- drawdowns are negative fractions from the running peak; `max_drawdown` is
  the minimum, `current_drawdown` the last.
- annualized return and Calmar need at least five daily returns; annualizing
  a single good day produces a meaningless figure.
- ratios with an undefined denominator are `null`: Sharpe with zero
  volatility, Sortino with no downside days, Calmar with no drawdown. A zero
  would read as a real result.
- fewer than two observations, non-positive values and non-finite numbers
  raise `RiskMetricsError` instead of yielding a number.

The first change ships the module and its tests only. A follow-up, once
#1096 lands, feeds the time-weighted index and each benchmark series into it,
exposes the numbers on the performance response and renders a Risk section in
the Performance tab.

## Consequences

- The math is unit-tested on synthetic series with hand-computed values, with
  no database, clock or network in the way.
- The API can expose the same numbers to agents and MCP clients without a
  second implementation in the frontend.
- The risk-free rate is the caller's decision. Zero is the default; the
  Índice DI the performance tab already fetches is the natural CDI proxy.
- Dividends (#1334) and allocation targets (#897) change the inputs of the
  performance service, not the math here.
