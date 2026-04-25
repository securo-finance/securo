import uuid
from datetime import date, datetime
from decimal import Decimal

from app.core.utils import deserialize_row, serialize_model
from app.models.transaction import Transaction


def test_serialize_model():
    t_id = uuid.uuid4()
    t_date = date(2023, 10, 1)
    t_created_at = datetime(2023, 10, 1, 12, 0, 0)

    transaction = Transaction(
        id=t_id,
        user_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        category_id=uuid.uuid4(),
        amount=Decimal("150.25"),
        date=t_date,
        effective_date=t_date,
        created_at=t_created_at,
        currency="USD",
        description="Grocery",
    )

    serialized = serialize_model(transaction)

    assert serialized["id"] == str(t_id)
    assert serialized["amount"] == "150.25"
    assert serialized["date"] == "2023-10-01"
    assert serialized["created_at"] == "2023-10-01T12:00:00"
    assert serialized["currency"] == "USD"
    assert serialized["description"] == "Grocery"


def test_deserialize_row():
    t_id_str = str(uuid.uuid4())
    user_id_str = str(uuid.uuid4())
    account_id_str = str(uuid.uuid4())
    category_id_str = str(uuid.uuid4())

    raw_row = {
        "id": t_id_str,
        "user_id": user_id_str,
        "account_id": account_id_str,
        "category_id": category_id_str,
        "amount": "150.25",
        "date": "2023-10-01",
        "created_at": "2023-10-01T12:00:00+00:00",
        "currency": "USD",
        "description": "Grocery",
        "invalid_extra_field": "should_be_ignored",
        "raw_data": None,
    }

    deserialized = deserialize_row(Transaction, raw_row)

    assert deserialized["id"] == uuid.UUID(t_id_str)
    assert deserialized["user_id"] == uuid.UUID(user_id_str)
    assert deserialized["amount"] == Decimal("150.25")
    assert deserialized["date"] == date(2023, 10, 1)
    assert deserialized["created_at"].isoformat() == "2023-10-01T12:00:00+00:00"
    assert deserialized["currency"] == "USD"
    assert deserialized["description"] == "Grocery"
    assert "invalid_extra_field" not in deserialized
    assert "raw_data" not in deserialized
