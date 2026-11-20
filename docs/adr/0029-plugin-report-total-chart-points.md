# 0029. A plugin report view is bounded in its total chart points

- Status: Accepted
- Date: 2026-11-20
- Origin: #1716 (board task #307)
- Refines: [0028](0028-plugin-reports.md)

## Context

ADR 0028 bounds a plugin's view block by block: 24 blocks, 12 series in a chart, 1,000 points in a series. Every limit holds on its own and the view still isn't bounded: 22 charts of 12 series by 1,000 points pass all three checks and ask the page to draw 264,000 points. Drawn as SVG in the reports tab, that view takes about half a minute and two gigabytes of heap and freezes the page. Nothing in the block limits says how many points one report is worth, so a plugin that merely draws many small charts arrives at the same place as a careless one.

## Decision

`ReportView` (`backend/app/plugins/report_view.py`) carries one total on top of the per-block limits: `MAX_TOTAL_POINTS = 12_000`, the sum of `len(block.x) * len(block.series)` over every chart block of the view, checked by `_bounded_points` once the blocks parse. A chart's series all have one value per `x` (already enforced by `ChartBlock`), so that sum is the number of values the page draws.

The total buys what a report needs instead of what the limits allow: one chart at the block limits is 12,000 points, and so are 24 charts of 500 points. A view above it is refused as any invalid view is (`InvalidView`), and the code report answers 502 (`CodeReportFailed`) with the reason, naming the total: the plugin has to send less. Nothing is downsampled behind the plugin's back, because a chart cut silently loses values the plugin chose to show.

The other block types keep their own caps and stay out of the total: a heatmap is 60 by 60 cells at most, a sankey four links per node, a table 1,000 rows of 12 columns. None of them draws one element per value the way a chart does, so the total would charge them for a cost they don't have.

## Consequences

- A code report that passes every per-block limit can still be refused; the error names the total, and the fix is a smaller view, not a retry.
- One big chart stays possible at the block limits, and many small charts stay possible under the same 12,000 values.
- The number is a rendering budget, not a data budget: what the plugin reads and computes is unchanged.
- A later review of 0028's per-block limits has to keep this total in mind: raising `MAX_SERIES` or `MAX_POINTS` doesn't raise the ceiling of a view.

## Enforced by

`backend/tests/test_report_plugins.py::test_a_view_draws_a_bounded_number_of_points_in_all` (one chart at the limits accepted, a second one refused, the 22-chart view refused, 24 charts of 500 points accepted) and `test_a_failing_or_invalid_code_report_is_an_error_for_the_page` (the 502 with the reason).
