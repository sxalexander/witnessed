"""The html target: one document a browser opens with nothing else beside it.

A render travels — pasted into a review, attached to a build, opened from a
share. Every style rule is therefore inline and no asset is referenced, so the
file that leaves a machine is the whole of what a reader receives.

The document is addressable as well as legible. A grid is a `<section>`
carrying its policy on `data-on-gap` and `data-on-regression`; a cell is a
`<td>` carrying its state on `data-state`, its closure on `title`, and its
regression on `data-regression` with the revision the cell was last true at.
A consumer therefore queries this render with a selector instead of a regular
expression over prose, and the prose beside each attribute is free to change.

The revision is written only on a regressed cell. A witnessed cell also knows
when it was last true, so emitting the revision wherever it exists would make
its presence say nothing; gating it on the regression makes *a revision in the
markup* mean *a cell that emptied*.

The view is the only input. No timestamp and no ordering of its own enter the
document, so the same run renders byte-identically wherever it is drawn.
"""

from collections.abc import Sequence
from html import escape

from witnessed.render import CellView, GridView

STYLE = """
:root { color-scheme: light dark; }
body { font: 15px/1.5 ui-sans-serif, system-ui, sans-serif; margin: 2rem; }
h1 { font-size: 1.3rem; margin: 0 0 1.5rem; }
section { margin: 0 0 2.5rem; }
h2 { font-size: 1.05rem; margin: 0 0 .2rem; }
.claim { margin: 0 0 .6rem; opacity: .75; }
.policy { margin: 0 0 .8rem; font-size: .85rem; letter-spacing: .02em; }
.policy code { padding: .1em .35em; border: 1px solid; border-radius: .25em; }
table { border-collapse: collapse; }
th, td { border: 1px solid; padding: .45rem .7rem; text-align: left; }
th { font-weight: 600; }
td { vertical-align: top; }
.state { display: block; }
.rev { display: block; font-size: .8rem; opacity: .8; }
.s-witnessed { background: #1f7a3f22; }
.s-failed { background: #b3261e22; }
.s-errored { background: #7a3fb322; }
.s-excepted { background: #7a76701a; }
.s-unknown { background: transparent; }
.regressed { outline: 2px solid #b3261e; outline-offset: -2px; }
"""
"""Enough style to read a grid at a glance, and no more.

Selectors match on class, never on `data-state` or `data-regression`. Those
attributes are the record of what was observed, and a stylesheet that spelled
them would put every one of their values into the document whether or not a
cell held it — enough for a consumer grepping this render to read five states
off an empty table. A `data-` token in this document appears on a cell or
nowhere.
"""


def render(view: Sequence[GridView]) -> str:
    """The whole view as one HTML document, without a trailing newline.

    `<section>` is reserved for grids: the count of sections in the document is
    the count of grids in the run, so a wrapper element is a `<main>`.
    """
    sections = "\n".join(_grid(grid) for grid in view)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>witnessed</title>\n"
        f"<style>{STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        "<main>\n"
        "<h1>witnessed</h1>\n"
        f"{sections}\n"
        "</main>\n"
        "</body>\n"
        "</html>"
    )


def _grid(grid: GridView) -> str:
    """One grid as a section, its policy both queryable and on the page.

    The same red cell means different things in two grids, so the policy is
    written where a reader sees it as well as onto the section, rather than
    being left for a consumer who knows to look at the attributes.
    """
    return (
        f'<section data-grid="{escape(grid.id, quote=True)}"'
        f' data-on-gap="{escape(grid.policy.on_gap, quote=True)}"'
        f' data-on-regression="{escape(grid.policy.on_regression, quote=True)}">\n'
        f"<h2>{escape(grid.id)}</h2>\n"
        f'<p class="claim">{escape(grid.claim)}</p>\n'
        f'<p class="policy">gap <code>{escape(grid.policy.on_gap)}</code>'
        f" &middot; regression <code>{escape(grid.policy.on_regression)}</code></p>\n"
        f"{_table(grid)}\n"
        "</section>"
    )


def _table(grid: GridView) -> str:
    """The grid drawn over its declared axes, dimensions down and variants across.

    Cells are looked up by coordinate rather than consumed in order, so a table
    cannot silently shift a cell one column left if the view's ordering and the
    axes ever disagree.
    """
    by_coordinate = {(cell.dimension, cell.variant): cell for cell in grid.cells}
    header = "".join(f"<th>{escape(variant)}</th>" for variant in grid.variants)
    rows = "\n".join(
        f'<tr><th scope="row">{escape(dimension)}</th>'
        + "".join(_cell(by_coordinate[(dimension, variant)]) for variant in grid.variants)
        + "</tr>"
        for dimension in grid.dimensions
    )
    return (
        "<table>\n"
        f'<thead><tr><th scope="col"></th>{header}</tr></thead>\n'
        f"<tbody>\n{rows}\n</tbody>\n"
        "</table>"
    )


def _cell(cell: CellView) -> str:
    """One cell as a `<td>` whose attributes carry everything the view resolved.

    `title` is the reason and nothing else, so a consumer reading the attribute
    reads what the manifest or the verifier wrote rather than a decoration this
    render added around it. A regression whose record names no revision carries
    the flag without the revision, because the view holds no revision to name.
    """
    classes = [f"s-{cell.state}"]
    attributes = f' data-state="{escape(str(cell.state), quote=True)}"'
    attributes += f' data-cell="{escape(cell.id, quote=True)}"'
    if cell.reason:
        attributes += f' title="{escape(cell.reason, quote=True)}"'
    body = f'<span class="state">{escape(str(cell.state))}</span>'
    if cell.regression:
        classes.append("regressed")
        attributes += ' data-regression="true"'
        said = []
        if cell.last_witnessed_at is not None:
            stamp = cell.last_witnessed_at.date().isoformat()
            attributes += f' data-last-witnessed-at="{escape(stamp, quote=True)}"'
            said.append(stamp)
        if cell.last_witnessed_rev:
            rev = cell.last_witnessed_rev
            attributes += f' data-last-witnessed-rev="{escape(rev, quote=True)}"'
            said.append(rev)
        if said:
            body += f'<span class="rev">last witnessed {escape(" ".join(said))}</span>'
    return f'<td class="{escape(" ".join(classes), quote=True)}"{attributes}>{body}</td>'


__all__ = ["STYLE", "render"]
