"""Build minimal, valid-looking request values from the OpenAPI schema.

Only required fields are filled. `*_id` fields take ids from the seeded world
when the name matches; everything else gets a type-based placeholder.
"""
from typing import Any

PLACEHOLDER_STRING = "Contract test"
PLACEHOLDER_DATE = "2026-04-28"
PLACEHOLDER_DATETIME = "2026-04-28T12:00:00Z"
PLACEHOLDER_EMAIL = "contract@example.com"
PLACEHOLDER_URL = "https://example.com"
PLACEHOLDER_NUMBER = 10
PLACEHOLDER_CURRENCY = "USD"

# Field names that should resolve to a specific world id. Everything else
# ending in `_id` falls back to the world key of the same name.
ID_ALIASES = {
    "group_member_id": "member_id",
    "from_member_id": "member_id",
    "to_member_id": "second_member_id",
    "target_id": "payee_id",
    "parent_id": "category_id",
    "source_category_id": "category_id",
    "destination_category_id": "category_id",
    "target_category_id": "category_id",
    "from_account_id": "account_id",
    "to_account_id": "second_account_id",
    "destination_account_id": "second_account_id",
    "group_id_ref": "group_id",
    "asset_group_id": "asset_group_id",
    "category_group_id": "category_group_id",
}


def _resolve_ref(schema: dict, components: dict) -> dict:
    while "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        schema = components["schemas"][name]
    return schema


def _pick_variant(schema: dict, components: dict) -> dict:
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [_resolve_ref(o, components) for o in schema[key]]
            non_null = [o for o in options if o.get("type") != "null"]
            return non_null[0] if non_null else options[0]
    if "allOf" in schema:
        merged: dict = {}
        for part in schema["allOf"]:
            merged.update(_resolve_ref(part, components))
        return merged
    return schema


def _string_value(name: str, schema: dict) -> Any:
    fmt = schema.get("format")
    if fmt == "date":
        return PLACEHOLDER_DATE
    if fmt == "date-time":
        return PLACEHOLDER_DATETIME
    if fmt == "email" or "email" in name:
        return PLACEHOLDER_EMAIL
    if fmt in ("uri", "url") or name.endswith("url"):
        return PLACEHOLDER_URL
    if "currency" in name:
        return PLACEHOLDER_CURRENCY
    if "color" in name:
        return "#22c55e"
    if name == "date" or name.endswith("_date"):
        return PLACEHOLDER_DATE
    if schema.get("pattern") == "^\\d+(\\.\\d{1,2})?$" or name == "amount":
        return "10.00"
    min_length = schema.get("minLength", 1)
    return (PLACEHOLDER_STRING + " " * min_length)[: max(min_length, len(PLACEHOLDER_STRING))]


def value_for(name: str, schema: dict, components: dict, world: dict, depth: int = 0) -> Any:
    schema = _pick_variant(_resolve_ref(schema, components), components)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema and schema["default"] is not None:
        return schema["default"]

    kind = schema.get("type")
    if schema.get("format") == "uuid" or (kind == "string" and name.endswith("_id")):
        key = ID_ALIASES.get(name, name)
        return world.get(key) or world.get(name)
    if kind == "string":
        return _string_value(name, schema)
    if kind in ("integer", "number"):
        low = schema.get("minimum", schema.get("exclusiveMinimum", 0))
        high = schema.get("maximum")
        value = max(PLACEHOLDER_NUMBER, low + 1) if low is not None else PLACEHOLDER_NUMBER
        if high is not None:
            value = min(value, high)
        return value
    if kind == "boolean":
        return False
    if kind == "array":
        if depth > 3:
            return []
        min_items = schema.get("minItems", 0)
        item = value_for(name.rstrip("s"), schema.get("items", {}), components, world, depth + 1)
        return [item] * max(min_items, 1) if min_items else []
    if kind == "object" or "properties" in schema:
        if depth > 3:
            return {}
        return object_for(schema, components, world, depth + 1)
    return None


def object_for(schema: dict, components: dict, world: dict, depth: int = 0) -> dict:
    schema = _pick_variant(_resolve_ref(schema, components), components)
    props = schema.get("properties", {})
    out = {}
    for field in schema.get("required", []):
        out[field] = value_for(field, props.get(field, {}), components, world, depth)
    return out
