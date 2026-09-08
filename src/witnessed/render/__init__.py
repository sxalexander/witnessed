"""The one reading of a grid that every render target consumes.

Four targets must agree about what a cell is: the same five states, the same
regression, the same reason behind a closed cell. They agree by not deciding —
`build_view` walks the model once and hands each renderer a structure that is
already resolved, so a renderer never calls `cell_state`, never unpacks an
`Observation.result` union, and cannot drift from the precedence the spec sets.

A view is flat where the model is layered. `last_witnessed_rev` is a string or
nothing, because the revision a cell was last true at is the whole of what a
render needs from a prior observation; a `last_witnessed` whose `rev` is null
is therefore indistinguishable from none at all.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from witnessed.model import (
    CellRecord,
    CellState,
    Errored,
    Grid,
    Policy,
    Verdict,
    cell_key,
    cell_state,
    exception_for,
    is_regression,
)

RunState = Mapping[str, Mapping[str, CellRecord]]
"""Records by grid id, then by cell key: the `grids` object of the run file."""


@dataclass(frozen=True, slots=True)
class CellView:
    """One cell, resolved: its state, why it is closed, and what it was.

    `id` is built here with `cell_key` so that the run file, the export, and
    all four targets spell a coordinate the same way without each rebuilding
    the string. `evidence` carries an errored cell's message, which is the only
    place a render can learn what the runner could not do.
    """

    id: str
    dimension: str
    variant: str
    state: CellState
    reason: str | None = None
    regression: bool = False
    last_witnessed_rev: str | None = None
    evidence: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GridView:
    """One grid, resolved: its claim, its axes, its policy, and every cell.

    The axes travel alongside the cells because a target that draws a table
    needs the row and column order the manifest declared, and the cell list
    alone cannot supply it. Policy travels because the same red cell means
    different things in two grids, so every target must surface it.
    """

    id: str
    claim: str
    policy: Policy
    dimensions: list[str]
    variants: list[str]
    cells: list[CellView]


def build_view(grids: Iterable[Grid], state: RunState) -> list[GridView]:
    """Resolve grids against the run file into the structure renderers consume.

    Grids are ordered by id and cells row-major over the declared axes, so the
    same inputs render byte-identically wherever discovery happened to find the
    manifests. A record whose coordinate is outside a grid's product is not
    reached: cells are generated from the axes, which are the only declaration
    of the grid's shape.
    """
    views: list[GridView] = []
    for grid in sorted(grids, key=lambda g: g.id):
        records = state.get(grid.id, {})
        views.append(
            GridView(
                id=grid.id,
                claim=grid.claim,
                policy=grid.policy,
                dimensions=list(grid.dimensions),
                variants=list(grid.variants),
                cells=[_cell(grid, d, v, records.get(cell_key(d, v))) for d, v in grid.cells()],
            )
        )
    return views


def _cell(grid: Grid, dimension: str, variant: str, record: CellRecord | None) -> CellView:
    state = cell_state(grid, dimension, variant, record)
    closure = exception_for(grid, dimension, variant, record)
    last_witnessed = record.last_witnessed if record is not None else None
    return CellView(
        id=cell_key(dimension, variant),
        dimension=dimension,
        variant=variant,
        state=state,
        reason=closure.reason if closure is not None else None,
        regression=is_regression(state, record),
        last_witnessed_rev=last_witnessed.rev if last_witnessed is not None else None,
        evidence=_evidence(record),
    )


def _evidence(record: CellRecord | None) -> dict:
    """What the verifier reported, or what stopped it reporting.

    An errored cell has no verdict to carry evidence, and its message is the
    only account of why the runner learned nothing, so it travels here rather
    than being dropped.
    """
    if record is None:
        return {}
    result = record.current.result
    if isinstance(result, Verdict):
        return dict(result.evidence)
    if isinstance(result, Errored):
        return {"error": result.error}
    return {}


__all__ = ["CellView", "GridView", "RunState", "build_view"]
