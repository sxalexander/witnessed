"""The md target: a grid as a table that reads where the work is reviewed.

Markdown is the one target a maintainer and a reviewer read in the same place,
which is why it carries what an eighty-column terminal has to drop: the reason
behind a closed cell and the revision a regressed cell was last true at. It
reads only the view, so it cannot disagree with the other three targets about
what a cell is.

A cell shows its state word, and a regressed cell shows that word *and* its
regression. The marker is added rather than substituted because a grid whose
only failed cell is a regression would otherwise never spell `failed`, and the
five states the model holds apart would render as four.

Prose does not fit in a table cell, so a closed cell carries a footnote
reference and its reason is the footnote text. Definitions are written under
their own grid's table rather than gathered at the end of the document, so one
grid is one block that survives being quoted on its own.
"""

from collections.abc import Sequence

from witnessed.render import CellView, GridView

REGRESSION_MARK = "regressed"
"""What distinguishes a red cell that is news from a red cell that is a rollout.

Written beside the state word, never in place of it.
"""


def render(view: Sequence[GridView]) -> str:
    """The whole view as GitHub-flavoured markdown, without a trailing newline.

    Grids are separated by a blank line and nothing else: each opens with its
    own H2, so the count of headings is the count of grids and no separator
    rule has to be read as structure.
    """
    return "\n\n".join(_grid(grid) for grid in view)


def _grid(grid: GridView) -> str:
    """One grid as heading, policy, claim, table, and the reasons its cells cite.

    The policy sits on the line directly under the heading because the same red
    cell means different things in two grids: a reader who has found the grid
    has found what its red costs, without scrolling to a legend.
    """
    blocks = [f"## {grid.id}\n{_policy(grid)}", grid.claim, _table(grid)]
    footnotes = _footnotes(grid)
    if footnotes:
        blocks.append(footnotes)
    return "\n\n".join(blocks)


def _policy(grid: GridView) -> str:
    """The grid's policy, spelled with the field names the manifest uses.

    A reader who sees `on_gap` here can search the manifest for the line that
    set it; a rendered word like *locked* could not be traced back to anything.
    """
    return f"policy: `on_gap: {grid.policy.on_gap}`, `on_regression: {grid.policy.on_regression}`"


def _table(grid: GridView) -> str:
    """The grid's product as rows of dimensions over columns of variants.

    Cells are looked up by coordinate rather than consumed in order, so the
    table's shape is the manifest's axes and a view that ordered its cells
    differently would still render into the right squares.
    """
    by_coordinate = {(cell.dimension, cell.variant): cell for cell in grid.cells}
    rows = [
        _row(["", *grid.variants]),
        _row([":--"] * (len(grid.variants) + 1)),
    ]
    for dimension in grid.dimensions:
        rows.append(
            _row(
                [
                    dimension,
                    *(
                        _cell(grid.id, by_coordinate[(dimension, variant)])
                        for variant in grid.variants
                    ),
                ]
            )
        )
    return "\n".join(rows)


def _cell(grid_id: str, cell: CellView) -> str:
    """One square: the state word, what it cites, and what it lost.

    Everything a square carries is short by construction — a word, a footnote
    reference, a revision — because a table cell admits no line break and prose
    put here would be prose a reader cannot see.
    """
    text = str(cell.state)
    if cell.reason:
        text += f"[^{_label(grid_id, cell)}]"
    if cell.regression:
        text += f", {REGRESSION_MARK}"
        moment = cell.last_witnessed_at
        stamp = moment.date().isoformat() if moment is not None else None
        if stamp and cell.last_witnessed_rev:
            text += f" (last witnessed {stamp} `{cell.last_witnessed_rev}`)"
        elif stamp:
            text += f" (last witnessed {stamp})"
        elif cell.last_witnessed_rev:
            text += f" (last witnessed `{cell.last_witnessed_rev}`)"
    return text


def _footnotes(grid: GridView) -> str:
    """The reason behind every closed cell of one grid, in the grid's cell order.

    A footnote is emitted for a reason rather than for a state, so a closure
    declared in the manifest and one returned by a verifier reach the reader by
    the same route: the render does not know which source closed a cell, and a
    reader following the reference does not need to.
    """
    return "\n".join(
        f"[^{_label(grid.id, cell)}]: {_flat(cell.reason)}" for cell in grid.cells if cell.reason
    )


def _label(grid_id: str, cell: CellView) -> str:
    """A footnote's name: the cell's address, in the characters a label may hold.

    Definitions from every grid share one namespace once the document is
    rendered, so the grid id is part of the name. Coordinates are joined with
    `-` rather than the `/` a cell is otherwise spelled with, to stay inside the
    characters every markdown implementation accepts in a label.
    """
    return f"{grid_id}-{cell.dimension}-{cell.variant}"


def _flat(reason: str) -> str:
    """A reason as one line.

    A footnote definition ends at its first unindented newline, so a reason
    written across several lines would render with its tail orphaned outside
    the footnote.
    """
    return " ".join(reason.split())


def _row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"
