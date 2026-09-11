#!/usr/bin/env python3
"""Seed rich demo data for Alex Rivera via authenticated HTTP API."""
from __future__ import annotations

import json
import random
import sys
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import urllib.error
import urllib.parse
import urllib.request

BASE = "http://localhost:8000"
EMAIL = "alex.rivera.demo@example.com"
PASSWORD = "DemoFinance2026!"
TODAY = date(2026, 9, 11)  # match box clock / user_info
RNG = random.Random(20260911)


class Api:
    def __init__(self) -> None:
        self.token: str | None = None

    def login(self) -> None:
        body = urllib.parse.urlencode(
            {"username": EMAIL, "password": PASSWORD}
        ).encode()
        req = urllib.request.Request(
            f"{BASE}/api/auth/login",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        self.token = data["access_token"]
        print(f"logged in as {EMAIL}")

    def request(
        self,
        method: str,
        path: str,
        payload: Any | None = None,
        raw: bool = False,
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
                if raw:
                    return body
                if not body or resp.status == 204:
                    return None
                return json.loads(body)
        except urllib.error.HTTPError as e:
            err = e.read().decode(errors="replace")
            raise RuntimeError(f"{method} {path} -> {e.code}: {err[:800]}") from e

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, payload: Any | None = None, **kw: Any) -> Any:
        return self.request("POST", path, payload=payload, **kw)

    def patch(self, path: str, payload: Any) -> Any:
        return self.request("PATCH", path, payload=payload)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)


def money(x: float | str | Decimal) -> str:
    return f"{Decimal(str(x)):.2f}"


