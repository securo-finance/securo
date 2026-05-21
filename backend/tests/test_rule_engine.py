# backend/tests/test_rule_engine.py
import types
import uuid
from decimal import Decimal
from datetime import date


from app.services.rule_engine import evaluate_conditions, apply_rule_actions


def make_tx(**kwargs) -> types.SimpleNamespace:
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
    return types.SimpleNamespace(**defaults)


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
        {"field": "description", "op": "contains", "value": "UBER"},   # passes
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
    assert "#work" in tx.notes
    assert "#small" in tx.notes


def test_append_notes_no_duplicate():
    actions = [{"op": "append_notes", "value": "#work"}]
    tx = make_tx(notes="#work")
    apply_rule_actions(actions, tx, category_already_set=False)
    assert tx.notes.count("#work") == 1


def test_ignore_action_sets_flag():
    actions = [{"op": "ignore"}]
    tx = make_tx(is_ignored=False)
    apply_rule_actions(actions, tx, category_already_set=False)
    assert tx.is_ignored is True


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
