# backend/tests/test_rule_engine.py
import uuid
from decimal import Decimal
from datetime import date


from app.models import Transaction
from app.services.rule_engine import evaluate_conditions, apply_rule_actions


def make_tx(**kwargs) -> Transaction:
    defaults = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        payee_id=uuid.uuid4(),
        category_id=None,
        description="UBER TRIP",
        amount=Decimal("25.50"),
        currency="BRL",
        date=date(2026, 2, 10),
        type="debit",
        source="manual",
        notes=None,
    )
    defaults.update(kwargs)
    return Transaction(**defaults)


# --- evaluate_conditions tests ---


def test_contains_match():
    conditions = [{"field": "description", "op": "contains", "value": "UBER"}]
    tx = make_tx(description="UBER TRIP")
    assert evaluate_conditions("and", conditions, tx) is True


def test_contains_case_insensitive():
    conditions = [{"field": "description", "op": "contains", "value": "uber"}]
    tx = make_tx(description="UBER TRIP")
    assert evaluate_conditions("and", conditions, tx) is True


def test_not_contains():
    conditions = [{"field": "description", "op": "not_contains", "value": "IFOOD"}]
    tx = make_tx(description="UBER TRIP")
    assert evaluate_conditions("and", conditions, tx) is True


def test_starts_with():
    conditions = [{"field": "description", "op": "starts_with", "value": "UBER"}]
    tx = make_tx(description="UBER TRIP")
    assert evaluate_conditions("and", conditions, tx) is True


def test_ends_with():
    conditions = [{"field": "description", "op": "ends_with", "value": "TRIP"}]
    tx = make_tx(description="UBER TRIP")
    assert evaluate_conditions("and", conditions, tx) is True


def test_equals():
    conditions = [{"field": "type", "op": "equals", "value": "debit"}]
    tx = make_tx(type="debit")
    assert evaluate_conditions("and", conditions, tx) is True


def test_equals_no_match():
    conditions = [{"field": "type", "op": "equals", "value": "credit"}]
    tx = make_tx(type="debit")
    assert evaluate_conditions("and", conditions, tx) is False


def test_regex():
    conditions = [{"field": "description", "op": "regex", "value": "PIX.*RECEBIDO"}]
    tx = make_tx(description="PIX RECEBIDO JOAO")
    assert evaluate_conditions("and", conditions, tx) is True


def test_regex_whitespace_class():
    conditions = [{"field": "description", "op": "regex", "value": r"PIX\s+RECEBIDO"}]
    tx = make_tx(description="PIX RECEBIDO JOAO")
    assert evaluate_conditions("and", conditions, tx) is True


def test_regex_digit_class():
    conditions = [{"field": "description", "op": "regex", "value": r"NOTA \d+"}]
    tx = make_tx(description="OPERACOES EM BOLSA NOTA 123884393")
    assert evaluate_conditions("and", conditions, tx) is True


def test_regex_word_boundary():
    conditions = [{"field": "description", "op": "regex", "value": r"\bCDB\b"}]
    tx = make_tx(description="VENCIMENTO CDB BANCO MASTER")
    assert evaluate_conditions("and", conditions, tx) is True


def test_regex_word_boundary_no_match():
    conditions = [{"field": "description", "op": "regex", "value": r"\bCDB\b"}]
    tx = make_tx(description="COMPRA CDB321LQ8I6 RESGATE")
    assert evaluate_conditions("and", conditions, tx) is False


def test_regex_negative_lookahead_with_whitespace():
    conditions = [
        {"field": "description", "op": "regex", "value": r"RESGATE(?!\s+PONTOS)"}
    ]
    assert evaluate_conditions("and", conditions, make_tx(description="RESGATE RDB")) is True
    assert evaluate_conditions("and", conditions, make_tx(description="RESGATE PONTOS")) is False


def test_regex_inline_flag_is_valid():
    conditions = [{"field": "description", "op": "regex", "value": "(?i)uber"}]
    tx = make_tx(description="UBER TRIP")
    assert evaluate_conditions("and", conditions, tx) is True


def test_regex_lowercase_pattern_still_matches():
    conditions = [{"field": "description", "op": "regex", "value": "pix.*recebido"}]
    tx = make_tx(description="PIX RECEBIDO JOAO")
    assert evaluate_conditions("and", conditions, tx) is True


def test_regex_accented_pattern_still_matches():
    conditions = [{"field": "description", "op": "regex", "value": "APLICAÇÃO"}]
    tx = make_tx(description="APLICACAO EM CDB")
    assert evaluate_conditions("and", conditions, tx) is True


