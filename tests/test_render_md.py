"""Tests of what the render self-grid asserts about the md target.

Each test is the Python spelling of one cell of that grid's `md` column, run
against the demo fixture the self-grid itself renders, so a broken renderer is
caught before a self-grid run has to build a snapshot to find it.

The fixture is read from disk rather than restated here. The unit test and the
self-grid therefore read the same bytes, and a fixture edited into no longer
producing all five states fails here rather than surprising a snapshot.
"""

import re
from pathlib import Path

import pytest

from witnessed import manifest, state
from witnessed.render import build_view
from witnessed.render.md_render import render

FIXTURE = Path(__file__).resolve().parent.parent / ".fixtures" / "demo"

REGRESSED_CELL = "a/y"
"""Failed, and seeded with a `last_witnessed`, so it is news rather than rollout."""

GAP_CELL = "b/x"
"""Errored with no `last_witnessed`: red, and not a regression."""

REGRESSED_REV = "a1b2c3d"
"""The revision `a/y` was last witnessed at, per the seeded run file."""


@pytest.fixture(scope="module")
def document() -> str:
    grids = [manifest.load(path) for path in sorted(FIXTURE.glob("*.grid.yaml"))]
    records = state.RunFile.model_validate_json(
        (FIXTURE / "seed-runs.json").read_text(encoding="utf-8")
    ).grids
    return render(build_view(grids, records))


def blocks(document: str) -> dict[str, str]:
    """The document split at its H2 headings, keyed by grid id.

    Splitting on the heading is how a reader finds a grid, so the tests locate
    one the same way rather than by counting blank lines.
    """
    found: dict[str, str] = {}
    for part in re.split(r"^## ", document, flags=re.MULTILINE)[1:]:
        heading, _, body = part.partition("\n")
        found[heading.strip()] = body
    return found


def squares(block: str) -> dict[str, str]:
    """Coordinate to rendered text, read back out of one grid's table.

    Reading the table rather than the source view is the point: what a claim
    asserts is what a person opening the document can see.
    """
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in block.splitlines()
        if line.startswith("|")
    ]
    variants = rows[0][1:]
    found: dict[str, str] = {}
    for row in rows[2:]:
        assert len(row) - 1 == len(variants), f"row `{row[0]}` does not span every variant"
        for variant, text in zip(variants, row[1:]):
            found[f"{row[0]}/{variant}"] = text
    return found


def footnotes(document: str) -> dict[str, str]:
    """Every footnote definition, keyed by label."""
    return {
        match["label"]: match["text"].strip()
        for match in re.finditer(
            r"^\[\^(?P<label>[^\]]+)\]:(?P<text>.*)$", document, flags=re.MULTILINE
        )
    }


def word(square: str) -> str:
    """The state word a square opens with.

    A square is its state followed by whatever it cites or lost, so the leading
    run of letters is the state and a marker written beside it cannot be read
    as one.
    """
    opening = re.match(r"[a-z]+", square)
    assert opening is not None, f"{square} does not open with a state word"
    return opening[0]


def cited(square: str) -> str:
    """The footnote label a square points at."""
    reference = re.search(r"\[\^([^\]]+)\]", square)
    assert reference is not None, f"{square} carries no footnote reference"
    return reference[1]


def test_five_distinct_state_words_appear(document):
    """cell-state: the five states the model holds apart survive the render."""
    words = {word(square) for square in squares(blocks(document)["alpha"]).values()}
    assert words == {"witnessed", "failed", "errored", "excepted", "unknown"}


def test_an_excepted_cell_carries_a_footnote_whose_text_is_its_reason(document):
    """except-reason: a closed cell's reason is reachable from the cell."""
    alpha = squares(blocks(document)["alpha"])
    definitions = footnotes(document)
    excepted = [square for square in alpha.values() if square.startswith("excepted")]
    assert len(excepted) == 2
    for square in excepted:
        assert definitions[cited(square)]


