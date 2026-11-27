# Demo Bank: an example bank sync plugin

A made-up bank, to show how a plugin brings accounts into Securo the way a
connected bank does (ADR 0023). It has a checking account, a credit card with
a bill and an investment fund with two buys. It never connects anywhere:
every record is made up from the date.

Copy this folder into a repository of your own to start a provider plugin.
`backend/tests/test_example_provider_plugin.py` installs it on the plugin
runner and syncs it through the bank sync pipeline, so it stays in step with
the contract.

## Files

- `securo-plugin.json`: the manifest. The integration's `provides` lists the
  records `sync` returns; `fields` is the connection form (a `secret` field is
  sealed by Securo and reaches the plugin in `context.secrets`, the others in
  `context.config`).
- `demo_bank.py`: the code. `runtime.entrypoint` (`demo_bank:plugin`) names
  the object whose `sync` Securo calls.

## What `sync` gets and returns

Securo calls `plugin.sync(params, context)` on the plugin runner every
`sync_minutes`, and when someone presses sync on the connection.

- `params["since"]`: the first day to read again, as `YYYY-MM-DD`. It is
  empty on the first sync; read your usual history then.
- `params["state"]`: what the last sync returned as `state` (a cursor, say).
  Securo keeps it only once the records are saved.

It returns:

```json
{
  "state": {"last_sync": "2026-11-17"},
  "data": {"contract": 1, "accounts": [], "holdings": []}
}
```

`data` follows contract 1 (`backend/app/plugins/contract.py`), checked whole
before anything is written:

- Amounts are strings (`"12.34"`), never floats. A transaction's `amount` is
  positive and its `type` (`debit` or `credit`) gives the direction.
- A credit card's `balance` is what is owed, as a positive number.
- Every record has an `external_id` that stays the same from one sync to the
  next. That is how Securo knows it already has a record, and how a
  `pending` transaction becomes `posted` instead of a second one.
- A list that is `null` (or left out) wasn't fetched this time, and what
  Securo has is left alone. An empty list was fetched and is empty: holdings
  missing from it are archived. `"data": null` means nothing was fetched yet.
- Trades that add up to a holding's `quantity` become its ledger.

## Errors

Raise an exception whose class is named:

- `NeedsAuth`: the sign-in is wrong or expired; the connection asks the
  person to reconnect. Try it with the access code `expired`.
- `RateLimited`: the service asked to slow down; no failure, Securo tries
  again on the next run. Try it with the access code `busy`.

Anything else marks the sync as failed, with the message shown on the
connection. Never put a secret in a message.

## Talking to a real service

This example reaches nothing. A real provider lists each host it calls in the
manifest's `network`, with the reason; the runner refuses any other. Python's
standard library is there; other packages come from a hashed lockfile named
in `runtime.lockfile`.
