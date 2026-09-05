import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ImportLogRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    #: Null for an order import, which lands on holdings rather than an account.
    account_id: Optional[uuid.UUID] = None
    account_name: Optional[str] = None
    #: "transactions" for a bank statement, "asset_orders" for a broker file.
    entity: str = "transactions"
    filename: str
    format: str
    transaction_count: int
    total_credit: Decimal
    total_debit: Decimal
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
