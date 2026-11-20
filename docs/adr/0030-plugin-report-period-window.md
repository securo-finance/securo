# 0030. A plugin report reads at most 731 days, and the tab trims its window to that

- Status: Accepted
- Date: 2026-11-20
- Origin: #1715 (board tasks #306 and #309)
- Refines: [0028](0028-plugin-reports.md)

## Context

ADR 0028 lets a plugin's report read the workspace's transactions, accounts and categories for the period the viewer picked, and the reports page offers presets and a custom range. Those ranges run longer than a plugin report should read in one call: the two-year preset spans up to 781 days depending on the day, and a custom range up to ten years. The API refused anything past its own ceiling with a 422 whose text is in English and speaks of days, which a plugin report tab showed as a technical error — and on the two-year preset it broke every plugin tab on many days of the year, because the preset's window is a rolling 24 months, longer than the ceiling.

## Decision

A plugin report reads a period whose end is at most `MAX_DAYS = 731` days after its start (`backend/app/services/plugin_report_service.py`: `(end - start).days > MAX_DAYS`), two years; a longer period is answered with a 422, as before. The limit is the difference between the two dates, and both are read, so an accepted report can cover 732 calendar dates: 2024-01-01 through 2026-01-01 is exactly 731 days apart and both ends count. 731 is a day more than two calendar years, so a two-year preset that lands on a leap day still fits.

The reports page does not hand the tabs a window the API will refuse. `PLUGIN_REPORT_MAX_DAYS` and `clampPeriodWindow` (`frontend/src/lib/report-period.ts`) trim a window to that same 731-day difference, keeping its end and moving the start to 731 days before it (732 dates end to end, as the API reads); the report then shows the most recent two years of what was asked instead of the oldest, and `reports.tsx` passes that trimmed window to `PluginReport`. When it was trimmed, the tab says which span it read (`reports.pluginReport.periodTrimmed`) rather than showing a period the numbers don't cover.

A 422 from the API still reaches the person as a translated line naming the two-year limit (`reports.pluginReport.periodTooLong`), with no retry button: the same window would fail again. Every locale carries the two strings.

## Consequences

- A plugin report never reads more than 732 calendar dates in one call (731 days between the ends), whatever the page's preset says, and what it read is visible on the tab.
- The two-year preset no longer breaks plugin tabs on any day of the year; the tab shows the two years ending today.
- The limit is spelled in two places on purpose, `MAX_DAYS` for the API and `PLUGIN_REPORT_MAX_DAYS` for the page, because neither side imports the other; nothing yet fails when the two numbers drift apart, so moving one means moving the other in the same pull request.
- A custom range longer than two years is trimmed, not refused, so the person sees a report instead of an error.

## Enforced by

`backend/tests/test_report_plugins.py`: the 422 past `MAX_DAYS`. `frontend/src/lib/report-period.test.ts` and `frontend/src/pages/reports.test.tsx`: the clamp keeps the end and the tab is handed the trimmed window; `frontend/src/locales/i18n.test.ts`: both strings exist in every locale.
