"""What a plugin's code report returns: blocks Securo draws with its own components (ADR 0028).

The plugin computes whatever it likes; what comes back is data only. There
is no HTML, CSS, script or URL in a view: text is shown as text, colors are
`#RRGGBB`, and every list is capped, so a report can't make the browser run
or fetch anything, nor make the page unusable.

Blocks sit on a 12-column grid by their `width` (a phone shows them one
under another): figures (`kpi`), charts over an axis (`chart`, series of
bars, lines and areas, on one or two axes, with reference lines and a
projected stretch), `heatmap`, `treemap`, `sankey`, `waterfall`,
`progress`, `table` and `text`.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MAX_BLOCKS = 24
MAX_SERIES = 12
MAX_POINTS = 1000
#: Chart points a view draws in all: the blocks are bounded one by one, but a
#: page with 24 charts each at MAX_POINTS x MAX_SERIES takes minutes to draw
#: and gigabytes of heap. One chart at those limits is what a report needs.
MAX_TOTAL_POINTS = 12_000
MAX_ROWS = 1000
MAX_COLUMNS = 12
MAX_TEXT = 2000
MAX_CELLS = 2000
MAX_NODES = 100

Format = Literal["currency", "number", "percent", "text", "date"]
Label = Annotated[str, Field(max_length=120)]
Color = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]
Number = float | int
#: How much of a row a block takes: a quarter, a third, a half, two thirds, all of it.
Width = Literal["quarter", "third", "half", "two_thirds", "full"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Block(_Model):
    title: Label | None = None
    width: Width | None = None
    """None is the block's own: a quarter for a figure, the whole row for the rest."""


class KpiBlock(_Block):
    """One figure, as the reports' headline numbers: a value and, optionally, its change."""

    type: Literal["kpi"]
    label: Label
    value: Number | None
    format: Format = "currency"
    #: The change, e.g. against the previous period, in the same format unless `delta_format` says otherwise.
    delta: Number | None = None
    delta_format: Format | None = None
    #: Whether a rise is good news (income) or bad (spending): picks the delta's color.
    delta_good: Literal["up", "down"] = "up"
    hint: Label | None = None


class Series(_Model):
    name: Label
    values: list[Number | None] = Field(max_length=MAX_POINTS)
    color: Color | None = None
    #: How this series is drawn, for a chart that mixes bars, lines and areas.
    kind: Literal["bar", "line", "area"] | None = None
    #: The right axis takes a second scale, in the chart's `y2_format`.
    axis: Literal["left", "right"] = "left"
    #: Bars or areas with the same stack id pile up.
    stack: Annotated[str, Field(max_length=32)] | None = None
    #: From this x on, the series is a projection: drawn dashed.
    projected_from: str | Number | None = None
    #: A bar below zero takes this color (a loss, a deficit).
    negative_color: Color | None = None


class ReferenceLine(_Model):
    """A line across the chart: a target, an average, or (with `x`) a moment such as today."""

    label: Label | None = None
    y: Number | None = None
    x: str | Number | None = None
    axis: Literal["left", "right"] = "left"
    color: Color | None = None

    @model_validator(mode="after")
    def _one_place(self) -> ReferenceLine:
        if (self.y is None) == (self.x is None):
            raise ValueError("a reference line has a y or an x, one of them")
        return self


class ChartBlock(_Block):
    """A chart over `x`: one value per point in each series."""

    type: Literal["chart"]
    chart: Literal[
        "bar", "stacked_bar", "horizontal_bar", "line", "area", "stacked_area", "pie", "donut", "scatter", "combo"
    ]
    x: list[str | Number] = Field(max_length=MAX_POINTS)
    series: list[Series] = Field(min_length=1, max_length=MAX_SERIES)
    format: Format = "currency"
    #: The right axis's format, for series with `axis: "right"`.
    y2_format: Format | None = None
    #: What `x` holds, so dates are named in the person's language.
    x_format: Literal["text", "date", "month", "number"] = "text"
    #: A color per point, for a pie, a donut or a one-series bar chart (a category's own, say).
    colors: list[Color | None] | None = Field(default=None, max_length=MAX_POINTS)
    references: list[ReferenceLine] = Field(default=[], max_length=10)

    @model_validator(mode="after")
    def _aligned(self) -> ChartBlock:
        for series in self.series:
            if len(series.values) != len(self.x):
                raise ValueError(f"series {series.name!r} has {len(series.values)} values for {len(self.x)} points")
            if series.projected_from is not None and series.projected_from not in self.x:
                raise ValueError(f"series {series.name!r}: projected_from must be one of the x values")
        if self.chart in ("pie", "donut") and len(self.series) != 1:
            raise ValueError("a pie or donut shows one series")
        if self.colors is not None and len(self.colors) != len(self.x):
            raise ValueError(f"colors has {len(self.colors)} entries for {len(self.x)} points")
        if any(s.axis == "right" for s in self.series) and self.chart in ("pie", "donut", "horizontal_bar"):
            raise ValueError(f"a {self.chart} chart has no second axis")
        return self


class HeatmapBlock(_Block):
    """Values on a grid: `values[row][column]` for each of `y` and `x` (weekday by month, say)."""

    type: Literal["heatmap"]
    x: list[str | Number] = Field(min_length=1, max_length=60)
    y: list[str | Number] = Field(min_length=1, max_length=60)
    values: list[list[Number | None]]
    format: Format = "currency"
    x_format: Literal["text", "date", "month", "number"] = "text"
    color: Color | None = None

    @model_validator(mode="after")
    def _shaped(self) -> HeatmapBlock:
        if len(self.values) != len(self.y) or any(len(row) != len(self.x) for row in self.values):
            raise ValueError("values must have one row per y and one value per x")
        if len(self.x) * len(self.y) > MAX_CELLS:
            raise ValueError(f"a heatmap holds at most {MAX_CELLS} cells")
        return self


