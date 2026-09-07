# Provider settings

Administrators can configure Open Exchange Rates, Pluggy and Enable Banking in
the admin settings screen after installation. SimpleFIN has an instance enable
switch; its setup tokens and access URLs remain specific to each bank connection.

Credentials are write-only: API responses contain configuration status and source,
never a saved value. Secrets use the existing Fernet encryption derived from
`SECRET_KEY`. Keep that key stable and backed up; after rotation, re-enter saved
provider credentials. An unreadable override is reported as invalid and never
silently replaced by an environment credential.

Each nonempty saved field overrides its environment setting. Omitted fields are
unchanged; null removes an override and restores environment fallback. The UI
leaves saved password fields empty and provides an explicit reset action.
An in-app Enable Banking private key also overrides the environment key file.

Provider availability and credentials are resolved from the database for each
operation, including worker jobs. Already running operations may finish using
their original credentials. Subsequent operations use the new settings without
restarting processes. Configuration status means required fields are present,
not that the provider has verified the credentials.

## Implementation and verification

1. Add encrypted settings storage, write-only admin endpoints and access tests.
2. Resolve runtime credentials and availability; isolate authentication caches.
3. Add accessible admin forms with configuration status, replacement and reset.

Source lives in `backend/app/services`, `backend/app/api`, `backend/app/providers`
and `frontend/src/components`. Tests use pytest in `backend/tests` and Vitest
alongside frontend components. Follow existing async service and React Query
patterns, for example `await session.execute(select(AppSetting))`.

Run `python -m pytest tests/test_provider_settings.py` from `backend`, then the
existing provider, connection, FX and admin suites. Run `npm test`,
`npm run lint` and `npm run build` from `frontend`. Verify unauthorized access,
encrypted database values, response redaction, environment fallback, reset,
credential replacement and availability changes across separate sessions.
Do not log credential values or include them in validation errors. No provider
SDKs or external integrations are needed; `tzlocal`, already present through the
dependency graph, is declared directly for operating-system timezone detection.

## Backend timezone

The admin System section accepts IANA timezone names. The `timezone` row in
`app_settings` takes precedence over the existing `TZ` environment variable,
then the operating system timezone. UTC is used only if none can be resolved.
New API requests, MCP tool calls and scheduled jobs
load the current setting. Calendar dates in reports, invoices, recurring
transactions, asset growth and AI context use that timezone. UTC timestamps
for audit records, authentication and elapsed-time comparisons remain UTC.

Existing Celery schedules use elapsed intervals rather than wall-clock cron
times. Their cadence is unchanged; their date-sensitive work observes the
configured local day. An operation already running keeps its timezone snapshot.

Apply migration `086` before using provider settings; it widens the existing
settings value column to accommodate encrypted PEM keys. The migration does
not copy environment secrets into the database.
