#!/usr/bin/env python3
"""Seed combined loan scenario exercise + committed recurring prepay for Alex."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

BASE = "http://localhost:8000"
EMAIL = "alex.rivera.demo@example.com"
PASSWORD = "DemoFinance2026!"
TODAY = date(2026, 9, 11)
MORTGAGE_ID = "2830dab0-0b4a-4967-8a34-56fb4ba1f8ea"
OUT = Path(__file__).resolve().parent / "seed_loan_combined_result.json"


class Api:
    def __init__(self) -> None:
        self.token: str | None = None

    def login(self) -> None:
        body = urllib.parse.urlencode({"username": EMAIL, "password": PASSWORD}).encode()
        for i in range(8):
            req = urllib.request.Request(
                f"{BASE}/api/auth/login",
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req) as resp:
                    self.token = json.loads(resp.read())["access_token"]
                    print("logged in")
                    return
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(15 + i * 8)
                    continue
                raise
        raise RuntimeError("login failed")

    def request(self, method: str, path: str, payload: Any | None = None, query: dict | None = None) -> Any:
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
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()[:800]}") from e

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, payload: Any | None = None, **kw: Any) -> Any:
        return self.request("POST", path, payload=payload, **kw)


def main() -> int:
    api = Api()
    api.login()

    accounts = api.get("/api/accounts")
    checking = next(a for a in accounts if a["name"] == "Everyday Checking")
    cats = {c["name"]: c for c in api.get("/api/categories")}
    housing = cats["Housing"]

    # Combined simulation on mortgage
    sim = api.post(
        "/api/v1/loans/simulations/combined",
        {
            "account_id": MORTGAGE_ID,
            "strategy": "reduce_tenure",
            "as_of_date": TODAY.isoformat(),
            "events": [
                {
                    "type": "one_time_prepayment",
                    "date": "2026-12-15",
                    "amount": "10000.00",
                    "label": "Year-end bonus",
                },
                {
                    "type": "recurring_prepayment",
                    "date": "2026-10-01",
                    "amount": "200.00",
                    "months": 48,
                    "label": "+$200/mo extra",
                },
                {
                    "type": "rate_change",
                    "date": "2027-03-01",
                    "new_rate": "5.875",
                    "label": "Refi-ish cut",
                },
            ],
        },
    )
    print(
        "combined sim KPIs:",
        {k: sim["kpis"][k] for k in ["interest_saved", "months_saved", "payoff_date", "emi"]},
    )

    # Idempotent: skip if active recurring commitment already exists for mortgage
    existing = api.get("/api/v1/loans/commitments", query={"account_id": MORTGAGE_ID})
    active = [c for c in existing if c.get("status") == "active" and c.get("kind") == "recurring_prepayment"]
    if active:
        commitment = active[0]
        print("reusing commitment", commitment["id"])
    else:
        commitment = api.post(
            "/api/v1/loans/commitments",
            {
                "account_id": MORTGAGE_ID,
                "kind": "recurring_prepayment",
                "amount": "200.00",
                "start_date": "2026-10-01",
                "day_of_month": 1,
                "funding_account_id": checking["id"],
                "category_id": housing["id"],
                "notes": "Demo: +$200/mo mortgage principal — combined plan commit",
            },
        )
        print("created commitment", commitment["id"], "budget", commitment.get("budget_id"))

    # Verify budget + projected path surfaces
    budgets = api.get("/api/budgets", query={"month": "2026-09-01"})
    # also try without month
    try:
        budgets_all = api.get("/api/budgets")
    except Exception:
        budgets_all = budgets

    recurring = api.get("/api/recurring-transactions")
    matched_rec = [
        r
        for r in recurring
        if commitment.get("recurring_transaction_id")
        and r["id"] == commitment["recurring_transaction_id"]
    ]

    projected = None
    try:
        projected = api.get("/api/dashboard/projected-transactions", query={"month": "2026-10-01"})
    except Exception as e:
        projected = {"error": str(e)[:200]}

    dash = api.get("/api/dashboard/summary")

    result = {
        "mortgage_id": MORTGAGE_ID,
        "combined_kpis": sim["kpis"],
        "timeline_rows": len(sim.get("timeline", [])),
        "commitment": {
            "id": commitment["id"],
            "kind": commitment["kind"],
            "amount": commitment["amount"],
            "start_date": commitment["start_date"],
            "budget_id": commitment.get("budget_id"),
            "recurring_transaction_id": commitment.get("recurring_transaction_id"),
            "status": commitment.get("status"),
        },
        "recurring_match": matched_rec[0] if matched_rec else None,
        "budgets_sample_count": len(budgets_all) if isinstance(budgets_all, list) else budgets_all,
        "projected_october": (
            {
                "count": len(projected),
                "extra_principal_hits": [
                    p
                    for p in projected
                    if isinstance(p, dict)
                    and "extra principal" in (p.get("description") or "").lower()
                ][:3],
            }
            if isinstance(projected, list)
            else projected
        ),
        "dashboard_projected_expenses": dash.get("projected_expenses") or dash.get("projected_monthly_expenses"),
        "ui_path": f"http://localhost:3000/loans/{MORTGAGE_ID} → Combined plan tab",
    }
    OUT.write_text(json.dumps(result, indent=2, default=str))
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
