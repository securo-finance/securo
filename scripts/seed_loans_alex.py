#!/usr/bin/env python3
"""Seed realistic loan accounts + lifecycle events for Alex Rivera demo."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

BASE = "http://localhost:8000"
EMAIL = "alex.rivera.demo@example.com"
PASSWORD = "DemoFinance2026!"
TODAY = date(2026, 9, 11)
OUT = Path(__file__).resolve().parent / "seed_loans_result.json"


class Api:
    def __init__(self) -> None:
        self.token: str | None = None

    def login(self, retries: int = 8) -> None:
        body = urllib.parse.urlencode(
            {"username": EMAIL, "password": PASSWORD}
        ).encode()
        last_err: Exception | None = None
        for i in range(retries):
            req = urllib.request.Request(
                f"{BASE}/api/auth/login",
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req) as resp:
                    data = json.loads(resp.read())
                self.token = data["access_token"]
                print(f"logged in as {EMAIL}")
                return
            except urllib.error.HTTPError as e:
                err = e.read().decode(errors="replace")
                last_err = RuntimeError(f"login {e.code}: {err[:200]}")
                if e.code == 429:
                    wait = 15 + i * 10
                    print(f"rate limited, sleep {wait}s...")
                    time.sleep(wait)
                    continue
                raise last_err from e
        raise last_err or RuntimeError("login failed")

    def request(
        self,
        method: str,
        path: str,
        payload: Any | None = None,
        query: dict | None = None,
    ) -> Any:
        url = f"{BASE}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        data = None
        headers = {"Authorization": f"Bearer {self.token}"}
        if payload is not None:
            data = json.dumps(payload, default=str).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                body = resp.read()
                if not body or resp.status == 204:
                    return None
                return json.loads(body)
        except urllib.error.HTTPError as e:
            err = e.read().decode(errors="replace")
            raise RuntimeError(f"{method} {path} -> {e.code}: {err[:1200]}") from e

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, payload: Any | None = None, **kw: Any) -> Any:
        return self.request("POST", path, payload=payload, **kw)

    def patch(self, path: str, payload: Any) -> Any:
        return self.request("PATCH", path, payload=payload)

    def put(self, path: str, payload: Any) -> Any:
        return self.request("PUT", path, payload=payload)


def money(x: Decimal | float | str) -> str:
    return f"{Decimal(str(x)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"


def find_or_none(accounts: list[dict], name: str) -> dict | None:
    for a in accounts:
        if a.get("name") == name:
            return a
    return None


def ensure_schedule(api: Api, account_id: str) -> list[dict]:
    schedule = api.get(f"/api/v1/loans/{account_id}/schedule")
    if schedule:
        return schedule
    print(f"  generating schedule for {account_id}")
    return api.post(f"/api/v1/loans/{account_id}/schedule/generate", {"version": 1})


def mark_paid_up_to(api: Api, schedule: list[dict], through: date) -> list[dict]:
    paid: list[dict] = []
    for entry in schedule:
        due = date.fromisoformat(entry["due_date"])
        if due > through:
            break
        if entry.get("payment_status") == "paid":
            paid.append(entry)
            continue
        updated = api.put(
            f"/api/v1/loans/schedule/{entry['id']}/status",
            {
                "payment_status": "paid",
                "actual_payment_date": entry["due_date"],
                "actual_amount_paid": money(entry["emi_amount"]),
            },
        )
        paid.append(updated)
    return paid


def create_and_link_emi_payments(
    api: Api,
    *,
    checking_id: str,
    housing_cat: str | None,
    transport_cat: str | None,
    loan_name: str,
    paid_entries: list[dict],
    category_hint: str,
) -> list[str]:
    cat = housing_cat if category_hint == "housing" else transport_cat
    tx_ids: list[str] = []
    for entry in paid_entries:
        # Idempotency: skip if already linked
        if entry.get("linked_transaction_id"):
            tx_ids.append(str(entry["linked_transaction_id"]))
            continue
        desc = f"{loan_name} EMI #{entry['emi_number']}"
        tx = api.post(
            "/api/transactions",
            {
                "account_id": checking_id,
                "description": desc,
                "amount": money(entry["emi_amount"]),
                "date": entry["due_date"],
                "type": "debit",
                "currency": "USD",
                "category_id": cat,
                "payee_raw": loan_name,
                "notes": f"Auto-seeded loan EMI payment linked to schedule entry {entry['id']}",
                "status": "posted",
            },
        )
        linked = api.post(
            f"/api/v1/loans/schedule/{entry['id']}/link",
            {"transaction_id": tx["id"]},
        )
        tx_ids.append(tx["id"])
        assert linked.get("linked_transaction_id") == tx["id"] or linked.get(
            "payment_status"
        ) == "paid"
    return tx_ids


def sync_outstanding(api: Api, account_id: str, schedule: list[dict]) -> dict:
    """Set account.balance to remaining principal after paid EMIs."""
    paid = [e for e in schedule if e.get("payment_status") == "paid"]
    scheduled = [e for e in schedule if e.get("payment_status") == "scheduled"]
    if paid:
        outstanding = paid[-1]["closing_balance"]
    elif scheduled:
        outstanding = scheduled[0]["opening_balance"]
    else:
        outstanding = 0
    return api.patch(
        f"/api/accounts/{account_id}",
        {"balance": money(outstanding)},
    )


def main() -> int:
    api = Api()
    api.login()

    accounts = api.get("/api/accounts")
    by_name = {a["name"]: a for a in accounts}
    checking = by_name.get("Everyday Checking")
    if not checking:
        raise SystemExit("Everyday Checking not found — run seed_demo_alex.py first")

    cats = {c["name"]: c for c in api.get("/api/categories")}
    housing = cats.get("Housing", {}).get("id")
    transport = cats.get("Transport", {}).get("id")

    results: dict[str, Any] = {"loans": {}, "exercised": {}, "notes": []}

    # --- Loan specs ---
    # Mortgage: originated ~28 months ago so ~28 EMIs paid by TODAY
    mortgage_disbursed = date(2024, 5, 1)
    auto_disbursed = date(2025, 3, 15)
    personal_disbursed = date(2026, 1, 10)

    loan_specs = [
        {
            "key": "mortgage",
            "name": "Home Mortgage — First National",
            "loan_kind": "home",
            "original_principal": "420000.00",
            "balance": "420000.00",
            "interest_rate": "6.375",
            "tenure_months": 360,
            "emi_day": 1,
            "disbursed_on": mortgage_disbursed.isoformat(),
            "category_hint": "housing",
            "prepay": True,
            "rate_change": False,
            "preclose": False,
        },
        {
            "key": "auto",
            "name": "Auto Loan — Honda Civic",
            "loan_kind": "auto",
            "original_principal": "28500.00",
            "balance": "28500.00",
            "interest_rate": "5.90",
            "tenure_months": 60,
            "emi_day": 15,
            "disbursed_on": auto_disbursed.isoformat(),
            "category_hint": "transport",
            "prepay": False,
            "rate_change": True,
            "preclose": False,
        },
        {
            "key": "personal",
            "name": "Personal Loan — QuickCash Demo",
            "loan_kind": "personal",
            "original_principal": "4500.00",
            "balance": "4500.00",
            "interest_rate": "11.50",
            "tenure_months": 24,
            "emi_day": 10,
            "disbursed_on": personal_disbursed.isoformat(),
            "category_hint": "housing",
            "prepay": False,
            "rate_change": False,
            "preclose": True,
        },
    ]

    for spec in loan_specs:
        existing = find_or_none(accounts, spec["name"])
        if existing:
            loan = existing
            print(f"reusing loan {spec['name']} id={loan['id']}")
            # Ensure loan fields present
            if not loan.get("original_principal"):
                loan = api.patch(
                    f"/api/accounts/{loan['id']}",
                    {
                        "loan_kind": spec["loan_kind"],
                        "original_principal": spec["original_principal"],
                        "interest_rate": spec["interest_rate"],
                        "tenure_months": spec["tenure_months"],
                        "emi_day": spec["emi_day"],
                        "disbursed_on": spec["disbursed_on"],
                        "balance": spec["balance"],
                    },
                )
        else:
            payload = {
                "name": spec["name"],
                "type": "loan",
                "balance": spec["balance"],
                "currency": "USD",
                "loan_kind": spec["loan_kind"],
                "original_principal": spec["original_principal"],
                "interest_rate": spec["interest_rate"],
                "tenure_months": spec["tenure_months"],
                "emi_day": spec["emi_day"],
                "disbursed_on": spec["disbursed_on"],
                "balance_date": spec["disbursed_on"],
            }
            print(f"creating {spec['name']}...")
            loan = api.post("/api/accounts", payload)
            print(f"  created id={loan['id']} emi={loan.get('emi_amount')}")

        schedule = ensure_schedule(api, loan["id"])
        print(f"  schedule rows={len(schedule)} emi={schedule[0]['emi_amount'] if schedule else None}")
        if schedule and not loan.get("emi_amount"):
            loan = api.patch(
                f"/api/accounts/{loan['id']}",
                {"emi_amount": money(schedule[0]["emi_amount"])},
            )
            print(f"  patched emi_amount={loan.get('emi_amount')}")

        # Mark EMIs due on/before yesterday as paid (leave current month scheduled)
        paid_through = TODAY - timedelta(days=1)
        paid = mark_paid_up_to(api, schedule, paid_through)
        print(f"  marked paid={len(paid)}")

        # Refresh schedule after status updates
        schedule = api.get(f"/api/v1/loans/{loan['id']}/schedule")
        loan = sync_outstanding(api, loan["id"], schedule)
        print(f"  outstanding balance={loan.get('balance')} current={loan.get('current_balance')}")

        # Link checking payments for a sample of recent paid EMIs (last 6)
        sample = [e for e in schedule if e.get("payment_status") == "paid"][-6:]
        tx_ids = create_and_link_emi_payments(
            api,
            checking_id=checking["id"],
            housing_cat=housing,
            transport_cat=transport,
            loan_name=spec["name"],
            paid_entries=sample,
            category_hint=spec["category_hint"],
        )
        print(f"  linked EMI txs={len(tx_ids)}")

        overview = api.get(f"/api/v1/loans/{loan['id']}/overview")
        results["loans"][spec["key"]] = {
            "id": loan["id"],
            "name": spec["name"],
            "loan_kind": spec["loan_kind"],
            "original_principal": spec["original_principal"],
            "interest_rate": spec.get("interest_rate") or loan.get("interest_rate"),
            "tenure_months": spec["tenure_months"],
            "emi_amount": loan.get("emi_amount") or (schedule[0]["emi_amount"] if schedule else None),
            "emi_day": spec["emi_day"],
            "disbursed_on": spec["disbursed_on"],
            "balance": loan.get("balance"),
            "schedule_rows": len(schedule),
            "emis_paid": overview.get("emis_paid"),
            "emis_remaining": overview.get("emis_remaining"),
            "linked_tx_sample": tx_ids,
            "overview": overview,
        }

        # Prepayment on mortgage
        if spec["prepay"] and not loan.get("is_closed"):
            prepay_date = (TODAY - timedelta(days=3)).isoformat()  # after last paid EMI
            prepay_amt = "15000.00"
            sim = api.post(
                "/api/v1/loans/prepayments/simulate",
                {
                    "account_id": loan["id"],
                    "prepayment_amount": prepay_amt,
                    "prepayment_date": prepay_date,
                },
            )
            # funding tx from checking
            prepay_tx = api.post(
                "/api/transactions",
                {
                    "account_id": checking["id"],
                    "description": f"{spec['name']} principal prepayment",
                    "amount": prepay_amt,
                    "date": prepay_date,
                    "type": "debit",
                    "currency": "USD",
                    "category_id": housing,
                    "payee_raw": spec["name"],
                    "notes": "Demo principal prepayment (reduce_tenure)",
                    "status": "posted",
                },
            )
            recorded = api.post(
                "/api/v1/loans/prepayments",
                {
                    "account_id": loan["id"],
                    "prepayment_amount": prepay_amt,
                    "prepayment_date": prepay_date,
                    "recalculation_method": "reduce_tenure",
                    "transaction_id": prepay_tx["id"],
                },
            )
            loan = api.get("/api/accounts")
            loan = next(a for a in loan if a["id"] == results["loans"][spec["key"]]["id"])
            results["loans"][spec["key"]]["prepayment"] = {
                "id": recorded.get("id"),
                "amount": prepay_amt,
                "date": prepay_date,
                "method": "reduce_tenure",
                "transaction_id": prepay_tx["id"],
                "simulate_keys": list(sim.keys()) if isinstance(sim, dict) else type(sim).__name__,
                "record": {
                    k: recorded.get(k)
                    for k in [
                        "schedule_version_before",
                        "schedule_version_after",
                        "tenure_change_months",
                        "emi_change_amount",
                    ]
                },
                "balance_after": loan.get("balance"),
            }
            print(f"  prepayment recorded {prepay_amt} -> balance {loan.get('balance')}")

        # Rate change apply on auto loan
        if spec["rate_change"] and not loan.get("is_closed"):
            new_rate = "5.40"
            sim = api.post(
                "/api/v1/loans/simulations/interest-rate-change",
                {
                    "account_id": results["loans"][spec["key"]]["id"],
                    "new_interest_rate": new_rate,
                    "effective_from_date": TODAY.isoformat(),
                },
            )
            applied = api.post(
                "/api/v1/loans/apply/interest-rate-change",
                {
                    "account_id": results["loans"][spec["key"]]["id"],
                    "new_interest_rate": new_rate,
                    "effective_from_date": TODAY.isoformat(),
                    "strategy": "keep_tenure",
                },
            )
            results["loans"][spec["key"]]["rate_change"] = {
                "simulate": sim,
                "applied": applied,
            }
            print(
                f"  rate change applied {sim.get('current_rate')} -> {new_rate}; "
                f"emi {sim.get('current_emi')} -> {sim.get('new_emi')}"
            )

        # Preclosure on tiny personal loan
        if spec["preclose"]:
            lid = results["loans"][spec["key"]]["id"]
            sim = api.post(
                "/api/v1/loans/simulations/preclosure",
                {"account_id": lid, "closure_date": TODAY.isoformat()},
            )
            applied = api.post(
                "/api/v1/loans/apply/preclosure",
                {"account_id": lid, "closure_date": TODAY.isoformat()},
            )
            results["loans"][spec["key"]]["preclosure"] = {
                "simulate": sim,
                "applied": applied,
            }
            print(
                f"  preclosed payoff={sim.get('total_payoff_amount')} "
                f"interest_saved={sim.get('interest_saved')}"
            )

    # Refresh mortgage/auto for sims after mutations
    mortgage_id = results["loans"]["mortgage"]["id"]
    early = api.post(
        "/api/v1/loans/simulations/early-payment",
        {
            "account_id": mortgage_id,
            "prepayment_amount": "10000.00",
            "prepayment_date": TODAY.isoformat(),
        },
    )
    results["exercised"]["early_payment_sim"] = {
        "reduce_emi_new_emi": early.get("reduce_emi", {}).get("new_emi"),
        "reduce_tenure_months_saved": early.get("reduce_tenure", {}).get("months_saved"),
    }

    dash = api.get("/api/v1/loans/dashboard")
    results["exercised"]["dashboard"] = dash
    summary = api.get("/api/v1/loans/summary")
    results["exercised"]["summary"] = summary

    # final schedule spot-check
    mort_sched = api.get(f"/api/v1/loans/{mortgage_id}/schedule")
    results["exercised"]["mortgage_schedule_rows"] = len(mort_sched)
    results["exercised"]["mortgage_paid_rows"] = sum(
        1 for e in mort_sched if e.get("payment_status") == "paid"
    )
    results["exercised"]["mortgage_scheduled_rows"] = sum(
        1 for e in mort_sched if e.get("payment_status") == "scheduled"
    )

    OUT.write_text(json.dumps(results, indent=2, default=str))
    print(f"wrote {OUT}")
    print(
        "dashboard active_loans=",
        dash.get("active_loans_count"),
        "total_outstanding=",
        dash.get("total_outstanding"),
        "total_monthly_emi=",
        dash.get("total_monthly_emi"),
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("FATAL:", exc, file=sys.stderr)
        raise
