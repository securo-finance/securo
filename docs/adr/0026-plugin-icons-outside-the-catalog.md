# 0026. Installed plugins show their catalog icon, read from the catalog only while one is on screen

- Status: Accepted
- Date: 2026-11-18
- Origin: #1621 (issue #1618: Connect Bank shows generated artwork instead of a plugin's icon)
- Extends: [0015](0015-plugins.md), [0016](0016-outbound-calls-and-no-telemetry.md), [0021](0021-plugin-icons.md)

## Context

ADR 0021 put a plugin's icon in the catalog and on its page. Installed plugins and their connections kept the generated badge, because they don't read the catalog: the installed list comes from what the workspace recorded. A provider plugin's icon matters most on the Connect Bank dialog and on Accounts, where someone picks or recognizes the institution.

Only the catalog knows which icon belongs to a plugin. Recording the icon's name at install time would not make Securo independent of the catalog: the icon itself is served from the catalog's host (ADR 0021), so a recorded name still needs the catalog to show anything.

## Decision

- The frontend reads the catalog to find an installed plugin's icon: `usePluginIcons` (`frontend/src/hooks/use-plugin-icons.ts`) shares the Plugins page's catalog query and maps a plugin id to its `icon_url`. No backend change, nothing recorded at install.
- It reads the catalog only while a plugin's avatar is on screen: the Connect Bank dialog (`frontend/src/components/connector-select-dialog.tsx`) asks only when it lists a plugin, and Accounts (`frontend/src/pages/accounts.tsx`) asks only when it shows a plugin connection without a logo of its own. A failed request isn't retried and leaves the badge.
- This amends ADR 0015 and the row for the plugin index in ADR 0016: the index is fetched when someone opens the catalog and, now, when one of these screens shows a plugin's avatar:

| Call | Where | Happens only when |
|---|---|---|
| Plugin index | `app/plugins/index.py` | `PLUGINS_ENABLED` and the catalog is opened, or a screen shows an installed plugin's avatar (Connect Bank, a plugin connection on Accounts) |

## Consequences

- Someone with a plugin connection makes a request to the catalog's host on opening Accounts, at most once per five minutes in a session, where before only the Plugins page did. It is the same request to the same host, behind the same switch, and the browser still reaches only Securo.
- A plugin the catalog no longer lists, or a catalog that can't be reached, shows the badge.
- A screen that adds a plugin avatar uses `usePluginIcons` with its own guard; one that has no avatar to fill never asks.

## Enforced by

`frontend/src/components/connect-dialogs.test.tsx` and `frontend/src/pages/accounts.test.tsx`: the icon is shown, the badge stays when the catalog can't be reached, and the catalog isn't asked when no plugin avatar is on screen.
