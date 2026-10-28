"""Turn the OpenAPI spec into contract cases.

One operation (METHOD + path template) fans out into variants:

- ``ok``          the owner calls it with world ids and a generated body
- ``viewer``      a viewer-role member calls a mutating route (expect 403)
- ``empty``       a JSON route with required fields gets ``{}`` (expect 422)
- ``unknown_id``  every id in the path is a fresh uuid (expect 404)
- ``anon``        no credentials at all (expect 401 unless public)

Expected statuses are not written here: they live in baseline.json, so a
behaviour change shows up as a diff of that file.
"""
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from app.main import app
from app.services import reconciliation_policy
from tests.api_contract.bodies import object_for, value_for
from tests.api_contract.world import PNG_BYTES

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

VARIANT_OK = "ok"
VARIANT_VIEWER = "viewer"
VARIANT_EMPTY = "empty"
VARIANT_UNKNOWN_ID = "unknown_id"
VARIANT_ANON = "anon"
VARIANTS = (VARIANT_OK, VARIANT_VIEWER, VARIANT_EMPTY, VARIANT_UNKNOWN_ID, VARIANT_ANON)

ADMIN_PREFIXES = ("/api/admin/",)

# Path parameter -> world key, where the names differ.
PARAM_WORLD_KEYS = {
    "tx_id": "transaction_id",
    "conn_id": "agents_connection_id",
    "member_user_id": "user_id",
    "id": "user_id",
}

# Same parameter name, different resource, depending on the route prefix.
PARAM_WORLD_KEYS_BY_PREFIX = {
    "/api/asset-groups/": {"group_id": "asset_group_id"},
    "/api/category-groups/": {"group_id": "category_group_id"},
}

# Path parameters that are not ids.
STATIC_PARAMS = {
    "pack_code": "BR",
    "node": reconciliation_policy.MATCH_RECURRING["node"],
    "provider": "pluggy",
    "key": "registration_enabled",
}

# Parameters that identify a row, so the unknown_id variant swaps them out.
NON_ID_PARAMS = set(STATIC_PARAMS) | {"token"}

_INVOICE_LINES = [{"description": "Consulting", "quantity": 2, "unit_price": "100.00"}]

# Routes whose generated body is not enough to reach the handler's happy
# path. Keyed by (METHOD, path); the callable gets the world. Routes left out
# on purpose answer a domain rule instead (a draft can't be voided, a sole
# owner can't leave): that answer is the contract, and the baseline pins it.
BODY_OVERRIDES: dict[tuple[str, str], Callable[[dict], Any]] = {
    ("POST", "/api/budgets"): lambda w: {
        "category_id": w["second_category_id"], "amount": 300, "month": "2026-05-01",
    },
    ("POST", "/api/budgets/batch"): lambda w: {
        "month": "2026-05-01",
        "items": [{"category_id": w["second_category_id"], "amount": 300}],
    },
    ("POST", "/api/payees/merge"): lambda w: {
        "source_ids": [w["second_payee_id"]], "target_id": w["payee_id"],
    },
    ("POST", "/api/assets/{asset_id}/transactions"): lambda w: {
        "kind": "buy", "quantity": 1, "price": 100, "date": "2026-04-28",
    },
    ("POST", "/api/agents/connections"): lambda w: {
        "name": "Second LLM", "kind": "ollama", "base_url": "http://ollama:11434",
    },
    ("PUT", "/api/agents/{agent_id}/tools"): lambda w: [],
    ("POST", "/api/invoices"): lambda w: {
        "payee_id": w["payee_id"], "currency": "USD", "lines": _INVOICE_LINES,
    },
    ("POST", "/api/invoice-schedules"): lambda w: {
        "name": "Second retainer", "frequency": "monthly", "payee_id": w["payee_id"],
        "lines": _INVOICE_LINES,
    },
    ("PATCH", "/api/admin/settings/{key}"): lambda w: {"value": "true"},
    ("POST", "/api/transactions/link-transfer"): lambda w: {
        "transaction_ids": [w["second_transaction_id"], w["savings_transaction_id"]],
    },
}

# Routes that need an issued (open) invoice rather than the world's draft.
_ISSUED_INVOICE = {"invoice_id": "issued_invoice_id"}
PARAM_WORLD_KEYS_BY_OPERATION: dict[tuple[str, str], dict[str, str]] = {
    (method, f"/api/invoices/{{invoice_id}}/{action}"): _ISSUED_INVOICE
    for method, action in (
        ("POST", "void"),
        ("POST", "uncollectible"),
        ("POST", "make-recurring"),
        ("POST", "allocations"),
        ("POST", "deductions"),
        ("POST", "share"),
        ("GET", "statement"),
    )
}

@dataclass(frozen=True)
class KnownBug:
    """A bug on the baseline branch: what the route *should* answer, and why it doesn't."""

    expected: int
    reason: str


