<p align="center">
  <img src="docs/logo.svg" width="200" alt="Securo logo" />
</p>
<h1 align="center">Securo</h1>
<p align="center">
  <a href="https://github.com/securo-finance/securo/actions/workflows/ci.yml"><img src="https://github.com/securo-finance/securo/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <img src="https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/tassionoronha/ae627b744aaa2ba89d850ea541c311be/raw/coverage.json" alt="Coverage" />
  <a href="https://github.com/securo-finance/securo/pkgs/container/securo-frontend"><img src="https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/tassionoronha/ae627b744aaa2ba89d850ea541c311be/raw/downloads.json" alt="Downloads" /></a>
  <a href="https://www.gnu.org/licenses/agpl-3.0"><img src="https://img.shields.io/badge/License-AGPL--3.0-blue.svg" alt="License: AGPL-3.0" /></a>
  <a href="https://discord.gg/rUqTKtQ9S4"><img src="https://img.shields.io/badge/Discord-Join%20the%20community-5865F2?logo=discord&logoColor=white" alt="Join our Discord" /></a>
  <br />
  <a href="https://usesecuro.com/">Website</a> · <a href="https://demo.usesecuro.com/">Try our Demo</a> · <a href="https://docs.usesecuro.com/">Read the Docs</a> · <a href="https://discord.gg/rUqTKtQ9S4">Discord</a>
</p>

<h3 align="center">Finance apps want your data. This one doesn't.</h3>

<p align="center">
We believe personal finance should actually be <em>personal</em>. No corporation should sit between you and your financial data. Securo is an open-source finance manager that runs on your own infrastructure, giving you full visibility into your accounts, spending, and habits, without surrendering a single byte to third parties. Take back control.
</p>

## Quick Start

**Linux & macOS** (uses Docker or Podman; installs Docker if neither is present):

```bash
curl -fsSL https://usesecuro.com/install.sh | bash
```

