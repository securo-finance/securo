from pydantic import BaseModel, field_validator


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


class CurrentMonthUpdate(BaseModel):
    period: str

    @field_validator("period")
    @classmethod
    def validate_period(cls, value: str) -> str:
        return normalize_period_value(value)
