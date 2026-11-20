"""What a plugin's code report returns: blocks Securo draws with its own components (ADR 0028).

The plugin computes whatever it likes; what comes back is data only. There
is no HTML, CSS, script or URL in a view: text is shown as text, colors are
`#RRGGBB`, and every list is capped, so a report can't make the browser run
or fetch anything, nor make the page unusable.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MAX_BLOCKS = 20
MAX_SERIES = 12
MAX_POINTS = 1000
MAX_ROWS = 1000
MAX_COLUMNS = 12
MAX_TEXT = 2000

Format = Literal["currency", "number", "percent", "text", "date"]
Label = Annotated[str, Field(max_length=120)]
Color = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]
Number = float | int


class _Block(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Label | None = None


class KpiBlock(_Block):
    """One figure, as the reports' headline numbers: a value and, optionally, its change."""

    type: Literal["kpi"]
    label: Label
    value: Number | None
    format: Format = "currency"
    #: The change, e.g. against the previous period, in the same format unless `delta_format` says otherwise.
    delta: Number | None = None
    delta_format: Format | None = None
    hint: Label | None = None


class Series(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Label
    values: list[Number | None] = Field(max_length=MAX_POINTS)
    color: Color | None = None


class ChartBlock(_Block):
    """A chart over `x`: one value per point in each series."""

    type: Literal["chart"]
    chart: Literal["bar", "stacked_bar", "horizontal_bar", "line", "area", "stacked_area", "pie", "donut", "scatter"]
    x: list[str | Number] = Field(max_length=MAX_POINTS)
    series: list[Series] = Field(min_length=1, max_length=MAX_SERIES)
    format: Format = "currency"
    #: What `x` holds, so dates are named in the person's language.
    x_format: Literal["text", "date", "month", "number"] = "text"
    #: A color per point, for a pie, a donut or a one-series bar chart (a category's own, say).
    colors: list[Color | None] | None = Field(default=None, max_length=MAX_POINTS)

    @model_validator(mode="after")
    def _aligned(self) -> ChartBlock:
        for series in self.series:
            if len(series.values) != len(self.x):
                raise ValueError(f"series {series.name!r} has {len(series.values)} values for {len(self.x)} points")
        if self.chart in ("pie", "donut") and len(self.series) != 1:
            raise ValueError("a pie or donut shows one series")
        if self.colors is not None and len(self.colors) != len(self.x):
            raise ValueError(f"colors has {len(self.colors)} entries for {len(self.x)} points")
        return self


class Column(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: Annotated[str, Field(max_length=64)]
    label: Label
    format: Format = "text"


class TableBlock(_Block):
    type: Literal["table"]
    columns: list[Column] = Field(min_length=1, max_length=MAX_COLUMNS)
    rows: list[dict[str, str | Number | None]] = Field(max_length=MAX_ROWS)

    @model_validator(mode="after")
    def _known_cells(self) -> TableBlock:
        keys = {c.key for c in self.columns}
        for row in self.rows:
            extra = set(row) - keys
            if extra:
                raise ValueError(f"a row has cells for no column: {', '.join(sorted(extra))[:200]}")
            for value in row.values():
                if isinstance(value, str) and len(value) > 500:
                    raise ValueError("a table cell holds at most 500 characters")
        return self


class TextBlock(_Block):
    """Plain text, shown as is (line breaks kept); never parsed as HTML or Markdown."""

    type: Literal["text"]
    text: Annotated[str, Field(max_length=MAX_TEXT)]


Block = Annotated[Union[KpiBlock, ChartBlock, TableBlock, TextBlock], Field(discriminator="type")]


class ReportView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The currency `currency` values are in; the viewer's when left out.
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    blocks: list[Block] = Field(min_length=1, max_length=MAX_BLOCKS)


class InvalidView(ValueError):
    """The plugin returned something that isn't a view Securo can draw. The message never echoes values."""


def parse_view(data: Any) -> ReportView:
    try:
        return ReportView.model_validate(data)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(p) for p in e['loc']) or 'view'}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)[:5]
        ]
        raise InvalidView("the report returned an invalid view: " + "; ".join(problems)) from None