**Windows:** Install [Docker Desktop](https://www.docker.com/products/docker-desktop/), then:

```bash
git clone https://github.com/securo-finance/securo.git && cd securo
docker compose up --build
```

Open [http://localhost:3000](http://localhost:3000) and create an account. That's it.

<p align="center">
  <img src="docs/screenshot.png" width="800" alt="Securo dashboard" />
</p>

## Features

- Multi-account management with running balances
- Transaction management with search, filters, and CSV export
- File import (OFX, QIF, CAMT, CSV)
- Auto-categorization rules engine
- Recurring transactions and budgets
- Goals and savings targets with progress tracking
- Asset management with valuation tracking and growth rules
- Reports: Net Worth and Income vs Expenses with category sparklines
- Bank sync via providers (Pluggy for Brazilian banks, Enable Banking for ~2500 European PSD2 banks, SimpleFIN for US and international banks, extensible)
- Multi-currency support with automatic FX conversion
- Multi-user support with admin panel and registration controls
- Two-factor authentication (TOTP) with brute-force protection
- OIDC login support for Authentik, Pocket ID, and other standard providers
- AI Agents (optional): self-hosted LLM chat with tool-use over your data, plus a per-agent RAG knowledge base

## Bank Sync (Optional)

Add credentials for any of the supported providers to `.env`, then restart with `docker compose up`. Configure one or both — each provider auto-registers when its credentials are present.

### Pluggy — Brazilian banks

Sign up at [pluggy.ai](https://pluggy.ai) and add:

```
PLUGGY_CLIENT_ID=your-client-id
PLUGGY_CLIENT_SECRET=your-client-secret
```

### Enable Banking — European banks (PSD2)

Sign up at [enablebanking.com](https://enablebanking.com), create a Production application, and download its PEM private key. Save the PEM to `./secrets/` (gitignored), then add:

```
ENABLE_BANKING_APP_ID=your-application-id
ENABLE_BANKING_PRIVATE_KEY_FILE=/app/secrets/your-key.pem
ENABLE_BANKING_OAUTH_REDIRECT_URI=https://your-host/oauth/callback
```

The redirect URI must match exactly one of the Allowed Redirect URLs in your EB application. Production EB requires HTTPS — for local development, expose your frontend via a tunnel (ngrok, cloudflared) or use the EB sandbox.

> **Free tier — restricted mode.** Enable Banking's free plan requires you to pre-link the accounts you want to import inside the EB portal *before* connecting from Securo. If you skip that step, the connection returns no accounts and Securo will surface a banner with a link to the portal.

### SimpleFIN — US and international banks

[SimpleFIN](https://www.simplefin.org/) is a read-only open protocol. No API key needed — each connection brings its own credentials via a single-use Setup Token from the [SimpleFIN Bridge](https://bridge.simplefin.org/). Just enable the feature:

```
SIMPLEFIN_ENABLED=true
SIMPLEFIN_API_URL=https://beta-bridge.simplefin.org   # sandbox; use bridge.simplefin.org for real banks
```

Then in Securo: **Accounts → Connect Bank → SimpleFIN**, and paste the token. The [developer page](https://beta-bridge.simplefin.org/info/developers) gives out free demo tokens if you want to try it without a real bank.

## OIDC Login (Optional)

Securo can delegate login to any standard OIDC provider, including Authentik and Pocket ID. Create a confidential/web application in your provider and register this redirect URI:

```
https://your-securo-host/api/auth/oidc/callback
```

Then add the provider settings to `.env` and restart:

```
OIDC_ENABLED=true
OIDC_PROVIDER_NAME=Pocket ID
OIDC_DISCOVERY_URL=https://id.example.com/.well-known/openid-configuration
OIDC_CLIENT_ID=securo
OIDC_CLIENT_SECRET=your-client-secret
# Optional; defaults to ${FRONTEND_URL}/api/auth/oidc/callback
OIDC_REDIRECT_URI=https://your-securo-host/api/auth/oidc/callback
```

New OIDC users are auto-provisioned by default (`OIDC_AUTO_REGISTER=true`) using verified email addresses. Set `OIDC_AUTO_REGISTER=false` to allow only existing Securo users whose email matches the provider claim.

### Optional OIDC role sync

Securo can also synchronize provider roles/groups into its built-in permissions when `OIDC_SYNC_ROLES=true`. The default claim is `groups`, which works well with Authentik group mappings and Pocket ID role/group assignments.

```
OIDC_SYNC_ROLES=true
OIDC_ROLES_CLAIM=groups
OIDC_ADMIN_ROLES=securo-admins
OIDC_WORKSPACE_ROLE_MAP={"securo-owners":"owner","securo-editors":"editor","securo-viewers":"viewer"}
```

`OIDC_ADMIN_ROLES` grants or revokes Securo admin (`is_superuser`) on each OIDC login. `OIDC_WORKSPACE_ROLE_MAP` maps provider roles/groups to the user's Personal workspace role (`owner`, `editor`, or `viewer`); if multiple mapped roles are present, Securo applies the highest permission. Leave `OIDC_SYNC_ROLES=false` to keep all Securo roles managed locally.

## Exchange Rates (Optional)

For automatic currency conversion, add a free [Open Exchange Rates](https://openexchangerates.org/) key to `.env`:

```
OPENEXCHANGERATES_APP_ID=your-app-id
```

Rates are fetched on-demand when foreign-currency transactions are created. Without a key, cross-currency amounts default to a 1:1 fallback rate with a visual warning.

## AI Agents (Optional)

Self-hosted AI assistants over your Securo data — multi-provider (OpenAI, Anthropic, Ollama, OpenAI-compatible), tool-use via MCP, per-agent RAG knowledge base, ⌘J global chat panel.

Add to `.env`:

```
AGENTS_ENABLED=true
COMPOSE_PROFILES=agents
```

Then `docker compose up -d`. Settings → AI Agents to add a provider connection. Off by default; zero cost when off.

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
# Run backend tests (from backend/, needs Python 3.11+; same as CI)
cd backend
pip install -e ".[dev]"   # first time only — installs pytest and dev deps
pytest

# Rebuild after dependency changes
docker compose up --build
```

If you've [mise](https://mise.jdx.dev/) installed, you can install backend/frontend directly with it:

```
# Install the Python version specified in .python-version,
# and create a project virtual environment using that Python.
# Install all tools and dependencies (include Python with dedicated venv)
mise //...:install

# Install only backend tools/deps
mise backend:install

# Run backend tests
mise backend:test

# Install frontend dependencies
mise frontend:install

# Run frontend linting
mise frontend:lint

# Run frontend build
mise frontend:build
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE).

This means you can freely use, modify, and distribute this software, but any modifications — including when used as a network service (SaaS) — must also be released under the AGPL-3.0.
