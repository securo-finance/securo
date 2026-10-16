# End-to-end (e2e) tests for web

Browser end-to-end tests for Securo, authored with [KaneCLI](https://www.testmuai.com/kane-cli/) by TestMu AI. Each test is a plain-English `_test.md` file; KaneCLI runs it in a real Chrome browser and records each step so later runs replay without re-authoring.

| Test | What it covers |
|---|---|
| `account_setup_test.md` | First-run setup of the admin account, skipping the tour, then adding a savings account with an opening balance and checking it in "Manual Accounts" |
| `transaction_test.md` | First-run setup, then recording an expense on the default "Wallet" account and checking it in the transactions list and the account balance |

They complement the backend (pytest) and frontend (Vitest) suites by going through the whole app in a browser. Nothing here is picked up by those suites or copied into the Docker images.

## Run the tests

The tests expect Securo at `http://localhost:3000` on a fresh database, because both start from the first-run setup page:

```bash
cp .env.example .env
docker compose up --build
```

Each test creates the admin account, so reset the database before running the next one, for example with `docker compose down -v` and `docker compose up`.

### With KaneCLI

```bash
npm install -g @testmuai/kane-cli
kane-cli login
kane-cli testmd run e2e/kane/account_setup_test.md --agent
kane-cli testmd run e2e/kane/transaction_test.md --agent
```

### With the exported Playwright code

Each test also has an exported Playwright (JavaScript) version under `output-<test>/playwright-javascript-code/`:

```bash
cd e2e/kane/output-transaction/playwright-javascript-code
npm install
npx playwright install chromium
node test.js
```

Copy `.env.example` to `.env` and fill in your TestMu AI (LambdaTest) credentials first.

## Editing a test

Edit the step text in the `_test.md` file and run it again with `kane-cli testmd run`. Changed steps (and the steps after them) are re-recorded; unchanged steps replay from `output-<test>/`.
