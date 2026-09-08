"""Tests of what the render self-grid asserts about the html target.

Each test here is the Python spelling of one cell of that grid's `html` column,
run against the demo fixture the self-grid renders — the same two manifests and
the same seeded run file — so a broken renderer is caught before a self-grid run
has to build a snapshot from it.

The document is read with a parser rather than searched as text. An attribute
found by a regular expression would pass for markup that no browser and no
consumer could query, and a reason containing an apostrophe would break a
substring match while the render was correct.
"""

import shutil
from html.parser import HTMLParser
from pathlib import Path

import pytest

from witnessed import state
from witnessed.cli import load_grids
from witnessed.render import build_view
from witnessed.render.html_render import render

FIXTURE = Path(__file__).resolve().parents[1] / ".fixtures" / "demo"

MANIFEST_REASON = (
    "b is defined only over x; the y variant names a coordinate the subject "
    "cannot occupy, so no verifier could ever witness it"
)
VERIFIER_REASON = (
    "c is absent from the x variant by choice; the corpus reports the omission "
    "rather than a human restating it"
)
WITNESSED_REV = "a1b2c3d"


class Document(HTMLParser):
    """The rendered document as the structure a consumer queries it for.

    Sections and cells are keyed by the ids the render writes onto them, which
    is the same spelling the run file and the export use, so a test names a cell
    the way every other surface does.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[dict[str, str]] = []
        self.cells: dict[tuple[str, str], dict[str, str]] = {}
        self.external: list[tuple[str, str]] = []
        self.text: list[str] = []
        self._grid: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        if tag == "section":
            self._grid = attributes.get("data-grid", "")
            self.sections.append(attributes)
        elif tag == "td":
            self.cells[(self._grid or "", attributes.get("data-cell", ""))] = attributes
        for name in ("src", "href"):
            if name in attributes:
                self.external.append((tag, attributes[name]))

    def handle_data(self, data: str) -> None:
        self.text.append(data)

    def body(self) -> str:
        return "".join(self.text)


def _markup(tmp_path: Path) -> str:
    """The demo fixture rendered against its seeded records.

    The seed is copied into a state directory of the test's own, exactly as the
    self-grid's `setup` does, so that neither the fixture's committed seed nor a
    project's run file is written by a render test.
    """
    shutil.copy(FIXTURE / "seed-runs.json", tmp_path / state.RUN_FILE_NAME)
    grids = [grid for grid, _ in load_grids([str(FIXTURE)])]
    return render(build_view(grids, state.load(tmp_path)))


@pytest.fixture
def document(tmp_path: Path) -> Document:
    parsed = Document()
    parsed.feed(_markup(tmp_path))
    return parsed


def test_five_distinct_states_appear(document: Document):
    states = {
        attributes["data-state"]
        for (grid, _), attributes in document.cells.items()
        if grid == "alpha"
    }
    assert states == {"witnessed", "failed", "errored", "excepted", "unknown"}


def test_an_excepted_cell_carries_its_reason_as_the_title(document: Document):
    assert document.cells[("alpha", "b/y")]["title"] == MANIFEST_REASON
    assert document.cells[("alpha", "c/x")]["title"] == VERIFIER_REASON


def test_every_excepted_cell_has_a_title_and_no_other_cell_does(document: Document):
    titled = {key for key, attributes in document.cells.items() if "title" in attributes}
    excepted = {
        key for key, attributes in document.cells.items() if attributes["data-state"] == "excepted"
    }
    assert titled == excepted == {("alpha", "b/y"), ("alpha", "c/x")}


def test_a_regression_is_flagged_and_names_the_revision(document: Document):
    regressed = document.cells[("alpha", "a/y")]
    assert regressed["data-state"] == "failed"
    assert regressed["data-regression"] == "true"
    assert regressed["data-last-witnessed-rev"] == WITNESSED_REV
    assert WITNESSED_REV in document.body()


def test_a_gap_that_was_never_witnessed_is_not_marked_as_a_regression(document: Document):
    gap = document.cells[("alpha", "b/x")]
    assert gap["data-state"] == "errored"
    assert "data-regression" not in gap
    assert "data-last-witnessed-rev" not in gap


def test_a_witnessed_cell_does_not_carry_a_revision(document: Document):
    """`solo/single` is witnessed and holds a `last_witnessed`. A revision in the
    markup must mean a cell that emptied, or its presence says nothing."""
    witnessed = document.cells[("beta", "solo/single")]
    assert witnessed["data-state"] == "witnessed"
    assert "data-regression" not in witnessed
    assert "data-last-witnessed-rev" not in witnessed


def test_each_grid_section_carries_its_own_policy(document: Document):
    policies = {
        section["data-grid"]: (section["data-on-gap"], section["data-on-regression"])
        for section in document.sections
    }
    assert policies == {"alpha": ("report", "fail"), "beta": ("fail", "fail")}


def test_each_grid_policy_is_on_the_page_as_well_as_on_the_section(document: Document):
    body = document.body()
    assert "gap report" in body
    assert "gap fail" in body
    assert body.count("regression fail") == 2


def test_two_grids_render_as_two_sections(document: Document):
    assert [section["data-grid"] for section in document.sections] == ["alpha", "beta"]


def test_every_cell_of_the_product_is_drawn(document: Document):
    assert len([key for key in document.cells if key[0] == "alpha"]) == 6
    assert len([key for key in document.cells if key[0] == "beta"]) == 1


def test_no_state_token_appears_outside_a_cell(tmp_path: Path):
    """A consumer greps this render. A stylesheet spelling `data-state="failed"`
    would put all five states into a document whose table was empty, so the
    `data-` tokens are counted against the cells that may carry them."""
    markup = _markup(tmp_path)
    assert markup.count('data-state="') == 7
    assert markup.count('data-regression="true"') == 1
    assert markup.count("data-last-witnessed-rev=") == 1
    assert markup.count("data-on-gap=") == 2


def test_the_document_stands_alone(tmp_path: Path):
    """A render travels. A document that fetches an asset is a document that
    renders differently, or not at all, wherever it lands."""
    markup = _markup(tmp_path)
    parsed = Document()
    parsed.feed(markup)
    assert parsed.external == []
    assert markup.startswith("<!doctype html>")
    assert not markup.endswith("\n")


def test_the_same_inputs_render_identically(tmp_path: Path):
    assert _markup(tmp_path) == _markup(tmp_path)
