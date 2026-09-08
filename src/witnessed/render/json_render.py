"""The json target: the reference render, and the only machine-readable one.

Where the terminal has eighty columns and must drop prose, json carries every
field the view resolved — an excepted cell's reason, a regressed cell's
revision, each grid's policy — so that whatever a narrower target omits is
still reachable from the same run. It reads only the view, never the model,
which is what lets the other three targets be written against one structure.

Ordering is the view's: grids by id, cells row-major over the declared axes.
Keys are written in a fixed order rather than sorted, so a diff of two runs
shows what changed about a cell instead of where a field moved to.
"""

import json
from collections.abc import Sequence

from witnessed.render import CellView, GridView


def render(view: Sequence[GridView]) -> str:
    """The whole view as one JSON document, without a trailing newline.

    Grids are keyed by id rather than listed, because every other surface — the
    run file, a `--grid` selection, a cell's node id — addresses a grid by id.
    """
    return json.dumps({"grids": {grid.id: _grid(grid) for grid in view}}, indent=2)


def _grid(grid: GridView) -> dict:
    """A grid as its policy and its cells.

    The axes are not restated: totality is generated from them, so the cell
    list already spells every coordinate the grid asserts, row-major.
    """
    return {
        "policy": {
            "on_gap": grid.policy.on_gap,
            "on_regression": grid.policy.on_regression,
        },
        "cells": [_cell(cell) for cell in grid.cells],
    }


def _cell(cell: CellView) -> dict:
    """One cell, with every key present whether or not it carries a value.

    A uniform shape keeps a consumer's query total: `.cells[] | select(.regression)`
    holds for a grid that has never regressed, and a null `reason` says the cell
    is not closed rather than that the render forgot to say why.
    """
    return {
        "id": cell.id,
        "state": str(cell.state),
        "reason": cell.reason,
        "regression": cell.regression,
        "last_witnessed": _last_witnessed(cell),
        "evidence": cell.evidence,
    }


def _last_witnessed(cell) -> dict | None:
    """A prior observation as a time and, where a repository supplies one, a revision.

    The time is present whenever the cell was ever witnessed; the revision is
    absent outside version control, so a consumer reads `at` and treats `rev`
    as an enrichment.
    """
    if cell.last_witnessed_at is None and cell.last_witnessed_rev is None:
        return None
    return {
        "at": cell.last_witnessed_at.isoformat() if cell.last_witnessed_at else None,
        "rev": cell.last_witnessed_rev,
    }