def test_amount_lt():
    conditions = [{"field": "amount", "op": "lt", "value": 50}]
    tx = make_tx(amount=Decimal("25.50"))
    assert evaluate_conditions("and", conditions, tx) is True


def test_amount_gt_no_match():
    conditions = [{"field": "amount", "op": "gt", "value": 100}]
    tx = make_tx(amount=Decimal("25.50"))
    assert evaluate_conditions("and", conditions, tx) is False


def test_and_all_match():
    conditions = [
        {"field": "description", "op": "contains", "value": "UBER"},
        {"field": "amount", "op": "lt", "value": 50},
    ]
    tx = make_tx(description="UBER TRIP", amount=Decimal("25.50"))
    assert evaluate_conditions("and", conditions, tx) is True


def test_and_partial_match():
    conditions = [
        {"field": "description", "op": "contains", "value": "UBER"},
        {"field": "amount", "op": "gt", "value": 100},  # fails
    ]
    tx = make_tx(description="UBER TRIP", amount=Decimal("25.50"))
    assert evaluate_conditions("and", conditions, tx) is False


def test_or_one_match():
    conditions = [
        {"field": "description", "op": "contains", "value": "IFOOD"},  # fails
        {"field": "description", "op": "contains", "value": "UBER"},  # passes
    ]
    tx = make_tx(description="UBER TRIP")
    assert evaluate_conditions("or", conditions, tx) is True


# --- apply_rule_actions tests ---


def test_set_category():
    cat_id = uuid.uuid4()
    actions = [{"op": "set_category", "value": str(cat_id)}]
    tx = make_tx()
    category_set = apply_rule_actions(actions, tx, category_already_set=False)
    assert tx.category_id == cat_id
    assert category_set is True


def test_set_category_skips_if_already_set():
    cat_id1 = uuid.uuid4()
    cat_id2 = uuid.uuid4()
    # First rule sets category
    actions1 = [{"op": "set_category", "value": str(cat_id1)}]
    tx = make_tx()
    apply_rule_actions(actions1, tx, category_already_set=False)
    # Second rule should NOT override
    actions2 = [{"op": "set_category", "value": str(cat_id2)}]
    apply_rule_actions(actions2, tx, category_already_set=True)
    assert tx.category_id == cat_id1  # unchanged


def test_append_notes():
    actions = [{"op": "append_notes", "value": "#work #reimbursable"}]
    tx = make_tx(notes=None)
    apply_rule_actions(actions, tx, category_already_set=False)
    assert tx.notes == "#work #reimbursable"


def test_append_notes_accumulates():
    actions1 = [{"op": "append_notes", "value": "#work"}]
    actions2 = [{"op": "append_notes", "value": "#small"}]
    tx = make_tx(notes=None)
    apply_rule_actions(actions1, tx, category_already_set=False)
    apply_rule_actions(actions2, tx, category_already_set=False)

    assert tx.notes is not None
    assert "#work" in tx.notes
    assert "#small" in tx.notes


def test_append_notes_no_duplicate():
    actions = [{"op": "append_notes", "value": "#work"}]
    tx = make_tx(notes="#work")
    apply_rule_actions(actions, tx, category_already_set=False)

    assert tx.notes is not None
    assert tx.notes.count("#work") == 1


def test_ignore_action_sets_flag():
    actions = [{"op": "ignore"}]
    tx = make_tx(is_ignored=False)
    apply_rule_actions(actions, tx, category_already_set=False)
    assert tx.is_ignored is True

def test_set_description_preserves_first_original_and_is_idempotent():
    tx = make_tx(description="|fd*f|ood Club", original_description=None)

    apply_rule_actions(
        [{"op": "set_description", "value": "iFood"}],
        tx,
        category_already_set=False,
    )
    assert tx.description == "iFood"
    assert tx.original_description == "|fd*f|ood Club"
    assert tx.description_is_rule_managed is True

    apply_rule_actions(
        [{"op": "set_description", "value": "iFood Delivery"}],
        tx,
        category_already_set=False,
    )
    assert tx.description == "iFood Delivery"
    assert tx.original_description == "|fd*f|ood Club"
    assert tx.description_is_rule_managed is True


def test_raw_payee_condition_is_case_and_accent_insensitive():
    tx = make_tx(payee="IFOOD.COM AGÊNCIA DE RESTAURANTES ONLINE S.A.")
    conditions = [
        {"field": "payee", "op": "contains", "value": "ifood.com agencia"}
    ]

    assert evaluate_conditions("and", conditions, tx) is True