class TreemapItem(_Model):
    name: Label
    value: Number | None = None
    color: Color | None = None
    children: list[TreemapLeaf] = Field(default=[], max_length=MAX_NODES)


class TreemapLeaf(_Model):
    name: Label
    value: Number
    color: Color | None = None


class TreemapBlock(_Block):
    """Parts of a total as nested boxes: categories, and the items in each."""

    type: Literal["treemap"]
    items: list[TreemapItem] = Field(min_length=1, max_length=MAX_NODES)
    format: Format = "currency"

    @model_validator(mode="after")
    def _sized(self) -> TreemapBlock:
        for item in self.items:
            if item.value is None and not item.children:
                raise ValueError(f"treemap item {item.name!r} needs a value or children")
        return self


class SankeyLink(_Model):
    source: Annotated[int, Field(ge=0)]
    target: Annotated[int, Field(ge=0)]
    value: Annotated[float, Field(gt=0)]


class SankeyBlock(_Block):
    """Flows between nodes: where money comes from and where it goes."""

    type: Literal["sankey"]
    nodes: list[Label] = Field(min_length=2, max_length=MAX_NODES)
    links: list[SankeyLink] = Field(min_length=1, max_length=MAX_NODES * 4)
    format: Format = "currency"

    @model_validator(mode="after")
    def _linked(self) -> SankeyBlock:
        for link in self.links:
            if link.source >= len(self.nodes) or link.target >= len(self.nodes):
                raise ValueError("a link names a node that isn't there")
            if link.source == link.target:
                raise ValueError("a link can't go from a node to itself")
        # Flows only go forward: a loop through other nodes can't be laid out.
        outgoing: dict[int, set[int]] = {}
        for link in self.links:
            outgoing.setdefault(link.source, set()).add(link.target)
        state: dict[int, int] = {}  # 1 while being walked, 2 once done

        def walk(node: int) -> None:
            stack = [(node, iter(outgoing.get(node, ())))]
            state[node] = 1
            while stack:
                current, children = stack[-1]
                child = next(children, None)
                if child is None:
                    state[current] = 2
                    stack.pop()
                elif state.get(child) == 1:
                    raise ValueError("links can't go round in a loop")
                elif child not in state:
                    state[child] = 1
                    stack.append((child, iter(outgoing.get(child, ()))))

        for node in list(outgoing):
            if node not in state:
                walk(node)
        return self


class WaterfallStep(_Model):
    label: Label
    value: Number
    #: A running total (start or end balance) rather than a change.
    total: bool = False


class WaterfallBlock(_Block):
    """A start, the changes that moved it, and where it ended."""

    type: Literal["waterfall"]
    steps: list[WaterfallStep] = Field(min_length=1, max_length=60)
    format: Format = "currency"


class ProgressItem(_Model):
    label: Label
    value: Number
    target: Annotated[float, Field(gt=0)]
    color: Color | None = None
    hint: Label | None = None


class ProgressBlock(_Block):
    """How far each item is towards its target: a budget used, a goal reached."""

    type: Literal["progress"]
    items: list[ProgressItem] = Field(min_length=1, max_length=50)
    format: Format = "currency"
    #: Past the target is bad (a budget) or fine (a goal).
    over_is: Literal["bad", "good"] = "bad"


class Column(_Model):
    key: Annotated[str, Field(max_length=64)]
    label: Label
    format: Format = "text"
    #: A footer row adding up the column.
    total: bool = False
    #: A bar behind each value, sized to the column's largest.
    bar: bool = False
    #: Negative values in red and positive in green.
    signed: bool = False


class TableBlock(_Block):
    type: Literal["table"]
    columns: list[Column] = Field(min_length=1, max_length=MAX_COLUMNS)
    rows: list[dict[str, str | Number | None]] = Field(max_length=MAX_ROWS)
    #: The column the table opens sorted by, largest first unless `sort_ascending`.
    sort_by: Annotated[str, Field(max_length=64)] | None = None
    sort_ascending: bool = False
    #: Rows shown at a time; the rest are a page away.
    page_size: Annotated[int, Field(ge=5, le=200)] = 25

    @model_validator(mode="after")
    def _known_cells(self) -> TableBlock:
        keys = {c.key for c in self.columns}
        if self.sort_by is not None and self.sort_by not in keys:
            raise ValueError("sort_by must be one of the columns")
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


Block = Annotated[
    Union[
        KpiBlock, ChartBlock, HeatmapBlock, TreemapBlock, SankeyBlock, WaterfallBlock, ProgressBlock, TableBlock,
        TextBlock,
    ],
    Field(discriminator="type"),
]


class ReportView(_Model):
    #: The currency `currency` values are in; the viewer's when left out.
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    blocks: list[Block] = Field(min_length=1, max_length=MAX_BLOCKS)

    @model_validator(mode="after")
    def _bounded_points(self) -> ReportView:
        points = sum(len(block.x) * len(block.series) for block in self.blocks if isinstance(block, ChartBlock))
        if points > MAX_TOTAL_POINTS:
            raise ValueError(f"a view draws at most {MAX_TOTAL_POINTS} chart points across its charts")
        return self


TreemapItem.model_rebuild()


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
