from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

def serialize_model(obj) -> dict:
    """
    Serializes an SQLAlchemy model instance into a JSON-compatible dictionary.
    """
    row = {}
    for col in obj.__table__.columns:
        val = getattr(obj, col.key)
        if isinstance(val, UUID):
            val = str(val)
        elif hasattr(val, "isoformat"):  # datetime / date
            val = val.isoformat()
        elif isinstance(val, Decimal):
            val = str(val)
        row[col.key] = val
    return row

def deserialize_row(model, row_data: dict) -> dict:
    """
    Given an SQLAlchemy model and a dictionary of raw JSON data,
    filters exactly out invalid keys and converts string values 
    to the appropriate Python types (UUID, datetime, Decimal) based 
    on the model's schema.
    """
    valid_keys = {c.key for c in model.__table__.columns}
    clean_row = {k: v for k, v in row_data.items() if k in valid_keys}
    
    for k, v in list(clean_row.items()):
        if v is None:
            continue
            
        col_type = getattr(model.__table__.columns, k).type
        try:
            ptype = col_type.python_type
            if ptype is datetime and isinstance(v, str):
                clean_row[k] = datetime.fromisoformat(v.replace("Z", "+00:00"))
            elif ptype is date and isinstance(v, str):
                clean_row[k] = date.fromisoformat(v)
            elif ptype is Decimal and not isinstance(v, Decimal):
                clean_row[k] = Decimal(str(v))
            elif ptype is UUID and isinstance(v, str):
                clean_row[k] = UUID(v)
        except NotImplementedError:
            pass
            
    return clean_row
