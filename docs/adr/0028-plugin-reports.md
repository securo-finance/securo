# 0028. Plugin reports are a query and a chart that Securo runs and draws

- Status: Accepted
- Date: 2026-11-20
- Origin: plugin reports in the reports library
- Extends: [0015](0015-plugins.md)

## Context

The reports page already works as a library: someone keeps the reports they want as tabs, in their order (`useLayout('reports')`, `frontend/src/lib/library.ts`), and the library was built to take a plugin's items under ids of their own. Plugins could add themes there, but not reports. A report that ran a plugin's code, or drew with its JavaScript, would break ADR 0015: no third-party code in the interface, nothing of a data or interface plugin runs.

## Decision

- A manifest declares reports under `contributes.reports` (`Report` in `backend/app/plugins/manifest.py`). A report is data only: `measure` (`expenses`, `income` or `net`), `group_by` (`category`, `category_group`, `payee`, `account` or `month`), optionally `categories` (keys of the plugin's own categories or of Securo's defaults), `chart` (`bar`, `line`, `area`, `pie` or `table`) and `limit` (the largest groups shown, the rest added up as one). There is no field for a query, a formula or code. Charts that can't show the data are refused at validation: `line` and `area` only by month, `pie` never by month nor for a net.
- `GET /api/reports/plugins/{owner}/{name}/{key}` (`get_plugin_report` in `backend/app/api/reports.py`) runs it with `plugin_report_service.run_report` (`backend/app/services/plugin_report_service.py`): it reads the report from the manifest the workspace installed, never from the catalog, and adds up the workspace's transactions with the rules every spending figure uses (`backend/app/services/_query_filters.py`: posted rows, category lines, no transfers or ignored categories, in their primary currency, by the reporting date the card accounting mode picks), for the period and accounts the person picked. It answers 404 while `PLUGINS_ENABLED` is off or the workspace doesn't have the report.
- It is a read like any report: whoever can see the workspace, a viewer included, and an access token with `reports:read`, since the route sits under `/api/reports`.
- The reports library lists the reports of the plugins installed in the workspace next to Securo's own, under the id `plugin:<owner/name>:<key>`. Adding one makes it a tab; the tab is drawn by `PluginReport` (`frontend/src/components/reports/PluginReport.tsx`) with Securo's own chart components and the page's period picker.

## Consequences

- A plugin can add a report without anyone running its code, and the report can't read more than the person looking at it can.
- What a report can say is limited to the measures, groupings and charts above. A new kind of report is a change here, in Securo, not in a plugin.
- Removing the plugin takes its reports out of the library; a tab someone kept for it is left out of the page, and their saved arrangement keeps working.
- The figures follow Securo's own spending rules, so a report never disagrees with the dashboard about what counts as an expense. It leaves out the shares of group splits that the dashboard adds for members of a group, and the recurring projections.

## Enforced by

`backend/tests/test_report_plugins.py`: the manifest's rules, listing reports for installed plugins, the sums by month, category, payee and account, the limit, the account filter, the viewer, the 404s and the period checks. `frontend/src/components/reports/PluginReport.test.tsx` and `frontend/src/lib/library.test.ts`: the tab, the chart and the library entries.
