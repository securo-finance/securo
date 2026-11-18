# Bank provider plugins

A new bank sync provider is a plugin, not a module in the core
([ADR 0023](../adr/0023-new-bank-providers-are-plugins.md)). The plugin fetches
from its service; the core checks what it returns and writes it through the
same bank sync pipeline Pluggy, Enable Banking and SimpleFIN use.

This guide has three parts:

1. [Installing and registering a plugin](#1-installing-and-registering-a-plugin)
2. [The contract a plugin answers](#2-the-contract-a-plugin-answers)
3. [What the sync does with the payload](#3-what-the-sync-does-with-the-payload)

The working reference is the Demo Bank plugin in
[`examples/plugins/demo-bank/`](../../examples/plugins/demo-bank/): a manifest,
the code and a README. `backend/tests/test_example_provider_plugin.py` installs
it on the plugin runner and syncs it through the real pipeline, so it fails
when the example no longer fits the contract. Copy that folder to start, and
read it alongside this guide rather than relying on the snippets here.

General plugin docs (publishing to the catalog, the manifest's common fields,
OAuth sign-in, local testing) are on the docs site:
[Writing a plugin](https://docs.usesecuro.com/features/plugins/authoring) and
[Writing a plugin with code](https://docs.usesecuro.com/features/plugins/code).

## 1. Installing and registering a plugin

### On the instance

A provider plugin runs code, so the instance needs the plugin runner:

```bash
PLUGINS_ENABLED=true
PLUGINS_RUNNER_URL=http://plugins-runner:8090
PLUGINS_RUNNER_TOKEN=...      # shared by the backend and the runner
PLUGINS_EGRESS_SECRET=...     # shared by the runner and the egress proxy
COMPOSE_PROFILES=plugins      # or: docker compose --profile plugins up
```

See the `plugins-runner` service in `docker-compose.prod.yml`. The runner has
none of the backend's secrets and no database; each plugin goes out only
through the egress proxy, to the hosts its manifest declares in `network`.

### Publishing

The plugin lives in its own public repository with a `securo-plugin.json` at
the root, and reaches instances through the plugin catalog
(`PLUGINS_INDEX_URL`), like any other plugin. Nothing is added to this
repository.

### In a workspace

1. The workspace owner installs the plugin from the catalog. The preview lists
   every host in `network` with its `purpose`.
2. Someone adds a connection: they fill in the integration's `fields` (or sign
   in, with `auth: "oauth"`). Secret fields are sealed by
   `backend/app/plugins/vault.py`; they never go into
   `bank_connections.credentials`.
3. Securo creates a bank connection for it, with provider `plugin:<plugin id>`,
   listed under Bank Connections from the start
   (`backend/app/plugins/connections.py`). Accounts the plugin returns are
   written under that bank connection.
4. The plugin's `sync` runs right after the connection is created, then every
   `sync_minutes`, and when someone presses **Sync now**. The bank sync
   schedule never syncs a plugin connection: `sync_connection` refuses one
   without a payload.

Removing the connection disconnects it as a native one is, accounts and
transactions included. Removing the plugin, or installing an update that no
longer `provides`, keeps the records: the accounts carry on as manual ones.

## 2. The contract a plugin answers

### Manifest: `provides`

An integration becomes a bank provider by declaring `provides` in
`contributes.integrations[]`
(`backend/app/plugins/manifest.py`, `Integration`):

| Kind | Needs | What it is |
|------|-------|------------|
| `accounts` | | Accounts with their current balance |
| `transactions` | `accounts` | Each account's transactions |
| `bills` | `accounts` | Credit card bills |
| `holdings` | | Investment positions |
| `trades` | `holdings` | Buys and sells that make up a position |

`provides` can't be empty or repeat a kind. A payload that carries a kind the
integration doesn't declare is refused. The rest of the integration is as for
any plugin with code: `key`, `name`, `auth`, `fields`, `sync_minutes` (15 to
10080). See `examples/plugins/demo-bank/securo-plugin.json`.

### `sync(params, context)`

| `params` | |
|----------|---|
| `since` | First day to read again, `YYYY-MM-DD`: the bank connection's last full sync minus 14 days. `None` on the first sync; read your usual history then |
| `state` | What the last successful sync returned as `state` (`{}` the first time) |
| `last_sync_at` | When the plugin connection last synced, or `None` |

`context.secrets` holds the secret fields (and what the plugin stored as
`secrets`), `context.config` the others.

It returns:

```json
{
  "state": {"cursor": "..."},
  "secrets": {"refresh_token": "..."},
  "data": {"contract": 1, "accounts": [], "holdings": []}
}
```

`secrets` is optional (a rotated token, say). `data` is the payload.

### The payload

`backend/app/plugins/contract.py` is the source of truth: each model there
(`Account`, `Transaction`, `Bill`, `ReservedBalance`, `Holding`, `Trade`,
`Payload`) mirrors a
provider dataclass in `backend/app/providers/base.py` field for field, and
unknown fields are refused. The rules that catch people out:

- `contract` is `CONTRACT_VERSION` (now `1`). A change an existing plugin
  can't ignore gets a new version; adding an optional field doesn't.
- Amounts are strings (`"12.34"`) or integers, never floats.
- A transaction's `amount` is positive; `type` (`debit` or `credit`) gives the
  direction. A credit card's `balance` is what is owed, as a positive number.
- Every record has an `external_id`, unique in its list and the same from one
  sync to the next.
- `null` (or a missing list) means not fetched this run: what Securo has is
  left alone. `[]` means fetched and empty. `"data": null` means nothing was
  fetched yet.
- At most 500 accounts, 20,000 transactions, 2,000 bills, 2,000 reserved
  balances, 5,000 holdings and 20,000 trades per reply. Each limit counts the
  whole reply, summed across accounts, and a reply over any of them is
  refused whole. Reserved balances can't be split across syncs: a reported
  list is the account's whole set, and reserves missing from it are deleted.
- `masked_number` is the last four characters only.
- An account's logo is either `institution_logo` (a key of the manifest's
  `logos`) or `institution_logo_url` (https, on a host declared in
  `network`), never both.

### Reserved balances

An account can carry `reserved_balances`: money the institution holds
reserved inside it, such as cofrinhos, "saldo reservado" or a judicial hold
([ADR 0022](../adr/0022-provider-reserved-balances.md)). They are for display
only. Securo shows them beside the account's balance and never adds them to
anything: not the account's balance, net worth, dashboard totals, goals,
balance history, holdings or the cashflow forecast. Keep reporting the
account's `balance` as the institution gives it; it means the same with or
without reserves.

```json
"reserved_balances": [
  {"external_id": "pocket-trip", "name": "Trip", "currency": "BRL", "amount": "1500.00"}
]
```

- `external_id`, `currency` and `amount` are required; `name` is optional.
- `amount` is a string (or integer), zero or positive. A zero reserve is kept
  and shown.
- The identity is `(external_id, currency)`, unique within the account. A
  reserve in two currencies is two records with the same `external_id`. A
  new `name` for the same identity renames it in place.
- `null` (or leaving the field out) means not reported this run: the reserves
  Securo stored are kept. Send it when you couldn't read them completely, too,
  rather than a partial list.
- A list is the whole set: reserves Securo has that aren't in it are deleted,
  and `[]` clears them all.
- They come with accounts, so `provides` needs only `accounts`; there is no
  separate kind.

A bank account's page lists each reserve in its own currency, with a
subtotal that adds only the reserves in the account's currency; no reserve is
converted. A credit card's page doesn't show reserves, though the contract
accepts and stores them.

### Errors

Raise an exception whose class is named:

| Exception | Connection | Schedule |
|-----------|------------|----------|
| `NeedsAuth` (or `AuthError`) | `needs_auth`; the bank connection shows as expired and asks to reconnect | Stops until the person reconnects |
| `RateLimited` | Stays `active`, the message is kept; not a failure | Tries again after the usual `sync_minutes` |
| Anything else | `error`, with the message shown | Backs off: the wait doubles with each failure, up to 24 hours |

Any secret value in a message is masked before it's stored or shown. Demo Bank
shows both named errors: access code `expired` and `busy`.

## 3. What the sync does with the payload

`connections.sync` in `backend/app/plugins/connections.py`, in order:

1. Calls the plugin's `sync` in the runner.
2. Keeps any new `secrets` at once, so a rotated token isn't lost even if the
   rest fails.
3. Checks the whole payload (`parse_payload`). If anything is invalid, the
   sync fails with a message naming where the problem is (never the values),
   nothing is written, and `state` isn't kept, so the next sync fetches again.
4. Hands the payload to `sync_connection` in
   `backend/app/services/connection_service.py` through
   `PluginPayloadProvider` (`backend/app/plugins/provider.py`). From there it
   is the same code as a native provider's:
   - accounts are matched by `external_id` and get their balance;
   - transactions are deduplicated by `external_id`; a `pending` one becomes
     `posted` in place. The connection's own settings (pending import, payee
     source) apply;
   - rules run on new transactions, and transfer detection looks for pairs
     among them;
   - bills are written for their account;
   - reserved balances are upserted by `(external_id, currency)` under their
     account, and a reported list removes the ones missing from it; the
     account's balance doesn't change;
   - holdings are matched by `external_id` (scoped to the connection, so two
     connections of the same plugin don't collide); holdings missing from a
     `[]` list are archived; trades that add up to a holding's `quantity`
     become its ledger;
   - an account whose `transactions` is `null` is a partial feed: no cleanup
     of transactions the provider no longer lists, and the account's last
     sync time doesn't move;
   - currencies and conversion follow ADR 0008 and ADR 0018.
5. Only once the records are saved, keeps the plugin's `state` and marks the
   connection `active`. If saving fails, the connection shows "Securo couldn't
   save what the plugin returned" and the cursor stays where it was.

With `"data": null`, step 4 is skipped: `state` is kept and the connection is
`active`.

The bank connection mirrors the plugin connection's status: `pending` and
`active` show as active, `error` as error, `needs_auth` as expired.

## Tests

`backend/tests/test_provider_plugins.py` (a payload through the pipeline, the
schedule leaving plugin connections alone), `backend/tests/test_plugin_contract.py`
(the contract), `backend/tests/test_plugin_trades.py` and
`backend/tests/test_example_provider_plugin.py` (Demo Bank).
