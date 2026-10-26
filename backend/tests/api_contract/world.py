"""Seed one complete workspace through the API and collect the ids.

Every contract case gets its own world, so DELETE cases can't leak into
other cases. Steps run in order. A step that fails is recorded in
`world["_failed"]` instead of raising, so one broken create endpoint shows
up as a clear failure in `test_world_builds` and doesn't hide every other
case behind a fixture error.
"""
from dataclasses import dataclass
from typing import Any, Callable

from httpx import AsyncClient

MONTH = "2026-04-01"
DAY = "2026-04-28"
# Smallest valid PNG (1x1 transparent pixel): attachment routes sniff the type.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100e5279ed80000000049454e44ae426082"
)


@dataclass(frozen=True)
class Step:
    key: str
    method: str
    path: str
    body: Callable[[dict], Any] | None = None
    files: Callable[[dict], Any] | None = None
    id_field: str = "id"


def _tx(account_key: str = "account_id", **extra) -> Callable[[dict], dict]:
    def build(w: dict) -> dict:
        return {
            "account_id": w[account_key],
            "description": "Groceries",
            "amount": 60,
            "date": DAY,
            "type": "debit",
            "currency": "USD",
            **{k: (v(w) if callable(v) else v) for k, v in extra.items()},
        }

    return build


STEPS: list[Step] = [
    Step("account_id", "POST", "/api/accounts",
         lambda w: {"name": "Wallet", "type": "checking", "balance": 0, "currency": "USD"}),
    Step("second_account_id", "POST", "/api/accounts",
         lambda w: {"name": "Savings", "type": "savings", "balance": 0, "currency": "USD"}),
    Step("category_id", "POST", "/api/categories", lambda w: {"name": "Food", "type": "expense"}),
    Step("second_category_id", "POST", "/api/categories",
         lambda w: {"name": "Clothing", "type": "expense"}),
    Step("category_group_id", "POST", "/api/category-groups", lambda w: {"name": "Essentials"}),
    Step("payee_id", "POST", "/api/payees", lambda w: {"name": "Market"}),
    Step("second_payee_id", "POST", "/api/payees", lambda w: {"name": "Corner shop"}),
    Step("transaction_id", "POST", "/api/transactions", _tx(category_id=lambda w: w["category_id"])),
    Step("second_transaction_id", "POST", "/api/transactions", _tx(description="Bakery")),
    Step("savings_transaction_id", "POST", "/api/transactions",
         _tx("second_account_id", description="Transfer in", type="credit")),
    Step("group_id", "POST", "/api/groups",
         lambda w: {"name": "Roommates", "kind": "social", "default_currency": "USD"}),
    Step("member_id", "POST", "/api/groups/{group_id}/members", lambda w: {"name": "Ana"}),
    Step("second_member_id", "POST", "/api/groups/{group_id}/members", lambda w: {"name": "Bruno"}),
    Step("split_transaction_id", "POST", "/api/transactions", _tx(
        description="Dinner",
        splits=lambda w: {"share_type": "equal", "splits": [
            {"group_member_id": w["member_id"]}, {"group_member_id": w["second_member_id"]},
        ]},
    )),
    Step("settlement_id", "POST", "/api/groups/{group_id}/settlements", lambda w: {
        "from_member_id": w["member_id"], "to_member_id": w["second_member_id"],
        "amount": "15.00", "currency": "USD", "date": DAY,
    }),
    Step("budget_id", "POST", "/api/budgets",
         lambda w: {"category_id": w["category_id"], "amount": 500, "month": MONTH}),
    Step("goal_id", "POST", "/api/goals", lambda w: {"name": "Emergency fund", "target_amount": 1000}),
    Step("recurring_id", "POST", "/api/recurring-transactions", lambda w: {
        "description": "Rent", "amount": 900, "type": "debit", "frequency": "monthly",
        "start_date": MONTH, "account_id": w["account_id"], "currency": "USD",
    }),
    Step("rule_id", "POST", "/api/rules", lambda w: {
        "name": "Market rule",
        "conditions": [{"field": "description", "op": "contains", "value": "Market"}],
        "actions": [{"op": "set_category", "value": w["category_id"]}],
        "apply_to_existing": False,
    }),
    Step("asset_group_id", "POST", "/api/asset-groups", lambda w: {"name": "Brokerage"}),
    Step("asset_id", "POST", "/api/assets",
         lambda w: {"name": "House", "type": "real_estate", "currency": "USD"}),
    Step("value_id", "POST", "/api/assets/{asset_id}/values", lambda w: {"amount": 250000, "date": DAY}),
    Step("collection_id", "POST", "/api/collections", lambda w: {"name": "Trip"}),
    Step("product_id", "POST", "/api/products", lambda w: {"name": "Consulting hour"}),
    Step("price_id", "POST", "/api/products/{product_id}/prices",
         lambda w: {"currency": "USD", "unit_price": "100.00"}),
    Step("invoice_id", "POST", "/api/invoices", lambda w: {
        "as_draft": True, "payee_id": w["payee_id"], "currency": "USD",
        "lines": [{"description": "Consulting", "quantity": 2, "unit_price": "100.00"}],
    }),
    Step("issued_invoice_id", "POST", "/api/invoices", lambda w: {
        "payee_id": w["payee_id"], "currency": "USD",
        "lines": [{"description": "Consulting", "quantity": 1, "unit_price": "250.00"}],
    }),
    Step("schedule_id", "POST", "/api/invoice-schedules", lambda w: {
        "name": "Monthly retainer", "frequency": "monthly",
        "lines": [{"description": "Retainer", "quantity": 1, "unit_price": "100.00"}],
    }),
    Step("attachment_id", "POST", "/api/transactions/{transaction_id}/attachments",
         files=lambda w: {"file": ("receipt.png", PNG_BYTES, "image/png")}),
    Step("agents_connection_id", "POST", "/api/agents/connections",
         lambda w: {"name": "Local LLM", "kind": "ollama", "base_url": "http://ollama:11434"}),
]


async def _lookup(client: AsyncClient, headers: dict, world: dict) -> None:
    me = await client.get("/api/users/me", headers=headers)
    if me.status_code == 200:
        world["user_id"] = world["id"] = me.json()["id"]
    ws = await client.get("/api/workspaces/current", headers=headers)
    if ws.status_code == 200:
        body = ws.json()
        world["workspace_id"] = body.get("id") or body.get("workspace", {}).get("id")


async def build_world(client: AsyncClient, headers: dict, fixtures: dict) -> dict:
    world: dict = {"_failed": {}, **fixtures}
    await _lookup(client, headers, world)
    for step in STEPS:
        try:
            path = step.path.format(**world)
        except KeyError as missing:
            world["_failed"][step.key] = f"missing {missing} for {step.path}"
            continue
        kwargs: dict = {"headers": headers}
        try:
            if step.files:
                kwargs["files"] = step.files(world)
            elif step.body:
                kwargs["json"] = step.body(world)
        except KeyError as missing:
            world["_failed"][step.key] = f"missing {missing} for body"
            continue
        resp = await client.request(step.method, path, **kwargs)
        if resp.status_code >= 300:
            world["_failed"][step.key] = f"{resp.status_code}: {resp.text[:300]}"
            continue
        world[step.key] = resp.json().get(step.id_field)
    return world