def test_payee_id_condition_remains_compatible():
    payee_id = uuid.uuid4()
    tx = make_tx(payee_id=payee_id)
    conditions = [
        {"field": "payee_id", "op": "equals", "value": str(payee_id)}
    ]

    assert evaluate_conditions("and", conditions, tx) is True


# --- Edge-case: evaluate_conditions ---


def test_not_equals():
    conditions = [{"field": "type", "op": "not_equals", "value": "credit"}]
    tx = make_tx(type="debit")
    assert evaluate_conditions("and", conditions, tx) is True


def test_not_equals_same_value():
    conditions = [{"field": "type", "op": "not_equals", "value": "debit"}]
    tx = make_tx(type="debit")
    assert evaluate_conditions("and", conditions, tx) is False


def test_gte_equal():
    conditions = [{"field": "amount", "op": "gte", "value": 25.50}]
    tx = make_tx(amount=Decimal("25.50"))
    assert evaluate_conditions("and", conditions, tx) is True


def test_gte_greater():
    conditions = [{"field": "amount", "op": "gte", "value": 20}]
    tx = make_tx(amount=Decimal("25.50"))
    assert evaluate_conditions("and", conditions, tx) is True


def test_gte_less():
    conditions = [{"field": "amount", "op": "gte", "value": 30}]
    tx = make_tx(amount=Decimal("25.50"))
    assert evaluate_conditions("and", conditions, tx) is False


def test_lte_equal():
    conditions = [{"field": "amount", "op": "lte", "value": 25.50}]
    tx = make_tx(amount=Decimal("25.50"))
    assert evaluate_conditions("and", conditions, tx) is True


def test_lte_less():
    conditions = [{"field": "amount", "op": "lte", "value": 30}]
    tx = make_tx(amount=Decimal("25.50"))
    assert evaluate_conditions("and", conditions, tx) is True


def test_lte_greater():
    conditions = [{"field": "amount", "op": "lte", "value": 20}]
    tx = make_tx(amount=Decimal("25.50"))
    assert evaluate_conditions("and", conditions, tx) is False


def test_date_gt():
    conditions = [{"field": "date", "op": "gt", "value": "2026-02-09"}]
    tx = make_tx(date=date(2026, 2, 10))
    assert evaluate_conditions("and", conditions, tx) is True


def test_date_gte_equal():
    conditions = [{"field": "date", "op": "gte", "value": "2026-02-10"}]
    tx = make_tx(date=date(2026, 2, 10))
    assert evaluate_conditions("and", conditions, tx) is True


def test_date_lt():
    conditions = [{"field": "date", "op": "lt", "value": "2026-02-11"}]
    tx = make_tx(date=date(2026, 2, 10))
    assert evaluate_conditions("and", conditions, tx) is True


def test_date_lte_equal():
    conditions = [{"field": "date", "op": "lte", "value": "2026-02-10"}]
    tx = make_tx(date=date(2026, 2, 10))
    assert evaluate_conditions("and", conditions, tx) is True


def test_invalid_date_comparison_returns_false():
    conditions = [{"field": "date", "op": "gte", "value": "not-a-date"}]
    tx = make_tx(date=date(2026, 2, 10))
    assert evaluate_conditions("and", conditions, tx) is False


def test_empty_conditions_returns_false():
    tx = make_tx()
    assert evaluate_conditions("and", [], tx) is False
    assert evaluate_conditions("or", [], tx) is False


def test_unknown_operator_returns_false():
    conditions = [{"field": "description", "op": "fuzzy_match", "value": "UBER"}]
    tx = make_tx(description="UBER TRIP")
    assert evaluate_conditions("and", conditions, tx) is False


def test_none_field_value_string_op():
    conditions = [{"field": "notes", "op": "contains", "value": "tag"}]
    tx = make_tx(notes=None)
    assert evaluate_conditions("and", conditions, tx) is False


def test_none_field_value_not_contains():
    conditions = [{"field": "notes", "op": "not_contains", "value": "tag"}]
    tx = make_tx(notes=None)
    assert evaluate_conditions("and", conditions, tx) is True


def test_invalid_regex_returns_false():
    conditions = [{"field": "description", "op": "regex", "value": "[invalid("}]
    tx = make_tx(description="UBER TRIP")
    assert evaluate_conditions("and", conditions, tx) is False


# --- Edge-case: apply_rule_actions ---


def test_invalid_uuid_set_category_skips():
    actions = [{"op": "set_category", "value": "not-a-uuid"}]
    tx = make_tx()
    result = apply_rule_actions(actions, tx, category_already_set=False)
    assert tx.category_id is None
    assert result is False


