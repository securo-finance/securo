from pydantic import BaseModel


class ReportBreakdown(BaseModel):
    key: str
    label: str
    value: float
    color: str


class ReportSummary(BaseModel):
    primary_value: float
    change_amount: float
    change_percent: float | None
    breakdowns: list[ReportBreakdown]


class ReportCompositionItem(BaseModel):
    key: str
    label: str
    value: float
    color: str
    group: str


class ReportDataPoint(BaseModel):
    date: str
    value: float
    breakdowns: dict[str, float]
    change: float | None = None
    composition: list[ReportCompositionItem] = []


class ReportMeta(BaseModel):
    type: str
    series_keys: list[str]
    currency: str
    interval: str
    forecast_start_date: str | None = None
    baseline_active: bool = False
    baseline_lookback_days: int | None = None


class CategoryTrendItem(BaseModel):
    key: str
    label: str
    color: str
    total: float
    group: str
    series: list[ReportDataPoint]


class ReportResponse(BaseModel):
    summary: ReportSummary
    trend: list[ReportDataPoint]
    meta: ReportMeta
    composition: list[ReportCompositionItem] = []
    category_trend: list[CategoryTrendItem] = []


class CategoryStatementRow(BaseModel):
    """One line of the income & expense statement.

    ``values`` are signed net amounts in the primary currency (credits
    positive, debits negative), aligned with ``CategoryStatementResponse.months``.
    """

    key: str
    kind: str  # "group" | "category" | "uncategorized"
    label: str | None
    icon: str | None = None
    color: str | None = None
    category_ids: list[str] = []
    # Set on the uncategorized rows only: they are split by transaction type,
    # so a drill-down has to filter by it to match the row.
    txn_type: str | None = None
    values: list[float]
    children: list["CategoryStatementRow"] = []


class CategoryStatementSection(BaseModel):
    totals: list[float]
    rows: list[CategoryStatementRow]


class CategoryStatementResponse(BaseModel):
    currency: str
    # "YYYY-MM", newest first: the selected month, then the ones before it.
    months: list[str]
    income: CategoryStatementSection
    expenses: CategoryStatementSection
