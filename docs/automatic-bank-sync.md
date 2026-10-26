# Automatic bank synchronization

`BANK_SYNC_MIN_INTERVAL_HOURS` controls how old the last successful bank sync
must be before a connection is eligible for automatic synchronization.
The default is **4 hours**, preserving the previous behavior. Any finite,
positive number of hours within Python's supported date range is accepted;
zero, negative/non-finite values and intervals that overflow the sync cutoff
fail startup validation.

Celery beat still checks hourly. Connections with no previous successful sync
remain eligible, and manual/on-demand synchronization is unchanged. A connection
is selected only when `last_sync_at` is strictly older than the cutoff, as before;
therefore the actual period can be longer than the configured minimum by up to
one polling tick (plus execution time). Startup-triggered automatic syncs use
the same eligibility filter.

## PSD2 unattended access

Banks may limit unattended account-information access to **four times per day**
under [Article 36(5)(b) of Delegated Regulation (EU) 2018/389](https://eur-lex.europa.eu/eli/reg_del/2018/389/oj).
For those banks, set `BANK_SYNC_MIN_INTERVAL_HOURS=6` in the worker environment.
The supplied development and production Compose files pass it through the shared
backend environment to API/worker/beat processes. Restart processes after changing
configuration. This setting applies to all bank providers, not just Enable Banking.

This is a spacing policy, **not a strict rolling request quota**: failures and
rate-limited runs do not advance `last_sync_at`, so they remain eligible on later
hourly checks. Manual syncs, multiple connections to the same bank and paginated
or retried provider requests may also consume access. A six-hour interval alone
does not guarantee compliance with every bank's per-resource accounting policy.
There is no change to provider refresh strategy or transaction fetch windows.
