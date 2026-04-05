import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class BankConnectionBase(BaseModel):
    provider: str
    institution_name: str


class BankConnectionRead(BankConnectionBase):
    id: uuid.UUID
    user_id: uuid.UUID
    external_id: str
    settings: Optional[dict] = None
    status: str
    last_sync_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OAuthUrlRequest(BaseModel):
    provider: str = "pluggy"


class OAuthUrlResponse(BaseModel):
    url: str


class OAuthCallbackRequest(BaseModel):
    code: str
    provider: str = "pluggy"


class ConnectTokenRequest(BaseModel):
    provider: str = "pluggy"


class ConnectTokenResponse(BaseModel):
    access_token: str


class ReconnectTokenResponse(BaseModel):
    access_token: str


class ConnectionSettingsUpdate(BaseModel):
    display_name: Optional[str] = None
    bill_import_enabled: Optional[bool] = None
    payee_source: Optional[Literal["auto", "merchant", "payment_data", "description", "none"]] = None
    import_pending: Optional[bool] = None
