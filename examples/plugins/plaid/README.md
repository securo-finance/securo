# Plaid: bank sync provider plugin

A reference bank sync provider plugin for the [Plaid API](https://plaid.com/docs/)
built on Securo's provider plugin contract ([ADR 0023](../../../docs/adr/0023-new-bank-providers-are-plugins.md),
[ADR 0051](../../../docs/adr/0051-plugin-hosted-signin-and-incremental-sync.md)).

Copy this folder into your own public GitHub repository with the `securo-plugin`
topic to publish or self-host it in your plugin catalog.
`backend/tests/test_plaid_provider_plugin.py` installs it on the real plugin
runner and exercises Hosted Link, Update Mode, `/transactions/sync`,
`/liabilities/get`, and `/investments/*` through Securo's bank sync pipeline.

## What it syncs

- **Depository & wallet accounts** (checking, savings, PayPal): live balances
  and cursor-based incremental `/transactions/sync` updates (`added`, `modified`,
  `removed`, and explicit `pending_transaction_id` settlement).
- **Credit cards**: balance, credit limit, minimum payment, due/close days,
  latest statement bill (from `/liabilities/get`), and transactions.
- **Loans & mortgages** (e.g. Nelnet student loans, home mortgages): principal
  balance, loan kind, original amount, APR, term, origination date, payment due
  day, and minimum payment (from `/liabilities/get`).
- **Investments** (e.g. M1 Finance, brokerages): holdings with tickers/ISINs,
  buy/sell trade history (`/investments/transactions/get`), and
  `auto_invested_balance` deduction so brokerage cash and holdings are never
  double-counted in net worth.

## Setup

1. Create a Plaid developer account (free **Sandbox** or **Production** Trial /
   Pay-As-You-Go) and copy your `client_id` and environment `secret`.
2. Hosted Link redirects back to Securo's callback URL automatically via
   `hosted_link.completion_redirect_uri` (`https://<your-securo-host>/plugins/oauth/callback`,
   or `http://localhost:3000/plugins/oauth/callback` in local Sandbox
   development). Registering this URL under **Team Settings → API → Allowed
   redirect URIs** in the Plaid Dashboard is optional for desktop/mobile web
   Hosted Link (it is only required if you later configure an OAuth
   `redirect_uri` for native mobile app-to-app flows).
3. Enable Securo plugins (`PLUGINS_ENABLED=true`, `COMPOSE_PROFILES=plugins`)
   and install this plugin from your catalog.
4. Add a connection per institution:
   - **Standard banks & credit cards (e.g. Chase, PayPal)**: keep default
     `products = transactions`. Investments and liabilities are requested as
     `optional_products` so they sync automatically if supported.
   - **Loan-only servicers (e.g. Nelnet)**: set `products = liabilities` so
     Plaid Link includes institutions that do not support `/transactions/sync`.
   - **Brokerage-only accounts (e.g. M1 Finance)**: set `products = investments`.
5. Complete Plaid Hosted Link in the redirected window. When Plaid redirects
   back to Securo, the plugin fetches the session via `/link/token/get`,
   exchanges the `public_token` for a sealed `access_token`, and triggers the
   initial sync.

## Re-authentication (Update Mode)

When an institution requires re-authentication (`ITEM_LOGIN_REQUIRED`), the
plugin raises `NeedsAuth` and Securo marks the connection as needing sign-in.
Clicking **Sign in** / **Reconnect** opens Plaid Hosted Link in **Update Mode**
using the existing `access_token`, preserving the same Item ID, accounts, and
transaction history without duplicates.

## Zero third-party dependencies

`plaid_plugin.py` uses Python's standard library only (`urllib.request`,
`json`, `decimal`, `datetime`) and talks only to `production.plaid.com` and
`sandbox.plaid.com` through `plugins-egress`.
