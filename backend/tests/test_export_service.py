import io
import uuid
import pytest
from app.services.export_service import ExportService
from app.models.transaction import Transaction


class DummySession:
    async def execute(self, stmt):
        return self

    def scalars(self):
        return self

    def all(self):
        return []

    async def flush(self):
        pass


@pytest.mark.asyncio
async def test_export_data_returns_bytesio():
    session = DummySession()
    user_id = "00000000-0000-0000-0000-000000000000"
    service = ExportService(session, user_id)
    result = await service.export_data()
    assert isinstance(result, io.BytesIO)


@pytest.mark.asyncio
def test_sanitize_row_sets_invalid_fk_to_none():
    user_id = uuid.uuid4()
    service = ExportService(DummySession(), user_id)
    # IDs válidos do usuário
    valid_account_id = uuid.uuid4()
    valid_category_id = uuid.uuid4()
    existing_keys = {
        "accounts": {str(valid_account_id)},
        "categories": {str(valid_category_id)},
        "payees": set(),
        "asset_groups": set(),
        "category_groups": set(),
        "assets": set(),
        "connections": set(),
    }
    # Row com FKs válidos
    row_valid = {
        "user_id": str(user_id),
        "account_id": str(valid_account_id),
        "category_id": str(valid_category_id),
        "amount": "10.00",
        "date": "2024-01-01",
        "description": "ok",
    }
    # Row com FKs inválidos
    row_invalid = {
        "user_id": str(user_id),
        "account_id": str(uuid.uuid4()),
        "category_id": str(uuid.uuid4()),
        "amount": "10.00",
        "date": "2024-01-01",
        "description": "fail",
    }
    # Deve manter FKs válidos
    clean_valid = service._sanitize_row(Transaction, row_valid, {}, existing_keys)
    assert clean_valid["account_id"] == valid_account_id
    assert clean_valid["category_id"] == valid_category_id
    # Deve setar FKs inválidos como None
    clean_invalid = service._sanitize_row(Transaction, row_invalid, {}, existing_keys)
    assert clean_invalid["account_id"] is None
    assert clean_invalid["category_id"] is None
