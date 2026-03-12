import uuid
from datetime import date as _Date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.category import CategoryRead


class TransactionBase(BaseModel):
    description: str
    amount: Decimal
    date: _Date
    type: str  # debit, credit
    external_id: Optional[str] = None


class TransactionCreate(TransactionBase):
    account_id: uuid.UUID
    category_id: Optional[uuid.UUID] = None
    currency: str = "BRL"
    notes: Optional[str] = None


class TransactionUpdate(BaseModel):
    description: Optional[str] = None
    amount: Optional[Decimal] = None
    date: Optional[_Date] = None
    type: Optional[str] = None
    currency: Optional[str] = None
    category_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class TransactionRead(TransactionBase):
    id: uuid.UUID
    user_id: uuid.UUID
    account_id: Optional[uuid.UUID] = None
    category_id: Optional[uuid.UUID] = None
    category: Optional[CategoryRead] = None
    currency: str = "BRL"
    source: str
    status: str = "posted"
    payee: Optional[str] = None
    notes: Optional[str] = None
    transfer_pair_id: Optional[uuid.UUID] = None

    model_config = ConfigDict(from_attributes=True)


class BulkCategorizeRequest(BaseModel):
    transaction_ids: list[uuid.UUID]
    category_id: Optional[uuid.UUID] = None


class TransactionImportPreview(BaseModel):
    transactions: list[TransactionBase]
    detected_format: str


class TransactionImportRequest(BaseModel):
    account_id: uuid.UUID
    transactions: list[TransactionBase]
    filename: str = ""
    detected_format: str = ""
