# seed_perf.py

Populates the database with realistic data for performance benchmarking and manual testing. Must be run inside the backend container since it imports app modules directly.

## Prerequisites

The stack must be running:

```bash
docker compose up -d
```

## Basic usage

```bash
# Full default seed (2024-01-01 through today)
docker compose exec backend python scripts/seed_perf.py

# Quick smoke run — 10% volume, same structure
docker compose exec backend python scripts/seed_perf.py --scale 0.1
```

## What gets seeded

At default settings the script creates the following data for one user:

| Entity | Default count | Controlled by |
|---|---|---|
| Accounts | 5 | `--accounts` |
| Categories | 30 (22 debit + 8 income) | `--categories` |
| Assets | 15 | `--assets` |
| Asset values | one per asset per day in range | `--start-date` |
| Transactions | 100,000 | `--scale` |
| Recurring transactions | 20 | `--scale` |
| FX rates (EUR + BRL) | one per day in range | `--start-date` |

Transactions are randomly distributed across the date range: 75% debit, 25% credit, with lognormal amounts.

## Options

| Flag | Default | Description |
|---|---|---|
| `--email` | `test@securo.app` | Email of the seed user |
| `--password` | `Securo123!` | Password of the seed user |
| `--start-date` | `2024-01-01` | Earliest date for all seeded data (format: `YYYY-MM-DD`) |
| `--scale` | `1.0` | Multiplier for transaction and recurring transaction counts |
| `--accounts` | `5` | Number of accounts (max 5) |
| `--categories` | `30` | Number of categories (max 30) |
| `--assets` | `15` | Number of assets (max 15) |
| `--no-reset` | _(unset)_ | Skip wiping existing data for the seed user before re-seeding |

### `--scale` in detail

`--scale` is a simple multiplier applied only to counts that can grow arbitrarily:

- Transactions: `int(100_000 × scale)`
- Recurring transactions: `int(20 × scale)`

It does **not** affect the number of accounts, categories, assets, or the date range. Use `--scale 0.1` for a fast smoke run and `--scale 2.0` to stress-test with 200k transactions.

### `--start-date` in detail

All time-series data (transactions, asset values, FX rates) spans from `--start-date` to the day the script runs. The default is `2024-01-01`. Use an earlier date to generate longer histories:

```bash
# 5 years of history
docker compose exec backend python scripts/seed_perf.py --start-date 2020-01-01
```

### `--no-reset`

By default the script wipes all existing data for the seed user before inserting. Pass `--no-reset` to skip the wipe — if the user already has transactions the script exits immediately without inserting anything.

## Examples

```bash
# Minimal dataset for a quick UI check
docker compose exec backend python scripts/seed_perf.py \
  --scale 0.1 --accounts 2 --categories 5 --assets 3

# Large dataset for performance profiling
docker compose exec backend python scripts/seed_perf.py \
  --scale 2.0 --start-date 2019-01-01

# Seed a second user without touching the default one
docker compose exec backend python scripts/seed_perf.py \
  --email analyst@securo.app --password Securo123!

# Re-seed only transactions (skip wipe, then add more — only works on a fresh user)
docker compose exec backend python scripts/seed_perf.py --no-reset
```

## Reproducibility

The script uses a fixed RNG seed (`random.Random(42)`), so the same arguments always produce the same data distribution. The only variable is `--start-date` relative to the current date, which determines the date range.
