<p align="center">
  <img src="docs/logo.svg" width="200" alt="Securo logo" />
</p>
<h1 align="center">Securo</h1>
<p align="center">
  <a href="https://www.buymeacoffee.com/usesecuro"><img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support%20Securo-FFDD00?logo=buymeacoffee&logoColor=black" alt="Buy Me a Coffee" /></a>
  <br />
  <a href="https://github.com/securo-finance/securo/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/securo-finance/securo/actions/workflows/ci.yml/badge.svg?branch=main&amp;event=push" alt="CI" /></a>
  <img src="https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/tassionoronha/ae627b744aaa2ba89d850ea541c311be/raw/coverage.json" alt="Coverage" />
  <a href="https://github.com/securo-finance/securo/pkgs/container/securo-frontend"><img src="https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/tassionoronha/ae627b744aaa2ba89d850ea541c311be/raw/downloads.json" alt="Downloads" /></a>
  <br />
  <a href="https://artifacthub.io/packages/search?repo=securo"><img src="https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/securo" alt="Artifact Hub" /></a>
  <a href="https://www.gnu.org/licenses/agpl-3.0"><img src="https://img.shields.io/badge/License-AGPL--3.0-blue.svg" alt="License: AGPL-3.0" /></a>
  <a href="https://discord.gg/rUqTKtQ9S4"><img src="https://img.shields.io/badge/Discord-Join%20the%20community-5865F2?logo=discord&logoColor=white" alt="Join our Discord" /></a>
  <br />
  <a href="https://usesecuro.com/">Website</a> · <a href="https://demo.usesecuro.com/">Demo</a> · <a href="https://www.usesecuro.com/roadmap">Roadmap</a> · <a href="https://docs.usesecuro.com/">Docs</a> · <a href="https://discord.gg/rUqTKtQ9S4">Discord</a> · <a href="https://cal.com/tassio/15min">Talk to the maintainer</a>
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
- Recurring transactions, budgets, and loan/financing tracking
- Goals and savings targets with progress tracking
- Asset management with valuation tracking and growth rules
- Reports: Net Worth and Income vs Expenses with category sparklines
- Multi-currency support with automatic FX conversion
- Multi-user support with workspaces, roles, and an admin panel
- Passkeys, two-factor authentication (TOTP), and OIDC single sign-on
- Bank sync via providers (Pluggy, Enable Banking, SimpleFIN, extensible)
- AI Agents (optional): self-hosted LLM chat with tool-use over your data

## Optional capabilities

