# 0023. New bank sync providers are plugins; the core keeps the pipeline

- Status: Accepted
- Date: 2026-11-17
- Origin: #1464 (provider plugins, issue #1461); proposals waiting on it: #659, #781, #1440

## Context

Every aggregator so far was added to the core (ADR 0006): a module under
`backend/app/providers/`, its fields in the admin screen, sometimes an optional
service of its own. Each one is code that every instance ships and the
maintainers review and keep working, while most instances use none of them.
Many of the proposals are for one country or one institution, and some read a
website rather than an API, so they break when that site changes.

Since ADR 0015 a plugin can run its own code in the sandboxed runner, reach
only the hosts its manifest declares and keep its secrets sealed. #1464 lets
that code return accounts, transactions, bills, holdings and trades for the
bank sync pipeline to write, so a provider no longer has to live in the core
to sync like a connected bank.

## Decision

- **A new bank sync provider is a plugin.** Its integration declares
  `provides` in the manifest (`backend/app/plugins/manifest.py`) and its
  `sync` returns records in the provider contract
  (`backend/app/plugins/contract.py`, `CONTRACT_VERSION`). Pull requests that
  add a provider module to the core are pointed to a plugin instead.
- **The core keeps the pipeline**, the same for native and plugin providers:
  - the `BankProvider` interface and DTOs in `backend/app/providers/base.py`;
    `PluginPayloadProvider` in `backend/app/plugins/provider.py` serves a
    validated payload through it;
  - `sync_connection` in `backend/app/services/connection_service.py`
    (dedup, pending to posted, bills, holdings, partial feeds, transfer
    detection, rules);
  - reconciliation and FX (ADR 0008, ADR 0018);
  - the bank connection a plugin connection writes under, and its status,
    managed in `backend/app/plugins/connections.py`.
- **The plugin owns the outside world**: sign-in (its form or OAuth),
  credentials (sealed by `backend/app/plugins/vault.py`, never in
  `bank_connections.credentials`), the calls to its service (through the
  egress proxy, to declared hosts only, ADR 0016), its cursor (`state`) and
  its schedule (`sync_minutes`). Throttling is `RateLimited` and an expired
  sign-in is `NeedsAuth`, raised from `sync`.
- **The contract is versioned.** A payload is checked whole before anything
  is written. A change that an existing plugin can't ignore gets a new
  contract version; adding an optional field does not.
- **The native providers stay in the core**: Pluggy, Enable Banking and
  SimpleFIN keep working as ADR 0006 describes. Moving any of them out is a
  separate decision.

## Consequences

- A provider for one country or institution no longer needs a core release,
  a core review of its scraping or API code, or a new optional service. It
  needs `PLUGINS_ENABLED` and the `plugins` Compose profile on the instance
  that installs it.
- What a provider can report is what the contract carries. A provider that
  needs something new (a record kind, a field, a sign-in flow) extends the
  contract and the pipeline in the core, for every provider, rather than
  special casing one.
- Plugin connections are synced by their plugin, never by the bank sync
  schedule; `sync_connection` refuses them without a payload.
- The admin screen's provider settings (`docs/provider-settings.md`) apply
  to native providers only; a plugin's settings are its connection form.

## Enforced by

`tests/test_provider_plugins.py` (a payload synced through the pipeline, the
schedule leaving plugin connections alone), `tests/test_plugin_contract.py`
(the contract) and `tests/test_plugin_trades.py`. That new providers go to a
plugin is a review rule; no test enforces it.
