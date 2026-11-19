# 0015. Plugins: data, sandboxed code and themes

- Status: Accepted
- Date: 2026-11-10
- Origin: #1407 (data plugins), #1429 (code plugins), #1434 (sealed secrets), #1442 (themes), #1450 (workspace kinds)

## Context

People want categories, rules, integrations and looks that Securo doesn't ship. Accepting code from strangers into a finance app is only acceptable if that code can't reach the database, the server's secrets or the network at large.

## Decision

- `PLUGINS_ENABLED` gates the feature (ADR 0004); the `plugins` router answers 404 when off, and access tokens can't reach it.
- A plugin is a repository with a `securo-plugin.json` manifest (`backend/app/plugins/manifest.py`). The catalog comes from an index fetched only when someone opens it, each manifest checked against its sha256 (`backend/app/plugins/index.py`).
- Three kinds of contribution:
  - **data** (category groups, categories, rules): copied into the workspace; nothing runs;
  - **code** (integrations): runs only in `plugins-runner` (Compose profile `plugins`), on an internal network with no database, Redis, internet or backend environment, each plugin as its own user. Outbound traffic goes through `plugins-egress` to the hosts the manifest declares; private addresses are refused. The plugin reaches Securo's API only with an access token limited to its declared scopes (ADR 0012). Secrets are sealed in `backend/app/plugins/vault.py`;
  - **themes**: tokens rendered by Securo itself, checked for WCAG AA contrast.
- Installs are per workspace at a pinned commit (`workspace_plugin`). Rows a plugin created carry their origin (`plugin_origin`); a row the user edited is left alone on update and kept on removal.
- Installing code requires the workspace owner.
- Extended by [0021](0021-plugin-icons.md): a plugin's icon is read from the catalog's host when a browser asks Securo for it, as well as when someone opens the catalog.
- Extended by [0026](0026-plugin-icons-outside-the-catalog.md): the index is also fetched when Connect Bank or Accounts shows an installed plugin's avatar, to find its icon.
- Extended by [0023](0023-new-bank-providers-are-plugins.md): an integration that declares `provides` returns records the bank sync pipeline writes, and new bank providers come this way.

## Consequences

- A new contribution kind decides which of the three it is and inherits that kind's isolation.
- Built-in rule packs (`backend/app/data/rule_packs/`) don't depend on the flag.

## Enforced by

`tests/test_data_plugins.py`, `tests/test_code_plugins.py`, `tests/test_plugin_runner.py`, `tests/test_theme_plugins.py`, `tests/test_rule_pack_provenance.py`.