def test_a_manifest_exception_and_a_verifier_exception_both_reach_their_reason(document):
    """Both routes to a closed cell arrive at the reader by the same one.

    `b/y` is closed in alpha's manifest and `c/x` by the verifier that read it.
    The render does not know which, and a reader following the reference does
    not need to.
    """
    alpha = squares(blocks(document)["alpha"])
    definitions = footnotes(document)
    assert definitions[cited(alpha["b/y"])].startswith("b is defined only over x;")
    assert definitions[cited(alpha["c/x"])].startswith("c is absent from the x variant by choice;")


def test_a_regression_is_marked_distinctly_from_a_gap_and_names_the_revision(document):
    """regression: a red cell that was once witnessed says so, and says from where."""
    alpha = squares(blocks(document)["alpha"])
    assert "regressed" in alpha[REGRESSED_CELL]
    assert REGRESSED_REV in alpha[REGRESSED_CELL]
    assert "regressed" not in alpha[GAP_CELL]
    assert REGRESSED_REV not in alpha[GAP_CELL]


def test_a_regressed_cell_keeps_its_state_word(document):
    """The marker is added to the state, never substituted for it.

    Alpha's only failed cell is the regression, so a render that replaced the
    word would spell four states while claiming five.
    """
    assert squares(blocks(document)["alpha"])[REGRESSED_CELL].startswith("failed")


def test_a_witnessed_cell_that_was_witnessed_before_is_not_a_regression(document):
    """`a/x` carries a `last_witnessed` and is still green: a regression is a loss."""
    assert squares(blocks(document)["alpha"])["a/x"] == "witnessed"


def test_each_grid_shows_its_policy_on_the_line_below_its_heading(document):
    """gap-policy: the same red cell costs different things in two grids."""
    found = blocks(document)
    assert found["alpha"].splitlines()[0] == "policy: `on_gap: report`, `on_regression: fail`"
    assert found["beta"].splitlines()[0] == "policy: `on_gap: fail`, `on_regression: fail`"


def test_two_grids_render_as_two_blocks(document):
    """multi-grid: one H2 per grid, each heading over a table of its own."""
    found = blocks(document)
    assert list(found) == ["alpha", "beta"]
    assert set(squares(found["alpha"])) == {"a/x", "a/y", "b/x", "b/y", "c/x", "c/y"}
    assert set(squares(found["beta"])) == {"solo/single"}


def test_the_same_inputs_render_identically(document):
    """A snapshot the self-grid greps must not differ between two runs of one state."""
    grids = [manifest.load(path) for path in sorted(FIXTURE.glob("*.grid.yaml"))]
    records = state.RunFile.model_validate_json(
        (FIXTURE / "seed-runs.json").read_text(encoding="utf-8")
    ).grids
    assert render(build_view(grids, records)) == document


def _hostile() -> str:
    grid = manifest.Grid.model_validate(
        {
            "witnessed": 1,
            "id": "docs",
            "claim": "claims <b>markup</b> [a](javascript:alert(1))\n# and a heading",
            "dimensions": ["a-b", "a"],
            "variants": ["c", "b-c"],
            "verify": "true",
            "except": {
                "a-b/c": {"why": "unimplemented", "reason": "first <img src=x onerror=alert(1)>"},
                "a/b-c": {"why": "unimplemented", "reason": "second [^docs_a-b_c]"},
            },
        }
    )
    return render(build_view([grid], {}))


def test_a_claim_and_a_reason_are_written_as_text_never_as_markup():
    """Prose from a manifest cannot inject HTML, a link, or a footnote reference."""
    document = _hostile()
    assert "<" not in document and ">" not in document
    assert "](javascript:" not in document.replace("\\]", "")
    assert (
        "claims &lt;b&gt;markup&lt;/b&gt; \\[a\\](javascript:alert(1)) # and a heading" in document
    )
    assert footnotes(document)["docs_a_b-c"] == "second \\[^docs_a-b_c\\]"


def test_two_cells_never_share_a_footnote_label():
    """Joined with `-`, cells `a-b/c` and `a/b-c` would both be labelled `docs-a-b-c`."""
    labels = footnotes(_hostile())
    assert set(labels) == {"docs_a-b_c", "docs_a_b-c"}
    assert labels["docs_a-b_c"].startswith("first ")