def test_empty_append_notes_no_change():
    actions = [{"op": "append_notes", "value": ""}]
    tx = make_tx(notes="existing")
    apply_rule_actions(actions, tx, category_already_set=False)
    assert tx.notes == "existing"


def test_whitespace_only_append_notes_no_change():
    actions = [{"op": "append_notes", "value": "   "}]
    tx = make_tx(notes="existing")
    apply_rule_actions(actions, tx, category_already_set=False)
    assert tx.notes == "existing"


def test_multiple_actions_set_category_and_append_notes():
    cat_id = uuid.uuid4()
    actions = [
        {"op": "set_category", "value": str(cat_id)},
        {"op": "append_notes", "value": "#transport"},
    ]
    tx = make_tx()
    result = apply_rule_actions(actions, tx, category_already_set=False)
    assert tx.category_id == cat_id
    assert tx.notes == "#transport"
    assert result is True


def test_or_no_matches_returns_false():
    conditions = [
        {"field": "description", "op": "contains", "value": "IFOOD"},
        {"field": "description", "op": "contains", "value": "RAPPI"},
    ]
    tx = make_tx(description="UBER TRIP")
    assert evaluate_conditions("or", conditions, tx) is False


def test_rule_priority_ordering():
    """Lower priority rules apply first; first category wins."""
    cat_low = uuid.uuid4()
    cat_high = uuid.uuid4()
    tx = make_tx(description="UBER TRIP")

    # Simulate priority ordering: low-priority rule runs first
    actions_low = [{"op": "set_category", "value": str(cat_low)}]
    actions_high = [{"op": "set_category", "value": str(cat_high)}]

    category_set = False
    category_set = apply_rule_actions(actions_low, tx, category_already_set=category_set)
    category_set = apply_rule_actions(actions_high, tx, category_already_set=category_set)

    assert tx.category_id == cat_low


# --- set_payee action tests ---


def test_set_payee_action():
    payee_id = uuid.uuid4()
    actions = [{"op": "set_payee", "value": str(payee_id)}]
    tx = make_tx()
    tx.payee_id = None
    apply_rule_actions(actions, tx, category_already_set=False)
    assert tx.payee_id == payee_id


# --- payee_id condition tests ---


def test_payee_id_equals_match():
    payee_id = uuid.uuid4()
    conditions = [{"field": "payee_id", "op": "equals", "value": str(payee_id)}]
    tx = make_tx(payee_id=payee_id)
    assert evaluate_conditions("and", conditions, tx) is True


def test_payee_id_equals_no_match():
    payee_id = uuid.uuid4()
    other_payee_id = uuid.uuid4()
    conditions = [{"field": "payee_id", "op": "equals", "value": str(other_payee_id)}]
    tx = make_tx(payee_id=payee_id)
    assert evaluate_conditions("and", conditions, tx) is False


def test_payee_id_not_equals():
    payee_id = uuid.uuid4()
    other_payee_id = uuid.uuid4()
    conditions = [{"field": "payee_id", "op": "not_equals", "value": str(other_payee_id)}]
    tx = make_tx(payee_id=payee_id)
    assert evaluate_conditions("and", conditions, tx) is True


def test_payee_id_none_not_equals_real_payee():
    payee_id = uuid.uuid4()
    conditions = [{"field": "payee_id", "op": "not_equals", "value": str(payee_id)}]
    tx = make_tx(payee_id=None)
    assert evaluate_conditions("and", conditions, tx) is True


def test_set_payee_invalid_uuid_ignored():
    actions = [{"op": "set_payee", "value": "not-a-uuid"}]
    tx = make_tx()
    tx.payee_id = None
    apply_rule_actions(actions, tx, category_already_set=False)
    assert tx.payee_id is None


def test_set_payee_combined_with_category():
    cat_id = uuid.uuid4()
    payee_id = uuid.uuid4()
    actions = [
        {"op": "set_category", "value": str(cat_id)},
        {"op": "set_payee", "value": str(payee_id)},
    ]
    tx = make_tx()
    tx.payee_id = None
    apply_rule_actions(actions, tx, category_already_set=False)
    assert tx.category_id == cat_id
    assert tx.payee_id == payee_id


# --- blank condition values (issue #438) ---

def test_blank_value_never_matches():
    """A blank value must not turn a condition into a match-everything rule."""
    tx = make_tx(description="UBER TRIP")
    for op in ("contains", "starts_with", "ends_with", "regex", "equals", "not_equals"):
        conditions = [{"field": "description", "op": op, "value": ""}]
        assert evaluate_conditions("and", conditions, tx) is False, op


