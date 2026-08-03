# Trading 212 connector

Securo's Trading 212 connector is read-only. It uses Trading 212 API credentials
for account summary, positions, transaction/dividend history, and completed
order history. It never sends order, cancellation, pie, or export-creation
requests.

## Connect or reconnect

From Accounts, choose **Trading 212** and paste either:

```
<api-key>:<api-secret>
```

or, for the demo environment:

```
demo:<api-key>:<api-secret>
```

Live is the default when the environment prefix is omitted. The callback checks
credentials by reading account summary before it creates or replaces a
connection. Both parts are encrypted before persistence; do not put them in
configuration files, commit messages, issue reports, or exported diagnostics.

The existing token reconnect dialog uses the same callback with the connection
ID. Reconnect replaces only encrypted credentials and connection identity; it
does not delete accounts, holdings, historical ledger rows, names, masks, or
user settings.

## What syncs

- One connected cash account with provider metadata kept separately from the
  display name and masked-number UI.
- Current positions as investment assets.
- Cash deposits/withdrawals and dividends as idempotent account transactions.
- Completed order fills as idempotent asset-ledger transactions, with ignored
  settlement rows used only to reconcile brokerage cash.

Provider metadata is updated when Trading 212 supplies a complete snapshot. A
sparse response does not overwrite metadata already stored on an account or
holding. Trading 212's occasional stale transaction-history next-page cursor is
recovered from the last returned transaction only for its documented HTTP 404
`/api-errors/entity-not-found` response; unrelated 404s still fail normally.

## Migration safety

Alembic revision `066` extends upstream head `065` and is the only migration
head. It adds generic provider columns (`bank_connections.kind`,
`accounts.external_metadata`, and `asset_transactions.raw_data`). Before each
addition it inspects the target table. This deliberately supports installations
that previously had equivalent local Trading 212 columns: existing columns are
left untouched, and no row data is renamed, rewritten, or dropped.

Back up first, then run the normal application migration command. Do not use a
legacy fork migration chain alongside this revision.

### Intentional downgrade behavior

Revision `066` is intentionally non-reversible. Its Alembic `downgrade()` is a
no-op: it permits revision bookkeeping to move back to `065` while retaining
`kind`, `external_metadata`, and `raw_data` in the database. Retaining those
legacy-compatible columns is deliberate because `066` cannot safely tell
whether a column was added by this revision or already existed in a local
legacy schema. Do not manually drop those columns as part of a downgrade; that
would make a rollback destructive for affected installations. Revision `065`
ignores the retained columns, so this bookkeeping-only downgrade is safe.
