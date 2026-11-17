# Bank sync providers as plugins

A new bank sync provider is a plugin, not a module in the core (ADR 0023).
The plugin keeps everything that touches the outside world: the sign-in, the
credentials, the calls to its service, its cursor and its schedule. The core
keeps the pipeline that writes what the plugin fetched, the same one a native
provider goes through.

This guide has three parts: what a provider plugin is made of and what the
person installing it sees, the contract `sync` receives and returns, and what
the core does with that answer. `examples/plugins/demo-bank/` is a small
working provider to read next to it.

## Part 1: The provider plugin

A plugin is a repository with a `securo-plugin.json` manifest and, when it
runs code, a Python entrypoint. The manifest is checked whole when the plugin
is installed (`backend/app/plugins/manifest.py`): an unknown key is refused,
never ignored.

### Identity

- `id` is `owner/name`, lower case, and it stays with the plugin forever: it
  keys the install, the runner and its connections.
- `name` and `description` are translated text: a map of locale to text with
  at least `en`, or one plain string, which is read as English.
- `version` is semantic, `1.4.0`. `min_securo_version` is the oldest Securo it
  works with; an instance older than that won't offer it.
- `repository` is the plugin's own repository, `license` its license, and
  `workspaces` says whether it is meant for personal or business workspaces
  (both when the key is left out).
- `logos` lists the institution logos shipped inside the package, by key,
  each a PNG or WebP path. Securo serves them itself, so an account showing
  one never makes a browser reach anywhere.

### The integration

An integration is the thing a person connects. A provider plugin declares it
under `contributes.integrations` (a plugin may declare more than one, and each
connection uses one):

```json
{
  "key": "acme",
  "name": "Acme Bank",
  "auth": "credentials",
  "fields": [
    {"key": "customer", "label": "Name on the accounts", "required": false},
    {"key": "access_code", "label": "Access code", "secret": true}
  ],
  "sync_minutes": 360,
  "provides": ["accounts", "transactions", "bills", "holdings", "trades"]
}
```

- `auth` is `credentials` (the person fills `fields` in), `oauth` (the plugin
  builds the sign-in link and trades the code for a token, through its
  `oauth_url` and `oauth_exchange` methods) or `none`.
- `fields` is the connection form, at most 20. A field is required unless it
  says otherwise. A field with `"secret": true` is sealed by Securo
  (`backend/app/plugins/vault.py`) and reaches the plugin in
  `context.secrets`; it is never shown again and never lands in the bank
  connection. Every other field reaches it in `context.config` and stays
  visible on the connection screen, so an account number belongs in a secret
  field even when it isn't a password.
- `sync_minutes` is how often `sync` runs, between 15 and 10080 (one week).
  Without it the plugin syncs only when someone presses sync.
- `provides` is the list of record kinds `sync` returns. `transactions` and
  `bills` need `accounts` alongside them, and `trades` needs `holdings`. A
  payload carrying a kind that isn't declared here is refused.

### Code and reach

- `runtime.entrypoint` names the object Securo calls, as `module:attribute`.
  Only the Python standard library is available unless `runtime.lockfile`
  names a requirements file with a hash for every package.
- `network` lists each host the plugin may reach, with the reason, one entry
  per host. The runner refuses every other host. A wildcard is not a host.
- `api_scopes` is for a plugin that reads or writes the workspace through the
  API instead of bringing records: each scope, with the reason. The call gets
  a token limited to them in `context.securo.token`. A provider that returns
  records does not need any.

The plugin's code never runs inside the backend. It runs in the plugin
runner, a container of its own without the database or the core's secrets,
and its calls leave through the egress proxy to the declared hosts only.

### Installing it

Installing needs `PLUGINS_ENABLED`; a plugin with code also needs the
`plugins` Compose profile, which starts the runner. The catalog is a static
index built outside Securo. When someone opens a plugin, Securo previews what
installing, updating or removing it would change in that workspace, and the
person confirms. That preview is where the manifest's `network` hosts and
`api_scopes` are shown: they are what the person consents to. A plugin is
installed per workspace, and an update the owner agrees to follows the new
manifest, including new hosts and scopes.

