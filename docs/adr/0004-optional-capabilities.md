# 0004. Optional capabilities: one switch, off by default, free when off

- Status: Accepted
- Date: 2026-05-12
- Origin: #186 (AI agents, the first one built this way)

## Context

Securo runs on people's own machines, often small ones. Every capability someone doesn't use still costs them if it adds imports, containers, background jobs, outbound calls or menu items. And every outbound call is a privacy question (ADR 0016).

## Decision

A capability beyond the core is switched on by one environment variable, is off by default when it has any cost, and costs nothing when off.

- Settings live in `backend/app/core/config.py` (`Settings`). Process-level flags are read through `feature_flag(name)` in `backend/app/core/feature_flags.py` (`AGENTS_ENABLED`, `PLUGINS_ENABLED`, `TESOURO_DIRETO_ENABLED`).
- Off means: routers not mounted and their modules not imported (`backend/app/main.py`), scheduled jobs not registered (`backend/app/worker.py`), extra services not started.
- Extra services sit behind Compose profiles in `docker-compose.yml`: `agents` starts `mcp-server`, `plugins` starts `plugins-runner` and `plugins-egress`.
- Integrations that need credentials are available only when the credentials are set (ADR 0006).
- The frontend learns the flags from `/api/info` (`useFeatureFlags`) and hides what is off.
- Each switch is documented in `.env.example` and in the README's "Optional capabilities" section.

Current switches and defaults: `AGENTS_ENABLED` (off), `PLUGINS_ENABLED` (off), `SIMPLEFIN_ENABLED` (off), `OIDC_ENABLED` (off), `BRASILAPI_INSTITUTION_LOOKUP_ENABLED` (off), `OPENEXCHANGERATES_APP_ID` (blank), `FX_SYNC_MODE` (`on_demand`), `LOCAL_AUTH_ENABLED` (on), `REGISTRATION_ENABLED` (on), `TESOURO_DIRETO_ENABLED` (on).

`TESOURO_DIRETO_ENABLED` is the one capability with an outbound call that is on by default: its data is fetched only when someone searches for a bond, and the startup cache warm runs only when a workspace uses BRL. The reason is written next to the setting in `config.py`.

## Consequences

- A new optional capability follows the same shape: one flag, off when it has a cost, documented in `.env.example` and the README, its off path verified to import, schedule and call nothing.
- Known exception: the database schema is not optional. The agents migrations (046 to 049, 054) create their tables and require the `vector` extension even when agents are off, which is why the Compose database image is `pgvector/pgvector:pg16`.

## Enforced by

`tests/test_config.py` covers settings validation. No test checks that every flag defaults off or that the off path imports nothing.
