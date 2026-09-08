"""Tests of what the render self-grid asserts about the tui target.

Each test is the Python spelling of one cell of that grid's `tui` column, run
against the demo fixture itself rather than a rendered snapshot, so a broken
renderer is caught before a self-grid run has to build one. The fixture is the
only input: two manifests producing all five states at known coordinates, and a
committed run file that makes one of them a regression rather than a gap.

Glyph assertions read the matrix rows and nothing else. The key line at the top
of the render names every glyph, so a renderer that drew each cell identically
would satisfy a search of the whole page while saying nothing.
"""

from pathlib import Path

from witnessed import manifest
from witnessed.model import CellState
from witnessed.render import GridView, build_view
from witnessed.render.tui import GLYPHS, REGRESSION, WIDTH, render
from witnessed.state import RunFile

FIXTURE = Path(__file__).resolve().parents[1] / ".fixtures" / "demo"

ALPHA_DIMENSIONS = ("a", "b", "c")
ALPHA_VARIANTS = ("x", "y")
WITNESSED_REV = "a1b2c3d"

MANIFEST_REASON = "no verifier could ever witness it"
"""A phrase found only in alpha's manifest exception for `b/y`."""

VERIFIER_REASON = "the corpus reports the omission"
"""A phrase found only in the exception the verifier returned for `c/x`."""

EXPECTED_STATES = {
    "a/x": CellState.WITNESSED,
    "a/y": CellState.FAILED,
    "b/x": CellState.ERRORED,
    "b/y": CellState.EXCEPTED,
    "c/x": CellState.EXCEPTED,
    "c/y": CellState.UNKNOWN,
}
"""What the fixture produces at each coordinate of alpha, by construction.

`b/y` is closed in the manifest and `c/x` by the verifier, so the two routes to
excepted are both exercised. `c/y` has no record, which is the only way unknown
can be produced.
"""

MARKS = {glyph + suffix for glyph in GLYPHS.values() for suffix in ("", REGRESSION)}


def view() -> list[GridView]:
    """The demo fixture's two grids, resolved against its committed run file."""
    grids = [manifest.load(path) for path in sorted(FIXTURE.glob("*.grid.yaml"))]
    seeded = (FIXTURE / "seed-runs.json").read_text(encoding="utf-8")
    return build_view(grids, RunFile.model_validate_json(seeded).grids)


def rendered() -> str:
    return render(view())


def blocks(text: str) -> list[str]:
    """The page split into one chunk per grid, the key line dropped."""
    return text.split("=" * WIDTH)[1:]


def matrix(block: str, dimensions=ALPHA_DIMENSIONS, variants=ALPHA_VARIANTS) -> dict[str, str]:
    """The marks a block draws, by coordinate.

    A row is recognised by its label and by every other field being a mark, so
    a grid's claim or a note line cannot be mistaken for a row of the table.
    """
    rows = {}
    for line in block.splitlines():
        fields = line.split()
        if not fields or fields[0] not in dimensions or len(fields) != len(variants) + 1:
            continue
        if all(field in MARKS for field in fields[1:]):
            rows[fields[0]] = fields[1:]
    return {
        f"{dimension}/{variant}": rows[dimension][index]
        for dimension in dimensions
        for index, variant in enumerate(variants)
    }


def alpha() -> dict[str, str]:
    return matrix(blocks(rendered())[0])


def test_every_state_has_a_glyph_and_no_two_states_share_one():
    assert set(GLYPHS) == set(CellState)
    assert len(set(GLYPHS.values())) == len(CellState) == 5
    assert REGRESSION not in GLYPHS.values()


def test_five_distinct_glyphs_appear_in_the_matrix_one_per_state():
    drawn = alpha()
    assert {cell: mark.rstrip(REGRESSION) for cell, mark in drawn.items()} == {
        cell: GLYPHS[state] for cell, state in EXPECTED_STATES.items()
    }
    assert len({mark.rstrip(REGRESSION) for mark in drawn.values()}) == 5


def test_an_excepted_cell_shows_its_state_and_withholds_its_reason():
    """80 columns has no room for prose, which is why `except-reason/tui` is
    excepted as not-applicable and the json target carries the reason instead."""
    drawn = alpha()
    assert drawn["b/y"] == GLYPHS[CellState.EXCEPTED]
    assert drawn["c/x"] == GLYPHS[CellState.EXCEPTED]
    page = rendered()
    assert MANIFEST_REASON not in page
    assert VERIFIER_REASON not in page


def test_a_regressed_cell_is_marked_apart_from_a_gap():
    drawn = alpha()
    assert drawn["a/y"] == GLYPHS[CellState.FAILED] + REGRESSION
    assert drawn["a/y"] != GLYPHS[CellState.FAILED]
    assert drawn["b/x"] == GLYPHS[CellState.ERRORED]


def test_a_regressed_cell_names_the_revision_it_was_last_witnessed_at():
    named = [line for line in blocks(rendered())[0].splitlines() if "a/y" in line]
    assert len(named) == 1
    assert WITNESSED_REV in named[0]


def test_a_cell_that_was_never_witnessed_names_no_revision():
    assert [line for line in blocks(rendered())[0].splitlines() if "b/x" in line] == []


def test_each_grid_shows_its_id_and_its_policy_in_a_header_line():
    first, second = blocks(rendered())
    assert "alpha  on_gap: report  on_regression: fail" in first.splitlines()
    assert "beta  on_gap: fail  on_regression: fail" in second.splitlines()


def test_two_grids_render_as_two_blocks_each_with_its_own_table():
    first, second = blocks(rendered())
    assert set(matrix(first)) == set(EXPECTED_STATES)
    assert matrix(second, ("solo",), ("single",)) == {"solo/single": GLYPHS[CellState.WITNESSED]}


def test_no_line_exceeds_eighty_columns():
    assert max(len(line) for line in rendered().splitlines()) <= WIDTH


def test_a_grid_wider_than_the_budget_keeps_its_marks():
    wide = manifest.load(FIXTURE / "alpha.grid.yaml").model_copy(
        update={"variants": [f"variant-{index:02d}" for index in range(30)], "except_": {}}
    )
    page = render(build_view([wide], {}))
    assert max(len(line) for line in page.splitlines()) <= WIDTH
    assert GLYPHS[CellState.UNKNOWN] in page


def test_the_same_view_renders_identically():
    assert render(view()) == render(view())


def test_a_page_with_no_grids_says_so():
    assert render([]).strip()