## Part 2: What `sync` gets and returns

Securo calls `plugin.sync(params, context)` on the runner, on the schedule and
whenever someone presses sync. One sync runs per connection at a time: a
second one that arrives meanwhile returns without doing anything.

### `params`

- `since`: the first day to read again, `YYYY-MM-DD`. It is `null` on the
  first sync, so read your usual history then. Afterwards it is the bank
  connection's last full sync minus 14 days, so the same window overlaps a
  little every run and late-arriving rows are picked up again.
- `state`: the `state` the previous sync returned, as a dict (`{}` before the
  first one). A cursor, a page marker, whatever the plugin needs next time.
- `last_sync_at`: when the connection last finished, or `null`.

### `context`

- `context.secrets`: the connection form's secret fields, opened for this
  call only.
- `context.config`: the rest of the form.
- `context.securo`: `api_url`, `workspace_id`, `connection_id`,
  `integration`, and `token` when the plugin declared `api_scopes`.

### The answer

```json
{
  "state": {"cursor": "page-2"},
  "data": {"contract": 1, "accounts": [], "holdings": []}
}
```

`state` is kept only after the records are written, so a sync that fails is
fetched again next time with the old cursor. Keep it small: it is stored on
the connection, not in the payload.

`data` follows contract 1 (`backend/app/plugins/contract.py`). It is checked
whole before anything is written, and one bad record fails the sync with a
message naming the field, never the value.

- `contract` is `1`. A change an existing plugin can't ignore gets a new
  contract version; adding an optional field does not.
- `accounts` is a list of accounts, each with its optional `transactions` and
  `bills`. `holdings` is a list, each with its optional `trades`.
- The whole reply is capped: 500 accounts, 20000 transactions, 2000 bills,
  5000 holdings and 20000 trades. Send the rest on the next sync.

### Accounts

Every account has `external_id`, `name`, `type`, `balance` and `currency`.
`type` is `checking`, `savings`, `credit_card`, `loan`, `investment` or
`wallet`. A card also carries what it knows: `credit_limit`,
`statement_close_day`, `payment_due_day`, `minimum_payment`, `card_brand`,
`card_level`, `masked_number` (the last four characters only, never the full
number). `institution_name`, `stable_external_id` and `shared_balance_group`
describe where it comes from and what it shares a balance with.

A card's `balance` is what is owed, as a positive number, like every other
amount in the contract. It is not the available limit and it is not negative.

