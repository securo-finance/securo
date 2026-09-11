# Planet Finance / secureFinanceApp demo session notes

## Login
- URL: http://localhost:3000
- API: http://localhost:8000
- Name: Alex Rivera
- Email: alex.rivera.demo@example.com
- Password: DemoFinance2026!
- Auth: `POST /api/auth/login` with `application/x-www-form-urlencoded` fields `username` + `password` → `{ access_token, token_type: bearer }`
- User is superuser; onboarding_completed set true via PATCH /api/users/me

## Part A fixes
Restored from upstream `securo-finance/securo` (raw/git show):
- `frontend/src/components/category-select.tsx` (was empty → blank dashboard)
- `frontend/src/types/index.ts` (was empty)
- `frontend/src/lib/rule-match-utils.ts` (was stub ~196B)

Frontend container restarted; Vite serves dashboard + category-select cleanly.

Known leftover stub (lazy, module-gated — does not break home shell):
- `frontend/src/pages/invoices.tsx` truncated vs upstream; many `invoice-*.tsx` components missing from fork. Visiting /invoices may fail until those are restored.

## Part B seed totals (after scripts/seed_demo_alex.py)
- accounts: 4 (Everyday Checking, High-Yield Savings, Visa Sapphire, Cash Wallet)
- payees: 18
- categories: 18 (16 system + Utilities + Coffee)
- category_groups: 6 (defaults)
- transactions: 159 (~75 days of activity + opening balances + transfers)
- budgets: 9 (current month, recurring)
- goals: 3 (Emergency Fund linked to savings, Japan Vacation, New Laptop)
- assets: 3 (VTSAX brokerage, Honda Civic, condo equity)
- recurring: 5 (salary x2, rent, Netflix, Spotify)
- rules: 9 (6 default packs + Whole Foods, PG&E, Chipotle)

Exercised via API: dashboard summary/spending/trend, reports net-worth/income-expenses/cash-flow, search, export backup + transactions CSV.
Artifacts: `scripts/alex_demo_backup.json`, `scripts/alex_transactions_export.csv`, `scripts/seed_demo_result.json`

## Skipped / blocked
- Bank sync (Pluggy/SimpleFIN/Enable Banking), OIDC, live AI agents, real email
- Loans — **enabled** (see Part C)
- Multipart CSV/OFX import (use Import page in UI)
- Invoices module full restore (missing components)

## Docker note
Use `sudo docker compose` on this box (user `box` not in docker group).

## Part C — Loan lifecycle (2026-09-11)

Loans router enabled at `/api/v1/loans`. Workspace module `loans` already on.

### Seeded loans (Alex Rivera)
Script: `scripts/seed_loans_alex.py` → `scripts/seed_loans_result.json`

| Loan | ID | Kind | Principal | Rate | Tenure | EMI | Outstanding | Status |
|------|----|------|-----------|------|--------|-----|-------------|--------|
| Home Mortgage — First National | `2830dab0-0b4a-4967-8a34-56fb4ba1f8ea` | home | 420000 | 6.375% | 360 | 2620.25 | 393289.62 | open (28 EMIs paid + $15k reduce_tenure prepay) |
| Auto Loan — Honda Civic | `dbd2a561-96ee-4dfa-b6be-dc475443152a` | auto | 28500 | 5.40% (was 5.90) | 60 | 544.83 | 21257.21 | open (17 EMIs paid; rate-change **applied**) |
| Personal Loan — QuickCash Demo | `0367ced6-3092-4e09-88dd-6fe07991d6f4` | personal | 4500 | 11.50% | 24 | 210.78 | 0 | **closed** via apply/preclosure |

Dashboard (auth): active_loans=2, total_outstanding≈414546.83, total_monthly_emi≈3165.08

