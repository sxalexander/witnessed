"""Tests of what the render self-grid asserts about the json target.

Each test here is the Python spelling of one cell of that grid's `json`
column, run against fixtures rather than a rendered snapshot, so a broken
renderer is caught before a self-grid run has to build one.

The fixture is the demo fixture's shape: one grid producing all five states at
known coordinates, and a second grid with a different policy.
"""

import json
from datetime import UTC, datetime

from witnessed.model import (
    CellRecord,
    Errored,
    Excepted,
    ExceptionKind,
    Grid,
    Observation,
    Verdict,
)
from witnessed.render import build_view
from witnessed.render.json_render import render

WITNESSED_REV = "a1b2c3"

ALPHA = {
    "witnessed": 1,
    "id": "alpha",
    "claim": "Every state the render must hold apart appears once",
    "dimensions": ["a", "b", "c"],
    "variants": ["x", "y"],
    "verify": "verify/demo-verifier {dimension} {variant}",
    "except": {
        "b/y": {
            "why": ExceptionKind.NOT_APPLICABLE,
            "reason": "the subject is a layout primitive here",
        }
    },
}

BETA = {
    "witnessed": 1,
    "id": "beta",
    "claim": "A second grid carries its own policy",
    "dimensions": ["only"],
    "variants": ["one"],
    "verify": "verify/demo-verifier {dimension} {variant}",
    "policy": {"on_gap": "fail", "on_regression": "fail"},
}


def observed(result: Verdict | Excepted | Errored, rev: str = "d4e5f6") -> Observation:
    return Observation(at=datetime(2026, 9, 7, 10, 12, 3, tzinfo=UTC), rev=rev, result=result)


def state() -> dict[str, dict[str, CellRecord]]:
    """Records placing one cell in each state the five-state claim distinguishes.

    `c/y` is absent so that unknown is produced by absence, which is the only
    way it can be produced. `a/y` carries a `last_witnessed` so that it is a
    regression rather than a gap, and `beta`'s red cell carries none so that it
    is a rollout instead.
    """
    return {
        "alpha": {
            "a/x": CellRecord(
                current=observed(Verdict(ok=True, evidence={"layers": 4})),
                last_witnessed=observed(Verdict(ok=True, evidence={"layers": 4})),
            ),
            "a/y": CellRecord(
                current=observed(Verdict(ok=False, evidence={"found": "nothing"})),
                last_witnessed=observed(Verdict(ok=True), rev=WITNESSED_REV),
            ),
            "b/x": CellRecord(current=observed(Errored(error="verifier exited 1"))),
            "c/x": CellRecord(
                current=observed(
                    Excepted(
                        why=ExceptionKind.UNIMPLEMENTED,
                        reason="the corpus reports the subject is absent by choice",
                    )
                )
            ),
        },
        "beta": {"only/one": CellRecord(current=observed(Verdict(ok=False)))},
    }


def rendered() -> dict:
    grids = [Grid.model_validate(BETA), Grid.model_validate(ALPHA)]
    return json.loads(render(build_view(grids, state())))


def cells(document: dict, grid_id: str) -> dict[str, dict]:
    return {cell["id"]: cell for cell in document["grids"][grid_id]["cells"]}


def test_all_five_states_appear():
    states = {cell["state"] for cell in rendered()["grids"]["alpha"]["cells"]}
    assert states == {"witnessed", "failed", "errored", "excepted", "unknown"}


def test_every_excepted_cell_carries_a_reason():
    excepted = [c for c in rendered()["grids"]["alpha"]["cells"] if c["state"] == "excepted"]
    assert len(excepted) == 2
    assert all(cell["reason"] for cell in excepted)


def test_a_manifest_exception_and_a_verifier_exception_both_reach_their_reason():
    alpha = cells(rendered(), "alpha")
    assert alpha["b/y"]["reason"] == "the subject is a layout primitive here"
    assert alpha["c/x"]["reason"] == "the corpus reports the subject is absent by choice"


def test_a_regressed_cell_is_flagged_and_names_the_revision():
    regressed = cells(rendered(), "alpha")["a/y"]
    assert regressed["regression"] is True
    assert regressed["last_witnessed"]["rev"] == WITNESSED_REV


def test_a_red_cell_that_was_never_witnessed_is_not_a_regression():
    rollout = cells(rendered(), "beta")["only/one"]
    assert rollout["state"] == "failed"
    assert rollout["regression"] is False
    assert rollout["last_witnessed"] is None


def test_each_grid_carries_its_own_policy():
    document = rendered()
    assert document["grids"]["alpha"]["policy"] == {"on_gap": "report", "on_regression": "fail"}
    assert document["grids"]["beta"]["policy"] == {"on_gap": "fail", "on_regression": "fail"}


def test_two_grids_produce_two_keys():
    assert list(rendered()["grids"]) == ["alpha", "beta"]


def test_evidence_travels_and_an_errored_cell_carries_its_message():
    alpha = cells(rendered(), "alpha")
    assert alpha["a/x"]["evidence"] == {"layers": 4}
    assert alpha["b/x"]["evidence"] == {"error": "verifier exited 1"}


def test_the_same_inputs_render_identically():
    assert render(build_view([Grid.model_validate(ALPHA)], state())) == render(
        build_view([Grid.model_validate(ALPHA)], state())
    )
