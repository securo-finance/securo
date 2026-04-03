<p align="center">
  <img src="docs/logo.svg" width="200" alt="Flux logo" />
</p>
<h1 align="center">Flux</h1>
<p align="center">
  <a href="https://github.com/securo-finance/securo/actions/workflows/ci.yml"><img src="https://github.com/securo-finance/securo/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <img src="https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/tassionoronha/ae627b744aaa2ba89d850ea541c311be/raw/coverage.json" alt="Coverage" />
  <a href="https://www.gnu.org/licenses/agpl-3.0"><img src="https://img.shields.io/badge/License-AGPL--3.0-blue.svg" alt="License: AGPL-3.0" /></a>
  <br />
  <a href="https://usesecuro.com/">Website</a> · <a href="https://github.com/orgs/securo-finance/projects/2">Roadmap</a>
</p>

<h3 align="center">Finance apps want your data. This one doesn't.</h3>

<p align="center">
We believe personal finance should actually be <em>personal</em>. No corporation should sit between you and your financial data. Flux is an open-source finance manager that runs on your own infrastructure, giving you full visibility into your accounts, spending, and habits, without surrendering a single byte to third parties. Take back control.
</p>

## Quick Start

**Linux & macOS** (installs Docker if needed):

```bash
curl -fsSL https://usesecuro.com/install.sh | bash
```

**Windows:** Install [Docker Desktop](https://www.docker.com/products/docker-desktop/), then:

```bash
git clone https://github.com/securo-finance/securo.git && cd securo
docker compose up --build
```

Open [http://localhost:3000](http://localhost:3000) and create an account. That's it.

### Devcontainer / WSL2

If you're running the repo inside a devcontainer with Docker Desktop on the host, use the same command:

```bash
docker compose up --build
```

This default compose file avoids bind mounts so it works when Docker cannot mount the devcontainer filesystem directly.

<p align="center">
  <img src="docs/screenshot.png" width="800" alt="Flux dashboard" />
</p>

## Features

- Multi-account management with running balances
- Transaction management with search, filters, and CSV export
- File import (OFX, QIF, CAMT, CSV)
- Auto-categorization rules engine
- Recurring transactions and budgets
- Asset management with valuation tracking and growth rules
- Reports: Net Worth and Income vs Expenses with category sparklines
- Dashboard with spending analytics and projections
- Bank sync via providers (Pluggy supported, extensible)
- Multi-currency support with automatic FX conversion
- Dark/light theme, multi-language support, privacy mode

## Bank Sync (Optional)

Create a `.env` file with your [Pluggy](https://pluggy.ai) credentials:

```
PLUGGY_CLIENT_ID=your-client-id
PLUGGY_CLIENT_SECRET=your-client-secret
```

Then restart: `docker compose up`

## Exchange Rates (Optional)

For automatic currency conversion, add a free [Open Exchange Rates](https://openexchangerates.org/) key to `.env`:

```
OPENEXCHANGERATES_APP_ID=your-app-id
```

Rates are fetched on-demand when foreign-currency transactions are created. Without a key, cross-currency amounts default to a 1:1 fallback rate with a visual warning.

## Tech Stack

| Layer | Stack |
|-------|-------|
| Backend | FastAPI, SQLAlchemy, Alembic, Celery |
| Frontend | React, TypeScript, Vite, Tailwind CSS |
| Database | PostgreSQL |
| Queue | Redis + Celery |

## AI-Assisted Development

Parts of this codebase were built with help of AI. All code is human-reviewed and no data leaves your environment.

## Development

```bash
# Run backend tests
docker compose exec backend pytest

# Stable local stack for validation and devcontainers
docker compose up --build

# Live-reload stack for host-native development
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE).

This means you can freely use, modify, and distribute this software, but any modifications — including when used as a network service (SaaS) — must also be released under the AGPL-3.0.
