from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def normalize_period_value(value: str) -> str:
    raw = value.strip()
    if len(raw) == 7 and raw[4] == "-":
        year = raw[:4]
        month = raw[5:]
    elif len(raw) == 7 and raw[2] == "/":
        month = raw[:2]
        year = raw[3:]
    else:
        raise ValueError("Period must use YYYY-MM or MM/YYYY format")

    if not (year.isdigit() and month.isdigit()):
        raise ValueError("Period must contain only digits")

    month_number = int(month)
    if month_number < 1 or month_number > 12:
        raise ValueError("Month must be between 01 and 12")

    return f"{year}-{month_number:02d}"


class CurrentMonthRead(BaseModel):
    current_period: str | None = None
    current_period_label: str | None = None
    is_defined: bool
    selected_mode: Literal["current", "snapshot"]
    selected_period: str | None = None
    selected_period_label: str | None = None
    is_snapshot_view: bool = False
    snapshots: list["ClosedMonthSnapshotRead"] = Field(default_factory=list)


class CurrentMonthUpdate(BaseModel):
    period: str

    @field_validator("period")
    @classmethod
    def validate_period(cls, value: str) -> str:
        return normalize_period_value(value)


class ClosedMonthSnapshotRead(BaseModel):
    period: str
    period_label: str
    closed_at: str


class CurrentMonthViewUpdate(BaseModel):
    mode: Literal["current", "snapshot"]
    period: str | None = None

    @model_validator(mode="after")
    def validate_payload(self):
        if self.mode == "snapshot":
            if not self.period:
                raise ValueError("Snapshot view requires a period")
            self.period = normalize_period_value(self.period)
        else:
            self.period = None
        return self


class CloseCurrentMonthRequest(BaseModel):
    next_period: str

    @field_validator("next_period")
    @classmethod
    def validate_period(cls, value: str) -> str:
        return normalize_period_value(value)


class CloseCurrentMonthRead(BaseModel):
    state: CurrentMonthRead
    closed_snapshot: ClosedMonthSnapshotRead