Everything beyond the core is off by default, needs a single env flag, and costs nothing until you turn it on. Full setup guides live in the [documentation](https://docs.usesecuro.com/):

- **Bank sync**: Pluggy for Brazilian banks, Enable Banking for ~2500 European PSD2 banks, and SimpleFIN for US and international banks. [Setup guides](https://docs.usesecuro.com/features/bank-sync/)
- **Login & security**: passkeys (Touch ID, Face ID, security keys), TOTP two-factor, and OIDC single sign-on with account linking and provider role sync. [Guide](https://docs.usesecuro.com/features/security/)
- **Exchange rates**: a free [Open Exchange Rates](https://openexchangerates.org/) key enables automatic currency conversion; without one, cross-currency amounts fall back to 1:1 with a visual warning. [Details](https://docs.usesecuro.com/deployment/docker/)
- **Timezone**: balances, budgets, and due dates turn over at midnight in your application timezone, set once in Admin Settings, with per-workspace overrides. [Details](https://docs.usesecuro.com/features/workspaces/)
- **AI Agents**: self-hosted assistants over your data, multi-provider, with tool use and a per-agent knowledge base. [Guide](https://docs.usesecuro.com/features/agents/)

## Attachment Storage

Attachments and invoice logos use local disk by default. Set `STORAGE_PROVIDER=s3`
to store them in a private Amazon S3 or S3-compatible bucket (including Garage
and MinIO). Create the bucket first; Securo does not create buckets or change
their access policies. Downloads continue through Securo's existing authorized
API routes, so no public bucket, browser S3 credentials, or bucket CORS policy
is needed.

Example for Garage in the root `.env` (Compose) or `backend/.env` (direct install):

```dotenv
STORAGE_PROVIDER=s3
STORAGE_S3_BUCKET=securo-attachments
STORAGE_S3_REGION=garage
STORAGE_S3_ENDPOINT_URL=https://s3.example.com
STORAGE_S3_ADDRESSING_STYLE=path
STORAGE_S3_ACCESS_KEY=your-access-key
STORAGE_S3_SECRET_KEY=your-secret-key
```

Use the region configured on your Garage server. S3 support is an optional
dependency and is not in the default backend image. With the prebuilt images, set
`BACKEND_IMAGE=ghcr.io/securo-finance/securo-backend:latest-s3` (or a
`<version>-s3` tag) in `.env`; when building from source, set `BACKEND_EXTRAS=s3`;
for direct installs, use `pip install -e ".[s3]"`. For Amazon S3, omit
`STORAGE_S3_ENDPOINT_URL`, use the bucket's AWS region, and leave
`STORAGE_S3_ADDRESSING_STYLE=auto` (the default). `virtual` is also supported.
Temporary credentials can include `STORAGE_S3_SESSION_TOKEN`. Omit both access
and secret keys to use the SDK's credential chain, such as an IAM role; the
container must actually have access to that identity. Standard AWS credential
environment variables are not automatically forwarded by the bundled Compose
files. HTTPS certificate verification remains enabled; `http://` endpoints are
rejected unless `STORAGE_S3_ALLOW_INSECURE_HTTP=true` is set (local development
only, as data is sent unencrypted).

Grant the application only read/write/delete access to its bucket objects
(`s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on AWS). Garage uses its own
bucket permissions: grant the dedicated application key read and write; owner
permissions are not needed. Configure the same storage settings for all backend,
worker, and MCP processes. Restart/recreate the containers after changing `.env`.
Local attachment volumes are unused in S3 mode. AI knowledge documents and
embedding caches still use their separate local storage settings.

### Moving existing attachments

The storage setting applies to every attachment; individual records do not
remember their previous provider. Stop the backend and any worker/MCP processes
that can write files, then copy the **contents** of the existing attachments
directory into the bucket, preserving relative paths as object keys. This also
copies invoice attachments and logos. Verify file counts and contents before
switching the setting and restarting. No database migration is needed. Keep the
original copy until verification is complete; reverting after new S3 uploads
requires copying those objects back to local storage too.

Back up both PostgreSQL and the bucket. Bucket replication does not replace
backups and a database-only restore does not recover attachment contents.

### Testing an S3-compatible server

Unit tests run without an S3 server. An optional integration test exercises the
real SDK over HTTP(S), including bytes, content type, and idempotent deletion.
Use a **dedicated test bucket** and provide these variables only to the test
process (it writes and deletes randomly prefixed test objects):

```bash
SECURO_S3_TEST_ENDPOINT=https://s3.example.com \
SECURO_S3_TEST_REGION=garage \
SECURO_S3_TEST_BUCKET=securo-storage-tests \
SECURO_S3_TEST_ACCESS_KEY=test-key \
SECURO_S3_TEST_SECRET_KEY=test-secret \
uv run --directory backend pytest tests/test_s3_storage_integration.py -v
```

## AI-Assisted Development

Parts of this codebase were built with help of AI. All code is human-reviewed and no data leaves your environment.

Contributing with AI is welcome. We review the author, not the tool: whatever wrote the diff, you own its quality, its fit with where Securo is going, and everything that happens after it merges. See [Using AI](CONTRIBUTING.md#using-ai).

## Support Securo

Securo is free and open source, and it runs entirely on your own infrastructure. If it helps you, a coffee keeps development going:

<p align="center">
  <a href="https://www.buymeacoffee.com/usesecuro"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" width="160" alt="Buy Me a Coffee" /></a>
</p>

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and guidelines.

Not sure where to start, or want to talk something through first? [Book 15 minutes](https://cal.com/tassio/15min) — no agenda needed. Something broken, an idea, or just what you think of Securo, all welcome.

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE).

This means you can freely use, modify, and distribute this software, but any modifications — including when used as a network service (SaaS) — must also be released under the AGPL-3.0.