# The baseline records the correct status; the case is a strict xfail until
# the fix lands, at which point the unexpected pass forces this entry out.
KNOWN_BUGS: dict[tuple[str, str, str], KnownBug] = {
    (VARIANT_OK, "POST", "/api/agents/{agent_id}/knowledge"): KnownBug(
        201,
        "500 MissingGreenlet when the Celery dispatch fails: mark_status() commits, "
        "expiring the doc that _serialize() then reads",
    ),
}

# Routes that answer 500 on purpose, by an explicit HTTPException. The 500
# guard skips these; each one is a design choice worth revisiting, not a crash.
DELIBERATE_500: dict[tuple[str, str], str] = {
    ("GET", "/api/auth/oidc/login"):
        "raises HTTPException(500) when OIDC_CLIENT_ID is unset (server misconfiguration)",
}

# Routes whose SQL only runs on Postgres. The SQLite sweep skips them; the
# live probe against a running stack covers them.
POSTGRES_ONLY: dict[tuple[str, str], str] = {
    ("GET", "/api/reports/income-expenses"): "to_char() is Postgres-only",
    ("GET", "/api/dashboard/monthly-trend"): "to_char() is Postgres-only",
}


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    params: tuple[dict, ...]
    content_type: str | None
    body_schema: dict | None

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def path_params(self) -> list[str]:
        return [p["name"] for p in self.params if p["in"] == "path"]

    @property
    def has_id_param(self) -> bool:
        return any(name not in NON_ID_PARAMS for name in self.path_params)


SPEC = app.openapi()
COMPONENTS = SPEC.get("components", {})


def _operations() -> list[Operation]:
    ops = []
    for path, methods in SPEC["paths"].items():
        for method, op in methods.items():
            content = op.get("requestBody", {}).get("content", {})
            ctype = next(iter(content), None)
            ops.append(
                Operation(
                    method=method.upper(),
                    path=path,
                    params=tuple(op.get("parameters", [])),
                    content_type=ctype,
                    body_schema=content.get(ctype, {}).get("schema") if ctype else None,
                )
            )
    return ops


OPERATIONS = _operations()


def _requires_fields(op: Operation) -> bool:
    if op.content_type != "application/json" or not op.body_schema:
        return False
    schema = op.body_schema
    while "$ref" in schema:
        schema = COMPONENTS["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return bool(schema.get("required"))


def variants_for(op: Operation) -> list[str]:
    out = [VARIANT_OK, VARIANT_ANON]
    if op.method in MUTATING and not op.path.startswith(ADMIN_PREFIXES):
        out.append(VARIANT_VIEWER)
    if _requires_fields(op):
        out.append(VARIANT_EMPTY)
    if op.has_id_param:
        out.append(VARIANT_UNKNOWN_ID)
    return out


def _world_key(op: Operation, name: str) -> str:
    per_operation = PARAM_WORLD_KEYS_BY_OPERATION.get((op.method, op.path), {})
    if name in per_operation:
        return per_operation[name]
    for prefix, mapping in PARAM_WORLD_KEYS_BY_PREFIX.items():
        if op.path.startswith(prefix) and name in mapping:
            return mapping[name]
    return PARAM_WORLD_KEYS.get(name, name)


def fill_path(op: Operation, world: dict, unknown_ids: bool = False) -> str:
    values = {}
    for name in op.path_params:
        if name in STATIC_PARAMS:
            values[name] = STATIC_PARAMS[name]
        elif unknown_ids:
            values[name] = str(uuid.uuid4())
        else:
            values[name] = world.get(_world_key(op, name)) or str(uuid.uuid4())
    return op.path.format(**values)


def query_for(op: Operation, world: dict) -> dict:
    query = {}
    for param in op.params:
        if param["in"] == "query" and param.get("required"):
            query[param["name"]] = value_for(param["name"], param.get("schema", {}), COMPONENTS, world)
    return query


def _body(op: Operation, world: dict) -> Any:
    override = BODY_OVERRIDES.get((op.method, op.path))
    if override is not None:
        try:
            return override(world)
        except KeyError:
            # Cases without a seeded world: the generated body is enough,
            # since the request is refused before the body matters.
            pass
    return object_for(op.body_schema or {}, COMPONENTS, world)


def request_kwargs(op: Operation, world: dict, empty_body: bool = False) -> dict:
    kwargs: dict = {}
    query = query_for(op, world)
    if query:
        kwargs["params"] = query
    if op.content_type == "application/json":
        if empty_body:
            kwargs["json"] = {}
        else:
            kwargs["json"] = _body(op, world)
    elif op.content_type == "multipart/form-data":
        kwargs["files"] = {"file": ("upload.png", PNG_BYTES, "image/png")}
    elif op.content_type == "application/x-www-form-urlencoded":
        kwargs["data"] = {"username": "test@example.com", "password": "testpass123"}
    return kwargs
