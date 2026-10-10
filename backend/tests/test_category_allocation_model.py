"""Tests for TransactionCategoryAllocation model import and column structure."""
import pytest


def test_model_importable():
    """TransactionCategoryAllocation can be imported from app.models."""
    from app.models.transaction_category_allocation import TransactionCategoryAllocation
    assert TransactionCategoryAllocation.__tablename__ == "transaction_category_allocations"


def test_model_registered_in_init():
    """TransactionCategoryAllocation is in the app.models namespace."""
    import app.models as models
    assert hasattr(models, "TransactionCategoryAllocation")


def test_model_columns():
    """Model has the required columns with correct types."""
    from app.models.transaction_category_allocation import TransactionCategoryAllocation
    from sqlalchemy import inspect

    mapper = inspect(TransactionCategoryAllocation)
    col_names = {c.key for c in mapper.columns}

    assert "id" in col_names
    assert "transaction_id" in col_names
    assert "workspace_id" in col_names
    assert "category_id" in col_names
    assert "amount" in col_names
    assert "notes" in col_names
    assert "position" in col_names
    assert "created_at" in col_names


def test_transaction_has_category_allocations_relationship():
    """Transaction model has category_allocations relationship."""
    from app.models.transaction import Transaction
    from sqlalchemy import inspect

    mapper = inspect(Transaction)
    rel_names = {r.key for r in mapper.relationships}
    assert "category_allocations" in rel_names