def main() -> int:
    api = Api()
    api.login()

    counts: dict[str, int] = {}
    notes: list[str] = []

    # --- categories / groups (defaults already seeded) ---
    cats = {c["name"]: c for c in api.get("/api/categories")}
    groups = {g["name"]: g for g in api.get("/api/category-groups")}
    print(f"categories={len(cats)} groups={len(groups)}")

    # Ensure a couple of extra user categories if useful
    income_group = groups.get("Income") or groups.get("Salary & Income")
    # Find group for Salary & Income category
    salary_cat = cats["Salary & Income"]
    grocery_cat = cats["Groceries"]
    dining_cat = cats["Food & Dining"]
    housing_cat = cats["Housing"]
    transport_cat = cats["Transport"]
    subs_cat = cats["Subscriptions"]
    shopping_cat = cats["Shopping"]
    health_cat = cats["Health"]
    leisure_cat = cats["Leisure"]
    transfers_cat = cats["Transfers"]
    utilities_like = cats.get("Taxes & Fees")  # fallback; may add Utilities

    # Add Utilities category under Housing if missing
    if "Utilities" not in cats:
        g_id = housing_cat.get("group_id")
        created = api.post(
            "/api/categories",
            {
                "name": "Utilities",
                "icon": "zap",
                "color": "#F97316",
                "group_id": g_id,
            },
        )
        cats["Utilities"] = created
        counts["categories_added"] = counts.get("categories_added", 0) + 1
    utilities_cat = cats["Utilities"]

    if "Coffee" not in cats:
        created = api.post(
            "/api/categories",
            {
                "name": "Coffee",
                "icon": "coffee",
                "color": "#92400E",
                "group_id": dining_cat.get("group_id"),
            },
        )
        cats["Coffee"] = created
        counts["categories_added"] = counts.get("categories_added", 0) + 1
    coffee_cat = cats["Coffee"]

    # Optional extra group for demo
    if "Lifestyle" not in groups:
        try:
            g = api.post(
                "/api/category-groups",
                {"name": "Lifestyle", "icon": "sparkles", "color": "#A855F7", "position": 10},
            )
            groups["Lifestyle"] = g
            counts["category_groups_added"] = 1
        except RuntimeError as e:
            notes.append(f"category group Lifestyle skipped: {e}")

    # --- accounts ---
    accounts = api.get("/api/accounts")
    by_name = {a["name"]: a for a in accounts}

    # Repurpose default Wallet (type checking) -> rename to Everyday Checking later; create proper set
    desired = [
        {
            "name": "Everyday Checking",
            "type": "checking",
            "balance": "4250.00",
            "currency": "USD",
            "balance_date": (TODAY - timedelta(days=90)).isoformat(),
        },
        {
            "name": "High-Yield Savings",
            "type": "savings",
            "balance": "12500.00",
            "currency": "USD",
            "balance_date": (TODAY - timedelta(days=90)).isoformat(),
        },
        {
            "name": "Visa Sapphire",
            "type": "credit_card",
            "balance": "840.00",  # amount owed
            "currency": "USD",
            "credit_limit": "8000.00",
            "statement_close_day": 22,
            "payment_due_day": 12,
            "minimum_payment": "35.00",
            "card_brand": "visa",
            "card_level": "sapphire",
            "balance_date": (TODAY - timedelta(days=90)).isoformat(),
        },
        {
            "name": "Cash Wallet",
            "type": "wallet",
            "balance": "185.00",
            "currency": "USD",
            "balance_date": (TODAY - timedelta(days=90)).isoformat(),
        },
    ]

    acct: dict[str, Any] = {}
    for spec in desired:
        if spec["name"] in by_name:
            acct[spec["name"]] = by_name[spec["name"]]
            continue
        # Reuse default "Wallet" checking as Everyday Checking if present
        if spec["name"] == "Everyday Checking" and "Wallet" in by_name:
            patched = api.patch(
                f"/api/accounts/{by_name['Wallet']['id']}",
                {
                    "name": "Everyday Checking",
                    "type": "checking",
                    "balance": spec["balance"],
                    "balance_date": spec["balance_date"],
                },
            )
            acct[spec["name"]] = patched
            counts["accounts_updated"] = counts.get("accounts_updated", 0) + 1
            continue
        created = api.post("/api/accounts", spec)
        acct[spec["name"]] = created
        counts["accounts_created"] = counts.get("accounts_created", 0) + 1

    checking = acct["Everyday Checking"]
    savings = acct["High-Yield Savings"]
    card = acct["Visa Sapphire"]
    cash = acct["Cash Wallet"]
    print("accounts:", {k: v["id"] for k, v in acct.items()})

    # --- payees ---
    existing_payees = {p["name"]: p for p in api.get("/api/payees")}
    payee_specs = [
        ("Whole Foods", "company", "https://www.wholefoodsmarket.com"),
        ("Trader Joe's", "company", None),
        ("Oak Street Apartments", "company", None),
        ("Acme Corp Payroll", "company", "https://example.com"),
        ("PG&E Utilities", "company", None),
        ("Netflix", "company", "https://www.netflix.com"),
        ("Spotify", "company", "https://www.spotify.com"),
        ("Shell Gas", "company", None),
        ("Uber", "company", None),
        ("Blue Bottle Coffee", "company", None),
        ("Amazon", "company", None),
        ("Target", "company", None),
        ("CVS Pharmacy", "company", None),
        ("Chipotle", "company", None),
        ("City Transit", "company", None),
        ("Planet Fitness", "company", None),
        ("AT&T Wireless", "company", None),
        ("State Farm Insurance", "company", None),
    ]
    payees: dict[str, Any] = {}
    for name, typ, website in payee_specs:
        if name in existing_payees:
            payees[name] = existing_payees[name]
            continue
        payload: dict[str, Any] = {"name": name, "type": typ}
        if website:
            payload["website"] = website
        payees[name] = api.post("/api/payees", payload)
        counts["payees"] = counts.get("payees", 0) + 1

    # --- transactions spanning ~75 days ---
    # Opening balances already create opening txs when accounts created; add living expenses
    tx_count = 0
    transfer_count = 0

    def add_tx(
        account_id: str,
        description: str,
        amount: float,
        d: date,
        typ: str,
        category_id: str | None = None,
        payee_id: str | None = None,
        notes: str | None = None,
    ) -> Any:
        nonlocal tx_count
        payload: dict[str, Any] = {
            "account_id": account_id,
            "description": description,
            "amount": money(amount),
            "date": d.isoformat(),
            "type": typ,
            "currency": "USD",
        }
        if category_id:
            payload["category_id"] = category_id
        if payee_id:
            payload["payee_id"] = payee_id
        if notes:
            payload["notes"] = notes
        res = api.post("/api/transactions", payload)
        tx_count += 1
        return res

    start = TODAY - timedelta(days=75)

    # Monthly salary on 1st and mid-month bonus-ish / biweekly: every other Friday-ish
    # Use 1st and 15th-ish
    for month_offset in range(0, 3):
        # Approximate months back
        for day in (1, 15):
            # walk back month_offset months from Sept
            y, m = 2026, 9 - month_offset
            while m <= 0:
                m += 12
                y -= 1
            try:
                d = date(y, m, day)
            except ValueError:
                continue
            if d < start or d > TODAY:
                continue
            add_tx(
                checking["id"],
                "ACME CORP PAYROLL",
                4250.00 if day == 1 else 4250.00,
                d,
                "credit",
                salary_cat["id"],
                payees["Acme Corp Payroll"]["id"],
                notes="Biweekly salary",
            )

    # Rent on the 1st
    for month_offset in range(0, 3):
        y, m = 2026, 9 - month_offset
        while m <= 0:
            m += 12
            y -= 1
        d = date(y, m, 1)
        if d < start or d > TODAY:
            continue
        add_tx(
            checking["id"],
            "Oak Street Apartments — Rent",
            1850.00,
            d,
            "debit",
            housing_cat["id"],
            payees["Oak Street Apartments"]["id"],
        )

    # Utilities ~5th
    for month_offset in range(0, 3):
        y, m = 2026, 9 - month_offset
        while m <= 0:
            m += 12
            y -= 1
        d = date(y, m, 5)
        if d < start or d > TODAY:
            continue
        add_tx(
            checking["id"],
            "PG&E Electric & Gas",
            RNG.uniform(95, 145),
            d,
            "debit",
            utilities_cat["id"],
            payees["PG&E Utilities"]["id"],
        )
        add_tx(
            checking["id"],
            "AT&T Wireless",
            78.00,
            d + timedelta(days=2),
            "debit",
            utilities_cat["id"],
            payees["AT&T Wireless"]["id"],
        )

    # Subscriptions
    for month_offset in range(0, 3):
        y, m = 2026, 9 - month_offset
        while m <= 0:
            m += 12
            y -= 1
        for day, desc, amt, payee in [
            (8, "NETFLIX.COM", 15.99, "Netflix"),
            (9, "SPOTIFY PREMIUM", 11.99, "Spotify"),
            (12, "PLANET FITNESS", 24.99, "Planet Fitness"),
        ]:
            d = date(y, m, day)
            if d < start or d > TODAY:
                continue
            add_tx(
                card["id"],
                desc,
                amt,
                d,
                "debit",
                subs_cat["id"],
                payees[payee]["id"],
            )

    # Weekly groceries + dining + transport + coffee over the window
    d = start
    while d <= TODAY:
        # groceries ~ weekly on Saturdays
        if d.weekday() == 5:
            store = RNG.choice(["Whole Foods", "Trader Joe's", "Target"])
            add_tx(
                checking["id"] if RNG.random() > 0.35 else card["id"],
                f"{store} groceries",
                RNG.uniform(55, 140),
                d,
                "debit",
                grocery_cat["id"],
                payees[store]["id"],
            )
        # dining 2-3x / week
        if d.weekday() in (1, 3, 5) and RNG.random() > 0.25:
            add_tx(
                card["id"],
                "Chipotle",
                RNG.uniform(12, 22),
                d,
                "debit",
                dining_cat["id"],
                payees["Chipotle"]["id"],
            )
        # coffee
        if d.weekday() < 5 and RNG.random() > 0.4:
            add_tx(
                cash["id"] if RNG.random() > 0.5 else card["id"],
                "Blue Bottle Coffee",
                RNG.uniform(4.5, 7.5),
                d,
                "debit",
                coffee_cat["id"],
                payees["Blue Bottle Coffee"]["id"],
            )
        # transport
        if d.weekday() < 5 and RNG.random() > 0.55:
            if RNG.random() > 0.5:
                add_tx(
                    card["id"],
                    "UBER TRIP",
                    RNG.uniform(9, 28),
                    d,
                    "debit",
                    transport_cat["id"],
                    payees["Uber"]["id"],
                )
            else:
                add_tx(
                    cash["id"],
                    "City Transit pass tap",
                    2.75,
                    d,
                    "debit",
                    transport_cat["id"],
                    payees["City Transit"]["id"],
                )
        # gas every ~10 days
        if (d - start).days % 10 == 0:
            add_tx(
                card["id"],
                "Shell Gas Station",
                RNG.uniform(35, 55),
                d,
                "debit",
                transport_cat["id"],
                payees["Shell Gas"]["id"],
            )
        # amazon shopping occasional
        if d.weekday() == 0 and RNG.random() > 0.5:
            add_tx(
                card["id"],
                "AMAZON.COM*ORDER",
                RNG.uniform(18, 95),
                d,
                "debit",
                shopping_cat["id"],
                payees["Amazon"]["id"],
            )
        # health / pharmacy monthly-ish
        if d.day == 18:
            add_tx(
                card["id"],
                "CVS Pharmacy",
                RNG.uniform(12, 45),
                d,
                "debit",
                health_cat["id"],
                payees["CVS Pharmacy"]["id"],
            )
        # leisure weekend
        if d.weekday() == 6 and RNG.random() > 0.4:
            add_tx(
                card["id"],
                "Weekend entertainment",
                RNG.uniform(25, 80),
                d,
                "debit",
                leisure_cat["id"],
            )
        d += timedelta(days=1)

    # Transfers: paycheck savings and CC payment
    for month_offset in range(0, 3):
        y, m = 2026, 9 - month_offset
        while m <= 0:
            m += 12
            y -= 1
        d_save = date(y, m, 2)
        if start <= d_save <= TODAY:
            api.post(
                "/api/transactions/transfer",
                {
                    "from_account_id": checking["id"],
                    "to_account_id": savings["id"],
                    "amount": money(800),
                    "date": d_save.isoformat(),
                    "description": "Transfer to savings",
                },
            )
            transfer_count += 1
        d_pay = date(y, m, 12)
        if start <= d_pay <= TODAY:
            api.post(
                "/api/transactions/transfer",
                {
                    "from_account_id": checking["id"],
                    "to_account_id": card["id"],
                    "amount": money(500),
                    "date": d_pay.isoformat(),
                    "description": "Credit card payment",
                },
            )
            transfer_count += 1
        # ATM cash
        d_atm = date(y, m, 20)
        if start <= d_atm <= TODAY:
            api.post(
                "/api/transactions/transfer",
                {
                    "from_account_id": checking["id"],
                    "to_account_id": cash["id"],
                    "amount": money(120),
                    "date": d_atm.isoformat(),
                    "description": "ATM withdrawal",
                },
            )
            transfer_count += 1

    counts["transactions"] = tx_count
    counts["transfers"] = transfer_count

    # --- budgets for current month ---
    month_start = date(TODAY.year, TODAY.month, 1)
    budget_specs = [
        (grocery_cat, 450),
        (dining_cat, 250),
        (transport_cat, 200),
        (subs_cat, 60),
        (utilities_cat, 220),
        (shopping_cat, 150),
        (leisure_cat, 120),
        (coffee_cat, 80),
        (housing_cat, 1850),
    ]
    existing_budgets = api.get("/api/budgets", query={"month": month_start.isoformat()})
    # API may ignore query; filter client-side
    if isinstance(existing_budgets, list):
        have = {(b["category_id"], str(b["month"])[:7]) for b in existing_budgets}
    else:
        have = set()
    for cat, amt in budget_specs:
        key = (cat["id"], month_start.isoformat()[:7])
        if key in have:
            continue
        api.post(
            "/api/budgets",
            {
                "category_id": cat["id"],
                "amount": money(amt),
                "month": month_start.isoformat(),
                "is_recurring": True,
            },
        )
        counts["budgets"] = counts.get("budgets", 0) + 1

    # --- goals ---
    existing_goals = {g["name"]: g for g in api.get("/api/goals")}
    goal_specs = [
        {
            "name": "Emergency Fund",
            "target_amount": money(15000),
            "current_amount": money(8500),
            "currency": "USD",
            "target_date": date(2027, 3, 1).isoformat(),
            "tracking_type": "account",
            "account_id": savings["id"],
            "icon": "shield",
            "color": "#10B981",
        },
        {
            "name": "Japan Vacation 2027",
            "target_amount": money(5000),
            "current_amount": money(1200),
            "currency": "USD",
            "target_date": date(2027, 6, 15).isoformat(),
            "tracking_type": "manual",
            "icon": "plane",
            "color": "#3B82F6",
        },
        {
            "name": "New Laptop",
            "target_amount": money(2200),
            "current_amount": money(600),
            "currency": "USD",
            "target_date": date(2026, 12, 1).isoformat(),
            "tracking_type": "manual",
            "icon": "laptop",
            "color": "#8B5CF6",
        },
    ]
    for spec in goal_specs:
        if spec["name"] in existing_goals:
            continue
        api.post("/api/goals", spec)
        counts["goals"] = counts.get("goals", 0) + 1

    # --- assets ---
    existing_assets = {a["name"]: a for a in api.get("/api/assets")}
    asset_specs = [
        {
            "name": "Vanguard Brokerage (VTSAX)",
            "type": "stock",
            "currency": "USD",
            "valuation_method": "manual",
            "units": "42.5",
            "purchase_date": "2024-06-01",
            "purchase_price": money(8500),
            "current_value": money(11250),
        },
        {
            "name": "2019 Honda Civic",
            "type": "vehicle",
            "currency": "USD",
            "valuation_method": "manual",
            "purchase_date": "2019-08-15",
            "purchase_price": money(18500),
            "current_value": money(11200),
        },
        {
            "name": "Home Equity (Condo share)",
            "type": "real_estate",
            "currency": "USD",
            "valuation_method": "manual",
            "purchase_date": "2021-03-01",
            "purchase_price": money(85000),
            "current_value": money(125000),
        },
    ]
    for spec in asset_specs:
        if spec["name"] in existing_assets:
            continue
        try:
            api.post("/api/assets", spec)
            counts["assets"] = counts.get("assets", 0) + 1
        except RuntimeError as e:
            notes.append(f"asset {spec['name']} failed: {e}")

    # --- recurring ---
    existing_rec = api.get("/api/recurring-transactions")
    rec_names = {r["description"] for r in existing_rec}
    rec_specs = [
        {
            "description": "Acme Corp Salary",
            "amount": money(4250),
            "currency": "USD",
            "type": "credit",
            "frequency": "monthly",
            "day_of_month": 1,
            "start_date": (TODAY - timedelta(days=60)).isoformat(),
            "account_id": checking["id"],
            "category_id": salary_cat["id"],
            "skip_first": True,
            "auto_generate": True,
        },
        {
            "description": "Acme Corp Salary (mid)",
            "amount": money(4250),
            "currency": "USD",
            "type": "credit",
            "frequency": "monthly",
            "day_of_month": 15,
            "start_date": (TODAY - timedelta(days=60)).isoformat(),
            "account_id": checking["id"],
            "category_id": salary_cat["id"],
            "skip_first": True,
            "auto_generate": True,
        },
        {
            "description": "Rent — Oak Street",
            "amount": money(1850),
            "currency": "USD",
            "type": "debit",
            "frequency": "monthly",
            "day_of_month": 1,
            "start_date": (TODAY - timedelta(days=60)).isoformat(),
            "account_id": checking["id"],
            "category_id": housing_cat["id"],
            "skip_first": True,
            "auto_generate": True,
        },
        {
            "description": "Netflix",
            "amount": money(15.99),
            "currency": "USD",
            "type": "debit",
            "frequency": "monthly",
            "day_of_month": 8,
            "start_date": (TODAY - timedelta(days=60)).isoformat(),
            "account_id": card["id"],
            "category_id": subs_cat["id"],
            "skip_first": True,
            "auto_generate": True,
        },
        {
            "description": "Spotify",
            "amount": money(11.99),
            "currency": "USD",
            "type": "debit",
            "frequency": "monthly",
            "day_of_month": 9,
            "start_date": (TODAY - timedelta(days=60)).isoformat(),
            "account_id": card["id"],
            "category_id": subs_cat["id"],
            "skip_first": True,
            "auto_generate": True,
        },
    ]
    for spec in rec_specs:
        if spec["description"] in rec_names:
            continue
        api.post("/api/recurring-transactions", spec)
        counts["recurring"] = counts.get("recurring", 0) + 1

    # --- extra categorization rules ---
    existing_rules = {r["name"] for r in api.get("/api/rules")}
    rule_specs = [
        {
            "name": "Whole Foods → Groceries",
            "conditions_op": "or",
            "conditions": [
                {"field": "description", "op": "contains", "value": "Whole Foods"},
                {"field": "description", "op": "contains", "value": "WHOLEFDS"},
            ],
            "actions": [{"op": "set_category", "value": grocery_cat["id"]}],
            "priority": 10,
            "is_active": True,
            "apply_to_existing": True,
            "overwrite_existing_categories": False,
        },
        {
            "name": "PG&E → Utilities",
            "conditions_op": "or",
            "conditions": [
                {"field": "description", "op": "contains", "value": "PG&E"},
                {"field": "description", "op": "contains", "value": "PGE"},
            ],
            "actions": [{"op": "set_category", "value": utilities_cat["id"]}],
            "priority": 10,
            "is_active": True,
            "apply_to_existing": True,
            "overwrite_existing_categories": False,
        },
        {
            "name": "Chipotle → Dining",
            "conditions_op": "and",
            "conditions": [
                {"field": "description", "op": "contains", "value": "Chipotle"},
            ],
            "actions": [{"op": "set_category", "value": dining_cat["id"]}],
            "priority": 5,
            "is_active": True,
            "apply_to_existing": True,
            "overwrite_existing_categories": False,
        },
    ]
    for spec in rule_specs:
        if spec["name"] in existing_rules:
            continue
        api.post("/api/rules", spec)
        counts["rules_added"] = counts.get("rules_added", 0) + 1

    # --- exercise reports / search / dashboard / export ---
    exercised: dict[str, Any] = {}
    try:
        summary = api.get("/api/dashboard/summary")
        exercised["dashboard_summary_balance_primary"] = summary.get("total_balance_primary")
        exercised["dashboard_monthly_income"] = summary.get("monthly_income")
        exercised["dashboard_monthly_expenses"] = summary.get("monthly_expenses")
    except RuntimeError as e:
        notes.append(f"dashboard summary: {e}")

    for path, q in [
        ("/api/dashboard/spending-by-category", None),
        ("/api/dashboard/monthly-trend", None),
        ("/api/reports/net-worth", {"months": 6}),
        ("/api/reports/income-expenses", {"months": 3}),
        ("/api/reports/cash-flow", {"months": 3}),
        ("/api/search", {"q": "Netflix"}),
        ("/api/search", {"q": "Whole Foods"}),
    ]:
        try:
            res = api.get(path, query=q)
            if isinstance(res, list):
                exercised[path] = f"list len={len(res)}"
            elif isinstance(res, dict):
                exercised[path] = f"keys={list(res.keys())[:8]}"
            else:
                exercised[path] = type(res).__name__
        except RuntimeError as e:
            notes.append(f"{path}: {e}")

    # Export backup
    try:
        backup = api.get("/api/export/backup", raw=True)
        exercised["export_backup_bytes"] = len(backup)
        with open("/workspace/secureFinanceApp/scripts/alex_demo_backup.json", "wb") as f:
            f.write(backup)
    except RuntimeError as e:
        notes.append(f"export backup: {e}")

    # Transactions export CSV if available
    try:
        csv_bytes = api.get("/api/transactions/export", raw=True)
        exercised["transactions_export_bytes"] = len(csv_bytes)
        with open("/workspace/secureFinanceApp/scripts/alex_transactions_export.csv", "wb") as f:
            f.write(csv_bytes)
    except RuntimeError as e:
        notes.append(f"transactions export: {e}")

    # Light import preview with a tiny OFX-like or CSV if supported — check import endpoint
    # Prefer CSV import of a couple synthetic rows if the API accepts it; otherwise note UI-only.
    try:
        # Many forks accept multipart; without multipart helper, skip and note.
        notes.append(
            "CSV/OFX import via multipart not exercised in this script (UI leftover / use /transactions import page)."
        )
    except Exception as e:
        notes.append(str(e))

    # Mark onboarding complete if preferences endpoint exists
    try:
        me = api.get("/api/users/me")
        prefs = me.get("preferences") or {}
        prefs["onboarding_completed"] = True
        prefs["display_name"] = "Alex Rivera"
        prefs["currency_display"] = "USD"
        # fastapi-users often PATCH /api/users/me
        api.patch("/api/users/me", {"preferences": prefs})
        exercised["onboarding_completed"] = True
    except RuntimeError as e:
        notes.append(f"preferences patch: {e}")

    # Final counts from API
    final = {
        "accounts": len(api.get("/api/accounts")),
        "payees": len(api.get("/api/payees")),
        "categories": len(api.get("/api/categories")),
        "category_groups": len(api.get("/api/category-groups")),
        "transactions_total": api.get("/api/transactions", query={"limit": 1}).get("total"),
        "budgets": len(api.get("/api/budgets")),
        "goals": len(api.get("/api/goals")),
        "assets": len(api.get("/api/assets")),
        "recurring": len(api.get("/api/recurring-transactions")),
        "rules": len(api.get("/api/rules")),
    }

    result = {
        "created_this_run": counts,
        "final_totals": final,
        "exercised": exercised,
        "notes": notes,
        "login": {"email": EMAIL, "password": PASSWORD, "auth": "POST /api/auth/login form username+password -> Bearer JWT"},
    }
    out_path = "/workspace/secureFinanceApp/scripts/seed_demo_result.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print("SEED FAILED:", e, file=sys.stderr)
        raise