An account may name its institution's logo by key with `institution_logo`
(one of the manifest's `logos`) or by `institution_logo_url`, an https image
on a host the manifest declares in `network`. One or the other, not both.

### Transactions

`external_id`, `description` (the text the person sees), `amount`, `date`,
`type` (`debit` or `credit`), and optionally `currency`, `status`
(`posted` by default, or `pending`), `payee`, `provider_category` and the
installment fields (`installment_number`, `total_installments`,
`installment_total_amount`, `installment_purchase_date`). An amount is always
positive; `type` says which way the money went.

`provider_category` is a hint, not a category of the workspace: when the
instance has the provider category mapping on, Securo may use it to pre-fill
one, and the workspace's rules still have the last word.

### Bills

`external_id`, `due_date`, `total_amount`, `currency`, and optionally
`minimum_payment`. `raw_data` is kept for reference; the pipeline reads the
contract's own fields, never `raw_data`.

### Holdings and trades

A holding has `external_id`, `name`, `currency` and `current_value`, plus what
it knows: `type` (`stock`, `etf`, `fund`, `crypto` or `investment`),
`quantity`, `unit_price`, `purchase_price`, `purchase_date`, `isin`, `ticker`,
`maturity_date`, `is_withdrawn`, `account_external_id` and `metadata`.

A trade has `external_id`, `kind` (`buy` or `sell`), `date`, `quantity`
(positive), `price` (per unit) and an optional `fee`. Trades that add up to a
holding's `quantity` become that holding's ledger.

### Amounts, ids and lists

- Amounts are strings, `"12.34"`. A float is refused: it loses cents on the
  way. Currency is a three-letter ISO code.
- `external_id` is how Securo knows it already has a record, and how a
  pending transaction becomes posted instead of a twin. It must stay the same
  from one sync to the next, and be unique inside its list.
- A list that is `null`, or left out, was not fetched this run: what Securo
  has is left alone. An empty list was fetched and is empty: a holding
  missing from `holdings: []` is archived.
- `"data": null` means nothing was fetched yet (a report still being built).
  The plugin's `state` is kept and the pipeline doesn't run.

### Errors

Raise a plain exception. Securo reads its class name:

- `NeedsAuth` (or `AuthError`): the sign-in is wrong or expired. The
  connection asks the person to reconnect and the schedule stops for it.
- `RateLimited`: the service asked to slow down. Not a failure; the next run
  tries again.
- Anything else: the sync failed, with the message shown on the connection.
  Secrets in it are masked before it is stored, but never put one there.

## Part 3: What the core does with the payload

`sync` on the plugin side is answered in `backend/app/plugins/connections.py`.
After the call:

- New secrets and the `state` are kept, the secrets even if what follows
  fails, so a rotated token is never lost. The cursor waits for the records.
- `parse_payload` checks the whole `data`: the contract version, every field,
  the caps, the kinds declared in `provides`, and the logos (their key or
  their host). Nothing of an invalid payload is written; the sync is marked
  failed and fetched again next run.
- A valid payload becomes a `PluginPayloadProvider`
  (`backend/app/plugins/provider.py`), which serves it through the
  `BankProvider` interface, and `sync_connection`
  (`backend/app/services/connection_service.py`) runs exactly as it does for a
  native provider (ADR 0006, ADR 0023). Same dedup, same rules, same
  reconciliation, same currency handling.

What that pipeline does with it, in the order it matters to a plugin:

- Accounts are matched by `external_id`. An existing account keeps its row
  and the user's own choices, so a sync never resets a type or a display name
  the person changed. An account the person closed stays closed.
- Balances are provider-owned and overwritten each sync; credit card debt is
  stored as a positive number and its details only move when the plugin sends
  them, never back to empty.
- Bills are matched by their `external_id`, and a transaction carrying
  `bill_external_id` is linked to one.
- Transactions are matched by `external_id`. A pending one that comes back
  posted is promoted in place. A row that doesn't match by id is compared with
  manual rows and with a pending twin (same amount, date and description)
  before being inserted, so a re-sent window doesn't duplicate anything.
- An account whose `transactions` is `null` is a partial feed: no phantom
  cleanup runs for it and the sync checkpoint doesn't move past it. An account
  whose list is `[]` was read and is empty, and its stale rows are cleaned up.
- Holdings are matched by source and id, their value history is kept, and a
  holding missing from a fetched list is archived. Trades become the ledger.
- Rules apply to the new transactions, as they do for a connected bank, and
  transfer detection pairs the account's legs afterwards.
- Once everything is written, the plugin's `state` is kept, the connection is
  active and `last_sync_at` moves. A failure leaves it in `error` (or
  `needs_auth`), keeps the record it has and backs the schedule off, doubling
  the wait per failure up to a day.

The bank connection the records land under is created with the plugin
connection, and its status mirrors the plugin connection's. Removing the
connection disconnects it as a native one is disconnected, accounts and their
transactions included; removing the plugin instead keeps the records, the
accounts carry on as manual ones and its assets are archived. Plugin
connections are synced by their plugin and never by the bank sync schedule:
`sync_connection` refuses one that arrives without a payload.

## Testing your plugin

`backend/tests/test_example_provider_plugin.py` installs the demo plugin on
the real runner, syncs it through `connections.sync` and the bank pipeline,
and checks what landed. A provider plugin can reuse that test as its own
contract check before it is published.
