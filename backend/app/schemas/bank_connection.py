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
    display_name: Optional[str] = None
    logo_url: Optional[str] = None
    settings: Optional[dict] = None
    status: str
    last_sync_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OAuthUrlRequest(BaseModel):
    provider: str = "pluggy"
    flow_params: Optional[dict] = None


class OAuthUrlResponse(BaseModel):
    url: str


class OAuthCallbackRequest(BaseModel):
    code: str
    state: Optional[str] = None
    provider: Optional[str] = None


class ReauthUrlResponse(BaseModel):
    url: str


class InstitutionRead(BaseModel):
    name: str
    display_name: str
    country: str
    logo: Optional[str] = None
    bic: Optional[str] = None
    psu_types: list[str] = []
    max_consent_days: Optional[int] = None
    max_history_days: Optional[int] = None


class InstitutionListResponse(BaseModel):
    countries: list[str]
    institutions: list[InstitutionRead]


class ConnectTokenRequest(BaseModel):
    provider: str = "pluggy"


class ConnectTokenResponse(BaseModel):
    access_token: str


class ReconnectTokenResponse(BaseModel):
    access_token: str


class ConnectionSettingsUpdate(BaseModel):
    display_name: Optional[str] = None
    payee_source: Optional[Literal["auto", "merchant", "payment_data", "description", "none"]] = None
    import_pending: Optional[bool] = None
