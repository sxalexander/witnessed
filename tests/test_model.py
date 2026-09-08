"""Tests of the invariants the domain model is responsible for holding.

Manifests are built with `Grid.model_validate` on a plain dict rather than by
keyword, because that is the shape a loaded YAML manifest arrives in and it is
the only shape that exercises the `except` alias.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from witnessed.model import (
    CellRecord,
    CellState,
    Errored,
    Excepted,
    ExceptionKind,
    Grid,
    Observation,
    Policy,
    Verdict,
    VerifyResultAdapter,
    cell_key,
    cell_state,
    exception_for,
    is_gap,
    is_regression,
)

MANIFEST = {
    "witnessed": 1,
    "id": "frameworks",
    "claim": "Every core component renders in every supported framework",
    "dimensions": ["button", "container"],
    "variants": ["react", "vue"],
    "verify": "verify/check-framework {dimension} {variant}",
}


def manifest(**overrides: object) -> dict:
    """A valid manifest with the named keys replaced."""
    return MANIFEST | overrides


def grid(**overrides: object) -> Grid:
    return Grid.model_validate(manifest(**overrides))


def observed(result: Verdict | Excepted | Errored, rev: str = "a1b2c3") -> Observation:
    return Observation(at=datetime(2026, 9, 7, 10, 12, 3, tzinfo=UTC), rev=rev, result=result)


# --- The manifest schema version -------------------------------------------


def test_manifest_without_a_version_does_not_load():
    payload = manifest()
    del payload["witnessed"]
    with pytest.raises(ValidationError):
        Grid.model_validate(payload)


def test_manifest_with_an_unknown_version_does_not_load():
    with pytest.raises(ValidationError):
        Grid.model_validate(manifest(witnessed=2))


# --- A manifest cannot express an observed state ---------------------------


def test_witnessed_is_a_version_not_a_state():
    """The one `witnessed` key a manifest has is `1`, and nothing else fits it."""
    with pytest.raises(ValidationError):
        Grid.model_validate(manifest(witnessed="witnessed"))


def test_manifest_carrying_a_per_cell_key_other_than_except_does_not_load():
    for key in ("cells", "states", "expect", "witness"):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            Grid.model_validate(manifest(**{key: {"button/vue": True}}))


def test_no_per_cell_verify_override():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Grid.model_validate(manifest(verify_overrides={"button/vue": "other"}))


def test_an_exception_cannot_name_an_observed_state():
    for state in ("witnessed", "failed", "errored", "unknown"):
        with pytest.raises(ValidationError):
            Grid.model_validate(
                manifest(**{"except": {"button/vue": {"why": state, "reason": "because"}}})
            )


def test_an_exception_cannot_smuggle_a_verdict():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Grid.model_validate(
            manifest(
                **{"except": {"button/vue": {"why": "unimplemented", "reason": "r", "ok": True}}}
            )
        )


def test_the_two_exception_kinds_are_the_whole_vocabulary():
    assert [kind.value for kind in ExceptionKind] == ["not-applicable", "unimplemented"]


# --- Exceptions carry a reason ---------------------------------------------


def test_an_exception_requires_a_non_empty_reason():
    for reason in (None, ""):
        with pytest.raises(ValidationError):
            Excepted.model_validate({"why": "not-applicable", "reason": reason})


def test_an_exception_requires_a_kind():
    with pytest.raises(ValidationError):
        Excepted.model_validate({"reason": "because"})


# --- Except keys live inside the product -----------------------------------


def test_an_except_key_outside_the_product_does_not_load():
    with pytest.raises(ValidationError, match=r"`except` key `pizz/altissimo` is outside"):
        Grid.model_validate(
            manifest(**{"except": {"pizz/altissimo": {"why": "not-applicable", "reason": "r"}}})
        )


def test_an_except_key_with_the_wrong_shape_does_not_load():
    for key in ("buttonvue", "button/vue/extra", "button/", "/vue", "vue/button"):
        with pytest.raises(ValidationError, match="outside the grid's product"):
            Grid.model_validate(
                manifest(**{"except": {key: {"why": "unimplemented", "reason": "r"}}})
            )


def test_an_except_key_inside_the_product_loads():
    g = grid(**{"except": {"container/vue": {"why": "not-applicable", "reason": "layout only"}}})
    assert g.except_["container/vue"].why is ExceptionKind.NOT_APPLICABLE


# --- Axes ------------------------------------------------------------------


def test_duplicate_axis_members_do_not_load():
    with pytest.raises(ValidationError, match="dimensions contains duplicate members"):
        Grid.model_validate(manifest(dimensions=["button", "button"]))
    with pytest.raises(ValidationError, match="variants contains duplicate members"):
        Grid.model_validate(manifest(variants=["vue", "react", "vue"]))


def test_an_identifier_outside_the_allowed_alphabet_does_not_load():
    for member in ("Button", "voice_input", "voice input", "voice.input", "-leading", "vue!"):
        with pytest.raises(ValidationError):
            Grid.model_validate(manifest(dimensions=[member]))
        with pytest.raises(ValidationError):
            Grid.model_validate(manifest(variants=[member]))
        with pytest.raises(ValidationError):
            Grid.model_validate(manifest(id=member))


def test_a_hyphenated_lowercase_identifier_loads():
    assert grid(dimensions=["voice-input", "text-input"]).dimensions == [
        "voice-input",
        "text-input",
    ]


def test_a_verify_command_containing_a_pipe_loads_as_written():
    command = "verify/check {dimension} {variant} | jq -c ."
    assert grid(verify=command).verify == command


def test_cells_are_the_product_of_the_axes():
    assert list(grid().cells()) == [
        ("button", "react"),
        ("button", "vue"),
        ("container", "react"),
        ("container", "vue"),
    ]


# --- Defaults --------------------------------------------------------------


def test_policy_defaults_report_gaps_and_fail_regressions():
    assert grid().policy == Policy(on_gap="report", on_regression="fail")


def test_an_unknown_policy_value_does_not_load():
    with pytest.raises(ValidationError):
        Grid.model_validate(manifest(policy={"on_gap": "explode"}))


def test_defaults_are_not_shared_between_grids():
    first, second = grid(), grid()
    first.export["template"] = "grids/prompts/implement.md.j2"
    first.except_["button/vue"] = Excepted(why=ExceptionKind.UNIMPLEMENTED, reason="r")
    assert second.export == {}
    assert second.except_ == {}


# --- What a verifier may return --------------------------------------------


def test_a_verifier_may_return_a_verdict():
    result = VerifyResultAdapter.validate_python({"ok": True, "evidence": {"velocities": 4}})
    assert isinstance(result, Verdict)
    assert result.ok is True


def test_a_verifier_may_return_an_exception():
    result = VerifyResultAdapter.validate_python(
        {"why": "not-applicable", "reason": "instrument range ends at C7"}
    )
    assert isinstance(result, Excepted)


def test_a_verifier_cannot_return_errored():
    with pytest.raises(ValidationError):
        VerifyResultAdapter.validate_python({"error": "process exited 137"})


def test_a_verifier_cannot_return_a_verdict_and_an_exception_at_once():
    with pytest.raises(ValidationError):
        VerifyResultAdapter.validate_python({"ok": True, "why": "not-applicable", "reason": "both"})


def test_a_verdict_cannot_carry_an_unknown_field():
    with pytest.raises(ValidationError):
        Verdict.model_validate({"ok": True, "state": "witnessed"})


# --- Observations and records ----------------------------------------------


def test_an_observation_holds_each_of_the_three_results():
    assert isinstance(observed(Verdict(ok=True)).result, Verdict)
    assert isinstance(observed(Excepted(why="unimplemented", reason="r")).result, Excepted)
    assert isinstance(observed(Errored(error="timed out")).result, Errored)


def test_an_observation_round_trips_through_json():
    original = observed(Verdict(ok=False, evidence={"found": "Samples/vln/sulpont/"}))
    dumped = original.model_dump_json()
    assert '"2026-09-07T10:12:03Z"' in dumped
    assert Observation.model_validate_json(dumped) == original


def test_a_record_holds_exactly_current_and_last_witnessed():
    assert set(CellRecord.model_fields) == {"current", "last_witnessed"}
    with pytest.raises(ValidationError):
        CellRecord.model_validate(
            {"current": observed(Verdict(ok=True)).model_dump(), "history": []}
        )


# --- Derived cell state ----------------------------------------------------


def test_the_render_distinguishes_five_states():
    assert [state.value for state in CellState] == [
        "witnessed",
        "failed",
        "errored",
        "excepted",
        "unknown",
    ]


def test_a_cell_with_no_record_is_unknown():
    assert cell_state(grid(), "button", "vue", None) is CellState.UNKNOWN


def test_a_verdict_that_is_ok_is_witnessed():
    record = CellRecord(current=observed(Verdict(ok=True)))
    assert cell_state(grid(), "button", "vue", record) is CellState.WITNESSED


def test_a_verdict_that_is_not_ok_is_failed():
    record = CellRecord(current=observed(Verdict(ok=False)))
    assert cell_state(grid(), "button", "vue", record) is CellState.FAILED


def test_an_errored_record_is_errored():
    record = CellRecord(current=observed(Errored(error="setup exited 1")))
    assert cell_state(grid(), "button", "vue", record) is CellState.ERRORED


def test_an_exception_returned_by_a_verifier_is_excepted():
    record = CellRecord(current=observed(Excepted(why="unimplemented", reason="not built")))
    assert cell_state(grid(), "button", "vue", record) is CellState.EXCEPTED


def test_a_manifest_exception_outranks_any_record():
    """The verifier does not run for a manifest-excepted cell, so a record left
    behind by an earlier shape of the grid cannot reopen it."""
    closed = grid(**{"except": {"button/vue": {"why": "not-applicable", "reason": "layout"}}})
    stale = CellRecord(current=observed(Verdict(ok=True)))
    assert cell_state(closed, "button", "vue", stale) is CellState.EXCEPTED
    assert cell_state(closed, "button", "react", stale) is CellState.WITNESSED


# --- Reaching an exception's reason ----------------------------------------


def test_a_manifest_exception_reason_is_reachable():
    closed = grid(**{"except": {"button/vue": {"why": "not-applicable", "reason": "layout"}}})
    found = exception_for(closed, "button", "vue")
    assert found is not None and found.reason == "layout"


def test_a_verifier_exception_reason_is_reachable():
    record = CellRecord(current=observed(Excepted(why="unimplemented", reason="range ends at C7")))
    found = exception_for(grid(), "button", "vue", record)
    assert found is not None and found.reason == "range ends at C7"


def test_a_cell_that_is_not_excepted_has_no_exception():
    record = CellRecord(current=observed(Verdict(ok=False)))
    assert exception_for(grid(), "button", "vue", record) is None


# --- Gaps and regressions --------------------------------------------------


def test_a_gap_is_failed_or_errored():
    assert is_gap(CellState.FAILED)
    assert is_gap(CellState.ERRORED)
    assert not is_gap(CellState.WITNESSED)
    assert not is_gap(CellState.EXCEPTED)


def test_a_fresh_clone_has_no_gaps():
    """An unknown cell is not a gap: nothing has been learned about it."""
    assert not is_gap(CellState.UNKNOWN)
    assert not is_gap(cell_state(grid(), "button", "vue", None))


def test_a_red_cell_never_witnessed_is_a_gap_not_a_regression():
    record = CellRecord(current=observed(Verdict(ok=False)))
    state = cell_state(grid(), "voice-input", "react", record)
    assert is_gap(state)
    assert not is_regression(state, record)


def test_a_red_cell_once_witnessed_is_a_regression():
    record = CellRecord(
        current=observed(Verdict(ok=False), rev="d4e5f6"),
        last_witnessed=observed(Verdict(ok=True), rev="a1b2c3"),
    )
    state = cell_state(grid(), "button", "vue", record)
    assert is_regression(state, record)
    assert record.last_witnessed is not None and record.last_witnessed.rev == "a1b2c3"


def test_a_witnessed_cell_is_not_a_regression():
    record = CellRecord(
        current=observed(Verdict(ok=True)),
        last_witnessed=observed(Verdict(ok=True)),
    )
    state = cell_state(grid(), "button", "vue", record)
    assert not is_regression(state, record)


def test_an_unknown_cell_is_not_a_regression():
    assert not is_regression(CellState.UNKNOWN, None)


# --- Cell addressing -------------------------------------------------------


def test_a_cell_is_spelled_the_same_everywhere():
    assert cell_key("button", "vue") == "button/vue"
    closed = grid(
        **{"except": {cell_key("button", "vue"): {"why": "unimplemented", "reason": "r"}}}
    )
    assert cell_key("button", "vue") in closed.except_
