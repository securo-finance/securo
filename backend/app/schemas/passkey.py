import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PasskeyRead(BaseModel):
    id: uuid.UUID
    name: str
    transports: list[str] | None = None
    aaguid: str | None = None
    device_type: str | None = None
    backed_up: bool | None = None
    created_at: datetime
    last_used_at: datetime | None = None

    model_config = {"from_attributes": True}


class PasskeyRegisterOptionsRequest(BaseModel):
    name: str = Field(default="Passkey", min_length=1, max_length=100)


class PasskeyOptionsResponse(BaseModel):
    challenge_id: str
    options: dict[str, Any]


class PasskeyRegisterVerifyRequest(BaseModel):
    challenge_id: str
    name: str = Field(default="Passkey", min_length=1, max_length=100)
    credential: dict[str, Any]


class PasskeyAuthenticateOptionsRequest(BaseModel):
    email: str | None = None


class PasskeyAuthenticateVerifyRequest(BaseModel):
    challenge_id: str
    credential: dict[str, Any]


class PasskeySecondFactorOptionsRequest(BaseModel):
    temp_token: str


class PasskeySecondFactorVerifyRequest(BaseModel):
    temp_token: str
    challenge_id: str
    credential: dict[str, Any]
