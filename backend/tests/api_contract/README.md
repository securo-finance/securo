# API contract sweep

Every route in the OpenAPI spec, every method, checked against an exact
expected status code.

## Safety: test data only, never a real account

The sweep runs **in-process**, against the throwaway SQLite database the
test suite creates. Its users are fakes from `tests/conftest.py`
(`test@example.com`, an admin, a viewer). Nothing reaches Postgres, a
running stack, or the internet.

Do not point it, or a copy of it, at a real account or a deployed instance.
About 185 of the cases write data: they delete transactions, accounts,
categories and groups, archive workspaces, merge payees, void invoices and
change admin settings. On real data that destroys the data.

To look at a running stack, only read from it, as a disposable user on a
local dev install. Never use prod or your own account.

## Usage

```bash
cd backend
uv run pytest tests/api_contract -n auto     # run the sweep (part of the normal suite)
uv run pytest tests/api_contract -n auto -k "ok:POST /api/budgets"   # one route
```

A failure reads `METHOD path [variant] expected X, got Y: <response>`.
Decide whether the change is intended (see "The baseline" below) before
touching the baseline.

## What runs

Each operation fans out into variants (`cases.py`):

| Variant      | Caller                    | Data                     | Usually expects |
|--------------|---------------------------|--------------------------|-----------------|
| `ok`         | owner (admin for `/api/admin/*`) | seeded world, generated body | 2xx, or the domain rule that applies |
| `viewer`     | viewer-role member        | seeded world             | 403 on writes   |
| `empty`      | owner                     | `{}` body                | 422             |
| `unknown_id` | owner                     | random ids               | 404             |
| `anon`       | nobody                    | random ids               | 401             |

`ok` and `viewer` run against a **world**: one business workspace seeded
through the API (`world.py`), rebuilt per case so DELETEs can't leak. The
other variants run in the default personal workspace without seeded data.

Outbound network is blocked. Routes that call a provider answer with their
own error handling (502/503), and that answer is part of the contract.

## The baseline

`baseline.json` holds the expected status per `variant` and `METHOD path`.
A failing case means the route's behaviour changed:

- **Intentional** (new validation, new field, new rule): regenerate the
  baseline in the same PR. The JSON diff documents the change for review.
- **Regression**: fix the code.

Regenerate (serial, about 2 minutes):

```bash
UPDATE_API_CONTRACT=1 uv run pytest tests/api_contract -n 0
# also dump why `ok` cases didn't reach a 2xx:
UPDATE_API_CONTRACT=1 API_CONTRACT_DETAILS=/tmp/details.jsonl uv run pytest tests/api_contract -n 0
```

Review the diff before committing. The baseline may never expect a 500.
`test_baseline_has_no_unhandled_errors` enforces that, and a new route
with no baseline fails `test_baseline_covers_every_operation`.

## Exceptions, all in `cases.py`

- `KNOWN_BUGS`: a bug on the baseline branch. The baseline holds the
  correct status and the case is a strict xfail until the fix lands.
- `DELIBERATE_500`: routes that raise `HTTPException(500)` on purpose.
- `POSTGRES_ONLY`: SQL SQLite can't run. These are skipped here.
- `BODY_OVERRIDES` / `PARAM_WORLD_KEYS_*`: where the generated request
  isn't enough to reach the happy path.

## Why several test files

CI runs `--dist loadfile`, which keeps a file on one worker. The cases are
sharded over `test_contract_*.py` so the sweep runs in parallel.