def test_whitespace_and_none_values_never_match():
    tx = make_tx(description="UBER TRIP")
    for value in ("   ", None):
        conditions = [{"field": "description", "op": "contains", "value": value}]
        assert evaluate_conditions("and", conditions, tx) is False


def test_blank_numeric_value_never_matches():
    """Blank numeric values used to fall back to 0, matching every amount."""
    tx = make_tx(amount=Decimal("25.50"))
    conditions = [{"field": "amount", "op": "gt", "value": ""}]
    assert evaluate_conditions("and", conditions, tx) is False


def test_zero_value_still_matches():
    """0 is a real value, not a blank one — it must keep working."""
    tx = make_tx(amount=Decimal("25.50"))
    conditions = [{"field": "amount", "op": "gt", "value": 0}]
    assert evaluate_conditions("and", conditions, tx) is True


# --- nested condition groups (mixing AND and OR) ---

def _group(op, *conditions):
    return {"op": op, "conditions": list(conditions)}


def test_and_of_or_group_matches():
    """`type is debit AND (description contains UBER OR contains 99POP)`."""
    conditions = [
        {"field": "type", "op": "equals", "value": "debit"},
        _group(
            "or",
            {"field": "description", "op": "contains", "value": "UBER"},
            {"field": "description", "op": "contains", "value": "99POP"},
        ),
    ]
    assert evaluate_conditions("and", conditions, make_tx(description="99POP VIAGEM")) is True
    assert evaluate_conditions("and", conditions, make_tx(description="UBER TRIP")) is True


def test_and_of_or_group_outer_condition_fails():
    conditions = [
        {"field": "type", "op": "equals", "value": "debit"},
        _group(
            "or",
            {"field": "description", "op": "contains", "value": "UBER"},
            {"field": "description", "op": "contains", "value": "99POP"},
        ),
    ]
    tx = make_tx(description="UBER TRIP", type="credit")
    assert evaluate_conditions("and", conditions, tx) is False


def test_and_of_or_group_no_leaf_matches():
    conditions = [
        {"field": "type", "op": "equals", "value": "debit"},
        _group(
            "or",
            {"field": "description", "op": "contains", "value": "UBER"},
            {"field": "description", "op": "contains", "value": "99POP"},
        ),
    ]
    assert evaluate_conditions("and", conditions, make_tx(description="IFOOD")) is False


def test_or_of_and_group():
    """`description contains IFOOD OR (contains MERCADO AND amount > 100)`."""
    conditions = [
        {"field": "description", "op": "contains", "value": "IFOOD"},
        _group(
            "and",
            {"field": "description", "op": "contains", "value": "MERCADO"},
            {"field": "amount", "op": "gt", "value": 100},
        ),
    ]
    assert evaluate_conditions("or", conditions, make_tx(description="IFOOD PEDIDO")) is True
    assert evaluate_conditions(
        "or", conditions, make_tx(description="MERCADO X", amount=Decimal("250.00"))
    ) is True
    assert evaluate_conditions(
        "or", conditions, make_tx(description="MERCADO X", amount=Decimal("30.00"))
    ) is False


def test_group_defaults_to_and_without_op():
    conditions = [
        _group(
            None,
            {"field": "description", "op": "contains", "value": "UBER"},
            {"field": "type", "op": "equals", "value": "debit"},
        ),
    ]
    assert evaluate_conditions("and", conditions, make_tx(description="UBER TRIP")) is True
    conditions[0]["conditions"][1]["value"] = "credit"
    assert evaluate_conditions("and", conditions, make_tx(description="UBER TRIP")) is False


def test_empty_group_never_matches():
    """An empty group has nothing to match, like an empty condition list."""
    conditions = [_group("or")]
    assert evaluate_conditions("and", conditions, make_tx()) is False
    assert evaluate_conditions("or", conditions, make_tx()) is False


def test_blank_value_inside_group_never_matches():
    conditions = [
        _group(
            "or",
            {"field": "description", "op": "contains", "value": ""},
            {"field": "description", "op": "contains", "value": "IFOOD"},
        ),
    ]
    assert evaluate_conditions("and", conditions, make_tx(description="UBER TRIP")) is False


def test_flat_conditions_still_evaluate_unchanged():
    """Rules saved before groups existed keep their exact meaning."""
    conditions = [
        {"field": "description", "op": "contains", "value": "UBER"},
        {"field": "type", "op": "equals", "value": "debit"},
    ]
    assert evaluate_conditions("and", conditions, make_tx(description="UBER TRIP")) is True
    assert evaluate_conditions("or", conditions, make_tx(description="IFOOD")) is True
