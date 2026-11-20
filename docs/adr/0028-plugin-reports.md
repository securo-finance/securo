# 0028. Plugins add reports: a query Securo runs, or their own code in the runner, drawn by Securo

- Status: Accepted
- Date: 2026-11-20
- Origin: plugin reports in the reports library
- Extends: [0015](0015-plugins.md)

## Context

The reports page already works as a library: someone keeps the reports they want as tabs, in their order (`useLayout('reports')`, `frontend/src/lib/library.ts`), and the library was built to take a plugin's items under ids of their own. Plugins could add themes there, but not reports. A fixed vocabulary of queries is safe but can't express the reports people actually want (a savings rate, a burn rate, a projection, anything statistical), and ADR 0015 forbids third-party code in the interface.

## Decision

- A manifest declares reports under `contributes.reports` (`Report` in `backend/app/plugins/manifest.py`), of two kinds:
  - **Query reports** are data only: `measure` (`expenses`, `income` or `net`), `group_by` (`category`, `category_group`, `payee`, `account` or `month`), optionally `categories` (keys of the plugin's own categories or of Securo's defaults), `chart` and `limit`. `plugin_report_service.run_report` adds them up with the rules every spending figure uses (`backend/app/services/_query_filters.py`). Nothing of the plugin runs.
  - **Code reports** (`code: true`) are computed by the plugin, so they need a runtime. The manifest names the data it gets in `inputs` (`transactions`, `accounts`, `categories`), and each input needs the matching read scope in `api_scopes`, so the person installing sees and agrees to what the code reads, as for integrations. When someone opens the tab, `plugin_report_service.run_code_report` reads those inputs for the workspace, with the period and accounts the viewer picked, and calls the plugin's `report(params, context)` method in the plugin runner (ADR 0015): its own process and user, no database, the network only through the egress proxy to the hosts it declared. The plugin may compute anything there, with any package its lockfile pins.
- A code report returns a **view** (`backend/app/plugins/report_view.py`): blocks laid on a 12-column grid by their `width` (a phone stacks them):
  - `kpi`: a figure and its change, colored by whether a rise is good news;
  - `chart`: bar, stacked bar, horizontal bar, line, area, stacked area, pie, donut, scatter and combo; a series can be drawn as bars, a line or an area, on a second axis with its own format, as a projection from a point on (dashed), with a color for negatives; reference lines mark a target or a moment;
  - `heatmap`, `treemap` (two levels), `sankey`, `waterfall` and `progress` (against a target);
  - `table`: sortable, paged, with totals, bars behind values and signed colors;
  - `text`.

  A view is data only: no HTML, script, style or URL; colors are `#RRGGBB`; text is shown as text; every list is capped. A view that breaks those rules is refused before anyone sees it.
- `GET /api/reports/plugins/{owner}/{name}/{key}` (`get_plugin_report` in `backend/app/api/reports.py`) answers both: a query report's rows, or a code report's checked view. It reads the report from the manifest the workspace installed, never from the catalog, and answers 404 while `PLUGINS_ENABLED` is off or the workspace doesn't have the report, 503 when a code report can't reach the runner, 502 when the plugin fails or returns something that isn't a view. It is a read like any report: whoever can see the workspace, a viewer included, and an access token with `reports:read`.
- The frontend draws both with one renderer, `ReportView` (`frontend/src/components/reports/ReportView.tsx`), with Securo's chart components, privacy mode and the person's language and currency. A query report's rows become a view first (`frontend/src/lib/plugin-report-view.ts`). The reports library lists the installed plugins' reports next to Securo's own under `plugin:<owner/name>:<key>`; a kept one is a tab, drawn by `PluginReport` (`frontend/src/components/reports/PluginReport.tsx`) with the page's period picker.

## Consequences

- A plugin can bring a report Securo doesn't have, simple or elaborate, without any of its code reaching the interface.
- A view is bounded per block — 24 blocks, 12 series in a chart, 1,000 points in a series — and in all: at most 12,000 chart points across its charts, the series times the points of each. Without that total, a view inside every per-block limit (22 charts of 12 series by 1,000 points, 264,000 points) is accepted and then takes about half a minute and two gigabytes to draw. One chart at the per-block limits is 12,000 points and still fits, and so do 24 charts of 500 points. A code report above the total is a 502 with the reason, as for any other invalid view, and has to send less: nothing is downsampled behind the plugin's back.
- A code report runs the plugin each time it is opened, in the runner's limits (60 seconds and 512 MB a call by default). It is handed at most 20,000 transactions a run; past that, `truncated` says so.
- What a code report reads, it can send to the hosts it declared: the consent shows both, as for an integration.
- Removing the plugin takes its reports out of the library; a tab someone kept for it is left out of the page, and their saved arrangement keeps working.
- A query report follows Securo's spending rules, so it never disagrees with the dashboard about what counts as an expense. It leaves out the shares of group splits the dashboard adds for members of a group, and recurring projections.

## Enforced by

`backend/tests/test_report_plugins.py`: the manifest's rules for both kinds (inputs need their read scopes, code needs a runtime), the sums of query reports, the view's limits, a code report run through the real runner with its inputs and the accounts picked, and the 404, 422, 502 and 503 answers. `frontend/src/components/reports/ReportView.test.tsx`, `frontend/src/components/reports/PluginReport.test.tsx`, `frontend/src/components/library-dialog.test.tsx` and `frontend/src/lib/library.test.ts`: the blocks as drawn, text kept as text, privacy mode, the tab, and saved plugin tabs kept while the plugins can't be read.