### How to try sims (Bearer from login)
```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'username=alex.rivera.demo@example.com&password=DemoFinance2026!' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

# Dashboard
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/loans/dashboard | jq

# Mortgage schedule / overview
MID=2830dab0-0b4a-4967-8a34-56fb4ba1f8ea
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/loans/$MID/schedule | jq 'length'
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/loans/$MID/overview | jq

# Early-payment / preclosure / rate-change sims
AID=dbd2a561-96ee-4dfa-b6be-dc475443152a
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{"account_id":"$MID","prepayment_amount":"10000","prepayment_date":"2026-09-11"}" \
  http://localhost:8000/api/v1/loans/simulations/early-payment | jq
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{"account_id":"$AID","closure_date":"2026-09-11"}" \
  http://localhost:8000/api/v1/loans/simulations/preclosure | jq
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{"account_id":"$AID","new_interest_rate":"5.0","effective_from_date":"2026-09-11"}" \
  http://localhost:8000/api/v1/loans/simulations/interest-rate-change | jq

# Apply endpoints (mutating)
# POST /api/v1/loans/apply/interest-rate-change  {account_id,new_interest_rate,effective_from_date,strategy}
# POST /api/v1/loans/apply/preclosure            {account_id,closure_date}
```

UI: http://localhost:3000/loans and `/loans/:accountId` (module gated). Added missing `frontend/src/components/ui/alert.tsx` so LoanDetailPage / LoanSimulations compile.

Related checking txs: last 6 paid EMIs per loan + mortgage $15k prepayment debited from Everyday Checking and linked where applicable.

### Implemented vs still missing
**Done:** dashboard auth 200; schedules with rows; paid EMIs; prepayment record; rate-change apply; preclosure apply; checking EMI txs; FE alert stub; regenerate carries paid history into new schedule version; utility routes at `/api/v1/loans/calculate-emi` (no double `/loans/loans`).

**Still missing / known gaps:**
- Bulk schedule delete/export ZIP endpoints referenced by older tests (`/schedule/bulk-delete`, `/bulk-export`) not implemented
- Loan `current_balance` from txs often 0/negative vs `balance` outstanding field used by analytics (EMI txs live on checking)
- Dashboard next_due can still duplicate if old schedule versions linger with scheduled rows (mitigated by current_version filter)
- Many `backend/tests/test_loan_*.py` still fail on assertion/shape drift (prepayment body, analytics keys, EMI golden values); after fixture/path fixes: **42 passed, 21 failed, 3 errors** (was ~10/50/50)
- Invoices module still incomplete (unrelated)


## Part D — Combined simulator + commit→budget/forecast (2026-09-11)

### What shipped
- `POST /api/v1/loans/simulations/combined` — stack one-time prepay, recurring prepay, rate changes, EMI holidays on one timeline; returns KPIs vs baseline, timeline, outstanding + cumulative principal/interest curves
- `GET/POST /api/v1/loans/commitments`, `POST .../cancel` — persist `loan_plan_commitments` (alembic **080**); commit creates **Housing recurring budget** + **monthly recurring debit** on Everyday Checking (feeds dashboard projected-transactions / forecast)
- UI: **Combined plan** tab on `/loans/:accountId` (`CombinedLoanSimulator.tsx`) — event editor, KPI strip, sparkline/stacked charts, commit panel
- Tests: `backend/tests/test_loan_combined_simulation.py` — **4 passed**
- Seed: `scripts/seed_loan_combined_plan.py` → `scripts/seed_loan_combined_result.json`

### Demo try-now (Alex)
1. Login → **Loans** → **Home Mortgage — First National** → tab **Combined plan**
2. Click **Run combined sim** (preloaded events) — expect ~$121k interest saved / 54 months saved vs baseline
3. Commit panel already has active **+$200/mo** commitment (`dbb0b383-…`)
4. Budgets: Housing recurring **$200** (`cf3f071a-…`, month 2026-10-01)
5. Forecast: `/api/dashboard/projected-transactions?month=2026-10-01` includes “Home Mortgage — First National extra principal (committed)”

### Combined seed KPIs (mortgage)
- interest_saved ≈ 121693.50 · months_saved = 54 · payoff 2047-05-01 (baseline 2051-11-01)
- commitment recurring_transaction_id=`8fe8a870-096e-4e19-8f6e-5cfcff129015`
