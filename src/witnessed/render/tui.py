"""The tui target: a whole grid at a glance, in eighty columns.

A maintainer runs `check` to see what they have and what they lost, and the
answer has to fit a terminal without wrapping. Eighty columns buys one mark per
cell and little else, so the matrix is glyphs and the prose a wider target
carries — an excepted cell's reason above all — is absent here by design and
reachable from the json target instead.

Two things a matrix of glyphs cannot say on its own travel beside it. A grid's
policy is in its header line, because the same red cell means different things
in two grids. A regressed cell gets a line under the table naming the revision
it was last witnessed at, because a cell that emptied is the event the tool
exists to report and the revision is what makes it actionable.

The regression mark composes with the state glyph rather than replacing it. A
sixth glyph standing in for the fifth would make a red cell's state unreadable
exactly where a reader most needs it, and would leave a grid whose only failed
cell is a regression — the demo fixture — drawing no failed glyph at all.

Nothing here reads the model's records. The view has already resolved every
cell, so this module chooses characters and column positions and decides
nothing about what a cell is.
"""

from collections.abc import Sequence

from witnessed.model import CellState
from witnessed.render import CellView, GridView

WIDTH = 80
"""The column budget. Every line this module emits fits inside it.

Eighty is the width a terminal is assumed to have when nothing says otherwise,
and a render that wraps is a render that cannot be read as a table.
"""

GLYPHS: dict[CellState, str] = {
    CellState.WITNESSED: "+",
    CellState.FAILED: "-",
    CellState.ERRORED: "!",
    CellState.EXCEPTED: "~",
    CellState.UNKNOWN: "?",
}
"""One mark per state, chosen so that no two states share a shape.

None of these is a letter or a digit. An axis member is spelled `[a-z0-9-]`, so
a glyph can never be mistaken for a truncated row or column name — a grid whose
variants are literally `x` and `y` is the fixture this render is checked
against.
"""

REGRESSION = "*"
"""What marks a cell that was witnessed and no longer is.

Distinct from all five state glyphs, and appended to one rather than replacing
it, so a regressed cell reports both what it is and that it was better.
"""

EMPTY = "no grids found"
"""What a run over a tree holding no manifest prints.

A blank page reads as a broken command; naming the absence says the discovery
worked and found nothing.
"""

_INDENT = 2
_GUTTER = 2
_MARK_WIDTH = 2
_RULE = "=" * WIDTH
_OVERFLOW = ">"


def render(view: Sequence[GridView]) -> str:
    """The whole view as one page of text, without a trailing newline.

    Each grid is a block opened by a full-width rule, so two grids are two
    tables a reader can tell apart at a glance and a consumer can count. The
    key is written once above them all: it belongs to the vocabulary rather
    than to any one grid.
    """
    if not view:
        return EMPTY
    lines = [_key()]
    for grid in view:
        lines += ["", _RULE]
        lines += _block(grid)
    return "\n".join(_clip(line) for line in lines)


def _key() -> str:
    """Every glyph and what it means, in the order the states are declared."""
    states = "  ".join(f"{GLYPHS[state]} {state}" for state in CellState)
    return f"key  {states}  {REGRESSION} regression"


def _block(grid: GridView) -> list[str]:
    """One grid: what it claims, under what policy, and every cell of it."""
    label, columns = _widths(grid)
    marks = {(cell.dimension, cell.variant): _mark(cell) for cell in grid.cells}
    lines = [_header(grid), _line(grid.claim)]
    lines += ["", _axis_row(label, columns, grid.variants)]
    lines += [_cell_row(label, columns, grid, dimension, marks) for dimension in grid.dimensions]
    regressions = _regressions(grid)
    if regressions:
        lines += [""] + regressions
    return lines


def _header(grid: GridView) -> str:
    """The grid's id and its policy, on one line and at the left margin.

    Policy is spelled with the same key names the manifest and the json target
    use, so a reader who has seen one has seen all three.
    """
    return f"{grid.id}  on_gap: {grid.policy.on_gap}  on_regression: {grid.policy.on_regression}"


def _axis_row(label: int, columns: Sequence[int], variants: Sequence[str]) -> str:
    cells = "".join(_field(variant, width) for variant, width in zip(variants, columns))
    return " " * (_INDENT + label) + cells


def _cell_row(
    label: int,
    columns: Sequence[int],
    grid: GridView,
    dimension: str,
    marks: dict[tuple[str, str], str],
) -> str:
    cells = "".join(
        _field(marks[(dimension, variant)], width) for variant, width in zip(grid.variants, columns)
    )
    return " " * _INDENT + dimension[:label].ljust(label) + cells


def _field(text: str, width: int) -> str:
    return " " * _GUTTER + text[:width].ljust(width)


def _mark(cell: CellView) -> str:
    """A cell as the characters that stand for it."""
    return GLYPHS[cell.state] + (REGRESSION if cell.regression else "")


def _regressions(grid: GridView) -> list[str]:
    """One line per cell that was witnessed and no longer is.

    The coordinate and the revision travel together because a maintainer acting
    on a regression needs both: which cell emptied, and what to diff it against.
    """
    regressed = [cell for cell in grid.cells if cell.regression]
    if not regressed:
        return []
    width = max(len(cell.id) for cell in regressed)
    return [
        _line(f"{cell.id.ljust(width)}  regression: last witnessed {_when(cell)}")
        for cell in regressed
    ]


def _when(cell: CellView) -> str:
    """When a regressed cell was last true, and what to diff it against.

    Every run records a time, so the answer never depends on the corpus being
    a repository. A revision is appended where one exists, because a
    maintainer who has one wants to read the range rather than the date.
    """
    moment = cell.last_witnessed_at
    stamp = moment.date().isoformat() if moment is not None else "at an unrecorded time"
    return f"{stamp} (rev {cell.last_witnessed_rev})" if cell.last_witnessed_rev else stamp


def _widths(grid: GridView) -> tuple[int, list[int]]:
    """How wide the row labels and each column are, inside the budget.

    Names are given their full width while the table fits. A grid too wide for
    the budget keeps its marks and loses its column names, because a reader who
    can see the shape can recover a name from the manifest, and a reader who
    can see neither has nothing.
    """
    label = max((len(dimension) for dimension in grid.dimensions), default=1)
    columns = [max(len(variant), _MARK_WIDTH) for variant in grid.variants]
    if _table_width(label, columns) > WIDTH:
        columns = [_MARK_WIDTH] * len(grid.variants)
        label = max(1, min(label, WIDTH - _table_width(0, columns)))
    return label, columns


def _table_width(label: int, columns: Sequence[int]) -> int:
    return _INDENT + label + sum(_GUTTER + width for width in columns)


def _line(text: str) -> str:
    return " " * _INDENT + text


def _clip(line: str) -> str:
    """A line inside the budget, ending in a marker where something was cut.

    Trailing space is dropped so that two runs of the same view compare byte
    for byte whatever the widest column happened to be.
    """
    trimmed = line.rstrip()
    if len(trimmed) <= WIDTH:
        return trimmed
    return trimmed[: WIDTH - len(_OVERFLOW)] + _OVERFLOW


__all__ = ["EMPTY", "GLYPHS", "REGRESSION", "WIDTH", "render"]
