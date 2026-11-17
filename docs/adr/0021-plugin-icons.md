# 0021. Plugin icons come from the catalog by checksum, served by Securo

- Status: Accepted
- Date: 2026-11-17
- Origin: #1462 (plugin icons, served from the catalog)
- Extends: [0015](0015-plugins.md), [0016](0016-outbound-calls-and-no-telemetry.md)

## Context

The catalog drew a generated badge for every plugin, so that browsing it never told a third party which plugins someone looked at. Bank and broker plugins want the institution's mark instead. Loading an image from GitHub or the plugin's site would break that promise (ADR 0016). Serving it from Securo keeps the promise, but it means reading the catalog's host at one more moment than ADR 0015 allows, which says the catalog is read only when someone opens it.

## Decision

- The catalog builder (the securo-finance/plugins repository) copies a plugin's icon, a PNG or WebP of at most 64 KB at the root of its repository, next to the index under its own checksum: `icons/<sha256>.png`, or the same with a webp extension. The plugin's index entry names that copy in `icon`. The manifest has no field for it.
- A catalog row carries `icon_url` only when the index names the copy by its checksum. The index entry knows it as `IndexEntry.icon_name` (`backend/app/plugins/index.py`); the address itself belongs to the API schema, `PluginCatalogRead` (`backend/app/schemas/plugin.py`). Listing the catalog stays one request: no icon is fetched to list it.
- `GET /api/plugins/icons/<sha256>.png|.webp` (`plugin_icon` in `backend/app/api/plugins.py`) serves it. Like the logos route it takes no sign-in, because it is an `<img>` source. `fetch_icon` reads the file from the icons folder beside the index, on the catalog's host only, without following redirects and capped at 64 KB, and refuses it unless it hashes to its name and is a PNG or WebP by its own signature. Nothing is stored: the answer is immutable for the browser, since a different icon has a different address, and Securo keeps a few icons in memory by name, as it does READMEs, so a repeat costs no request.
- The frontend falls back to the generated badge when a plugin has no icon or it fails to load (`frontend/src/components/plugin-avatar.tsx`).
- This amends ADR 0015: the catalog's host is read when someone opens the catalog and, now, when a browser asks Securo for an icon. It is an outbound call, and it extends the list in ADR 0016:

| Call | Where | Happens only when |
|---|---|---|
| Plugin icons | `app/plugins/index.py` | `PLUGINS_ENABLED` and a browser asks Securo for an icon by its checksum name; to the catalog's host only |

## Consequences

- The browser reaches only Securo, for icons as for everything else in the catalog.
- A request without sign-in can make Securo ask the catalog's host for one file, by a name that must be a sha256 and an image extension. Refusing that would mean remembering which icons the catalog listed, in Redis or a table, for every backend process; a fixed host, a fixed path shape and a 64 KB cap were judged enough.
- An icon never needs refreshing: a new release with a new image is a new checksum, so a new address.
- An icon the catalog names badly, one the entry can't hold or that isn't text at all, costs the icon and never the listing: the plugin still shows, with the generated badge.
- Icons show in the catalog and on a plugin's page. Installed plugins and connections, which don't read the catalog, still draw the badge.
- A plugin that uses a company's mark is responsible for the right to use it; the catalog can revoke it.

## Enforced by

`tests/test_plugin_icons.py`: the row's address, the one-request listing, the checks on the name, the checksum, the type and the size, and that nothing but the catalog's icons folder is ever asked for.
