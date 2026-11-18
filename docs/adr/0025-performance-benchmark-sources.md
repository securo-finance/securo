# 0025. Performance benchmarks come from Yahoo Finance, the Central Bank of Brazil and Ipeadata

- Status: Accepted
- Date: 2026-11-18
- Origin: #1096 (portfolio performance tab with benchmark comparisons)
- Extends: [0016](0016-outbound-calls-and-no-telemetry.md)

## Context

The Performance tab compares a portfolio's time-weighted return with market indices. Securo holds no index data, so each comparison reads it from outside. Yahoo Finance was already on ADR 0016's list for market prices, but only for asset quotes. The Índice DI, the usual yardstick for Brazilian fixed income, is not on Yahoo. Its official source is the Central Bank of Brazil's SGS series 12, whose two interfaces fail often enough that a mirror, Ipeadata, is worth keeping as a fallback. Both are new hosts.

## Decision

- `backend/app/providers/benchmark.py` holds every index source behind `CompositeBenchmarkProvider` (`get_benchmark_provider`). `portfolio_performance_service.get_portfolio_performance_multi` and the search route in `backend/app/api/assets.py` are its only callers.
- Yahoo Finance (`YahooBenchmarkProvider`) answers symbol searches through its lookup and spark endpoints, and index histories through yfinance's chart endpoint.
- The Índice DI (`B3DIBenchmarkProvider`) reads SGS series 12 from the Central Bank, first through its SOAP service and then through its JSON API, and falls back to Ipeadata's copy of the series (`SGS366_CDI366`). Searching for the Índice DI is matched locally and calls nothing.
- One switch, `PERFORMANCE_BENCHMARKS_ENABLED` (`performance_benchmarks_enabled` in `backend/app/core/config.py`), turns all of them off. While it is off, the search route answers 404, a performance request drops any benchmarks it names before the service runs, and `/api/info` tells the frontend to hide the picker. The portfolio's own return needs none of them.
- Results are kept in memory for each backend process: searches for 15 minutes and histories for an hour, so reopening the tab doesn't repeat the calls.
- This extends the list in ADR 0016:

| Call | Where | Happens only when |
|---|---|---|
| Yahoo Finance (index search and history) | `providers/benchmark.py` | `PERFORMANCE_BENCHMARKS_ENABLED` and someone searches for a benchmark or opens the Performance tab with one selected |
| Central Bank of Brazil SGS (SOAP and JSON), Ipeadata | `providers/benchmark.py` | `PERFORMANCE_BENCHMARKS_ENABLED` and the Performance tab compares against the Índice DI; Ipeadata only after both Central Bank interfaces fail |

## Consequences

- The switch is on by default, unlike ADR 0004's default for optional capabilities. It matches Yahoo market prices and Tesouro Direto, which are also on by default. No call happens until someone picks a benchmark: none is selected by default, and the selection is remembered per browser and workspace.
- What leaves the server is the index symbol and a date range. No holding, amount or account is ever sent.
- A source that fails or is rate limited is reported for that benchmark alone (`source_error`). The portfolio's return and the other benchmarks still show.

## Enforced by

`tests/test_benchmark_api.py`: `test_disabled_benchmarks_never_reach_an_external_source` and `test_info_and_routes_agree_when_only_settings_turn_benchmarks_off` for the switch, and `test_regular_asset_list_does_not_touch_benchmark_provider` for calls outside the Performance tab. `tests/test_benchmark_provider.py` and `tests/test_sgs_soap_source.py` cover the sources and their fallback order. Nothing fails when a new host appears in `benchmark.py`; that stays with review.
