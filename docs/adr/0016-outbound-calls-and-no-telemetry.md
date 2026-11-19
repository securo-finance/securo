# 0016. Every outbound call is gated, and there is no telemetry

- Status: Accepted
- Date: 2026-03-10

## Context

Securo's promise is that a person's financial data stays on their own infrastructure. That promise is only as good as the list of every request the app makes to the outside world.

## Decision

- No telemetry, analytics or error reporting service, in the backend or the frontend.
- Every outbound call is tied to something the person configured or did. The full list:

| Call | Where | Happens only when |
|---|---|---|
| Open Exchange Rates | `providers/openexchangerates.py` | an app id is set; on demand, or every 12 h with `FX_SYNC_MODE=scheduled` |
| Pluggy, Enable Banking | `providers/pluggy.py`, `providers/enable_banking.py` | credentials are set and a connection exists |
| SimpleFIN bridge | `providers/simplefin.py` | SimpleFIN is on (`SIMPLEFIN_ENABLED`, or the admin's saved setting, which replaces it) and a user token |
| BrasilAPI bank lookup | `providers/pluggy.py` | `BRASILAPI_INSTITUTION_LOOKUP_ENABLED` |
| Yahoo Finance | `providers/market_price.py`, `api/assets.py` | someone searches a symbol or previews a quote in the asset form; daily for assets valued at market price |
| Tesouro Transparente | `providers/tesouro_direto.py` | `TESOURO_DIRETO_ENABLED` and a bond search; startup warm only if a workspace uses BRL; daily for existing `TD:` holdings, **even with the flag off** (known gap, to fix) |
| OIDC provider | `api/oidc_auth.py` | `OIDC_ENABLED` |
| LLM providers, external MCP servers, embedding model download | `app/agents/` | `AGENTS_ENABLED` and a configured connection |
| Plugin index and manifests | `app/plugins/index.py` | `PLUGINS_ENABLED` and the catalog is opened |
| Plugin code egress | `plugin_runner/` | `plugins` profile, declared hosts only |

  Paths are under `backend/app/` except `plugin_runner/`, under `backend/`.
- Extended by [0019](0019-s3-storage.md), which adds S3-compatible object storage for files, called only with `STORAGE_PROVIDER=s3`.
- Extended by [0021](0021-plugin-icons.md), which adds plugin icons, fetched from the plugin catalog's host when a browser asks Securo for one.
- Extended by [0025](0025-performance-benchmark-sources.md), which adds the Performance tab's market indices: Yahoo Finance for index search and history, and the Central Bank of Brazil and Ipeadata for the Índice DI, behind `PERFORMANCE_BENCHMARKS_ENABLED`.
- Extended by [0026](0026-plugin-icons-outside-the-catalog.md), which also fetches the plugin index when Connect Bank or Accounts shows an installed plugin's avatar.
- The browser makes three requests of its own: the GitHub releases API to show the update banner (every 6 hours, can be turned off per browser), Google Fonts from `frontend/index.html`, and institution favicons from Google (the backend only builds the URL).

## Consequences

- A new outbound call needs a gate (ADR 0004), a row in this table through a superseding ADR, and a mention in the README.
- Self-hosting the fonts and making the update check opt-in would bring the browser side to zero by default; both are open.

## Enforced by

Review only. Tests mock external FX sync by default (`_no_external_fx_sync` in `tests/conftest.py`), but nothing fails when a new outbound call appears.
