# 0006. Bank sync providers behind one interface, available when configured

- Status: Accepted
- Date: 2026-03-10
- Origin: initial commit (interface), #1033 (credentials resolved per call, settable from the admin screen)

## Context

Bank aggregators differ by country, flow (OAuth redirect, connect widget, token paste) and data shape. Most self-hosted instances use none of them, and an instance should never import or call one it isn't configured for.

## Decision

- Every aggregator implements `BankProvider` in `backend/app/providers/base.py` and returns the shared DTOs (`AccountData`, `TransactionData`, `ConnectionData`, `BillData`, `HoldingData`, `InstitutionData`). Failures use the shared errors (`SessionExpiredError`, `ProviderUserActionRequired`, `ProviderRateLimited`, `ProviderNotConfiguredError`) so the sync code handles them the same way.
- `backend/app/providers/__init__.py` lists `KNOWN_PROVIDERS` and imports a provider's module only when `credentials.is_configured(name, settings)` is true. Availability is computed per call, not registered once at startup.
- Credentials come from the environment or from the admin screen. `resolve_settings` in `backend/app/services/provider_settings.py` overlays saved values on the environment field by field; saved secrets are encrypted and need a `SECRET_KEY` of at least 32 characters. Details in `docs/provider-settings.md`.
- Today: Pluggy and Enable Banking (on when their credentials are present) and SimpleFIN (`SIMPLEFIN_ENABLED`).

## Consequences

- A new aggregator is a new `BankProvider`, an entry in `KNOWN_PROVIDERS` and `PROVIDER_FIELDS`, and its module and class in `_CREDENTIALED_PROVIDERS` (or a `register_provider` call for one that needs no credentials); sync, scheduling and the UI pick it up. One exception: a provider whose `flow_type` is `widget` opens `BankConnectDialog` (`frontend/src/components/bank-connect-dialog.tsx`), which renders Pluggy's widget; a provider with a different widget needs that dialog taught to render it.
- Provider-specific behavior stays inside the provider. Services never branch on a provider's name to reshape its data.

## Enforced by

`tests/test_providers.py`, `tests/test_provider_settings.py`, and per-provider tests (`tests/test_providers_pluggy*.py`, `tests/test_providers_enable_banking.py`, `tests/test_providers_simplefin.py`). Tests reset the provider registry between cases (`_reset_provider_registry` in `tests/conftest.py`).
