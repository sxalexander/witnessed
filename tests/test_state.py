"""Tests of the properties the run file is responsible for holding.

Records are built through `merge` rather than by hand wherever a test can,
because the route from a result to a `last_witnessed` is the invariant under
test and constructing a `CellRecord` directly bypasses it.
"""

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from witnessed import state
from witnessed.model import (
    CellRecord,
    Errored,
    Excepted,
    ExceptionKind,
    Observation,
    Verdict,
)

EARLIER = datetime(2026, 9, 1, 16, 40, 11, tzinfo=UTC)
LATER = datetime(2026, 9, 7, 10, 12, 3, tzinfo=UTC)

WITNESSED = Verdict(ok=True)
FAILED = Verdict(ok=False, evidence={"found": "Samples/vln/sulpont/"})
ERRORED = Errored(error="killed on timeout")
EXCEPTED = Excepted(why=ExceptionKind.NOT_APPLICABLE, reason="range ends at C7")

PRODUCT = [("a", "x"), ("a", "y"), ("b", "x"), ("b", "y")]


def green(coordinate: tuple[str, str], grid_id: str = "demo") -> state.RunData:
    """Records in which one cell has been witnessed once, at the earlier instant."""
    return state.merge({}, grid_id, {coordinate: WITNESSED}, "a1b2c3", at=EARLIER)


# --- Reading -----------------------------------------------------------------


def test_a_missing_file_is_the_unknown_state(tmp_path: Path):
    assert state.load(tmp_path) == {}


def test_a_missing_state_directory_is_the_unknown_state(tmp_path: Path):
    assert state.load(tmp_path / "absent") == {}


def test_an_unreadable_file_raises_rather_than_discarding_records(tmp_path: Path):
    state.run_file_path(tmp_path).write_text('{"witnessed": 1, "grids": {"demo": []}}')
    with pytest.raises(ValueError):
        state.load(tmp_path)


def test_a_file_without_a_version_does_not_load(tmp_path: Path):
    state.run_file_path(tmp_path).write_text('{"grids": {}}')
    with pytest.raises(ValueError):
        state.load(tmp_path)


# --- Writing -----------------------------------------------------------------


def test_every_result_type_survives_a_save_and_load(tmp_path: Path):
    data = state.merge(
        {},
        "demo",
        {
            ("a", "x"): WITNESSED,
            ("a", "y"): FAILED,
            ("b", "x"): ERRORED,
            ("b", "y"): EXCEPTED,
        },
        "d4e5f6",
        at=LATER,
    )
    state.save(tmp_path, data)
    assert state.load(tmp_path) == data


def test_a_failed_write_leaves_the_previous_file_intact(tmp_path: Path, monkeypatch):
    state.save(tmp_path, green(("a", "x")))
    previous = state.run_file_path(tmp_path).read_bytes()

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("witnessed.state.os.replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        state.save(tmp_path, state.merge({}, "demo", {("a", "x"): FAILED}, "d4e5f6", at=LATER))

    assert state.run_file_path(tmp_path).read_bytes() == previous
    assert {path.name for path in tmp_path.iterdir()} == {state.RUN_FILE_NAME}


def test_the_written_file_is_byte_stable(tmp_path: Path):
    data = state.merge({}, "demo", dict.fromkeys(PRODUCT, WITNESSED), "a1b2c3", at=EARLIER)
    state.save(tmp_path, data)
    first = state.run_file_path(tmp_path).read_bytes()
    state.save(tmp_path, state.load(tmp_path))
    assert state.run_file_path(tmp_path).read_bytes() == first


# --- Merging: the route to last_witnessed ------------------------------------


def test_a_witnessed_result_moves_last_witnessed():
    merged = state.merge(green(("a", "x")), "demo", {("a", "x"): WITNESSED}, "d4e5f6", at=LATER)
    record = merged["demo"]["a/x"]
    assert record.last_witnessed is not None
    assert record.last_witnessed.at == LATER
    assert record.last_witnessed.rev == "d4e5f6"


def test_a_failed_result_keeps_the_revision_the_cell_was_last_true_at():
    merged = state.merge(green(("a", "x")), "demo", {("a", "x"): FAILED}, "d4e5f6", at=LATER)
    record = merged["demo"]["a/x"]
    assert record.current.result == FAILED
    assert record.last_witnessed is not None
    assert record.last_witnessed.rev == "a1b2c3"
    assert record.last_witnessed.at == EARLIER
    assert state.is_regression(record)


@pytest.mark.parametrize("result", [FAILED, ERRORED, EXCEPTED], ids=lambda r: type(r).__name__)
def test_only_a_verdict_of_ok_moves_last_witnessed(result):
    merged = state.merge(green(("a", "x")), "demo", {("a", "x"): result}, "d4e5f6", at=LATER)
    last_witnessed = merged["demo"]["a/x"].last_witnessed
    assert last_witnessed is not None
    assert last_witnessed.rev == "a1b2c3"


def test_a_new_cell_that_fails_is_a_gap_and_not_a_regression():
    merged = state.merge({}, "demo", {("voice-input", "react"): FAILED}, "d4e5f6", at=LATER)
    record = merged["demo"]["voice-input/react"]
    assert record.last_witnessed is None
    assert not state.is_regression(record)


def test_a_cell_that_goes_green_is_no_longer_a_regression():
    broken = state.merge(green(("a", "x")), "demo", {("a", "x"): FAILED}, "d4e5f6", at=LATER)
    assert state.is_regression(broken["demo"]["a/x"])
    fixed = state.merge(broken, "demo", {("a", "x"): WITNESSED}, "e7f8a9", at=LATER)
    assert not state.is_regression(fixed["demo"]["a/x"])


# --- Merging: what a run may not touch ---------------------------------------


def test_a_partial_run_leaves_the_cells_it_did_not_select_alone():
    existing = state.merge({}, "demo", dict.fromkeys(PRODUCT, WITNESSED), "a1b2c3", at=EARLIER)
    merged = state.merge(existing, "demo", {("a", "x"): FAILED}, "d4e5f6", at=LATER)
    assert merged["demo"]["a/x"].current.result == FAILED
    for key in ("a/y", "b/x", "b/y"):
        assert merged["demo"][key] == existing["demo"][key]


def test_a_full_run_leaves_a_cell_it_did_not_observe_alone():
    existing = state.merge({}, "demo", dict.fromkeys(PRODUCT, WITNESSED), "a1b2c3", at=EARLIER)
    merged = state.merge(
        existing,
        "demo",
        {("a", "x"): FAILED},
        "d4e5f6",
        product=PRODUCT,
        at=LATER,
    )
    assert merged["demo"]["b/y"] == existing["demo"]["b/y"]
    assert merged["demo"]["b/y"].last_witnessed is not None


def test_a_run_of_one_grid_leaves_the_other_grids_alone():
    existing = green(("a", "x"), grid_id="beta")
    merged = state.merge(existing, "demo", {("a", "x"): FAILED}, "d4e5f6", at=LATER)
    assert merged["beta"] == existing["beta"]


def test_a_full_run_of_one_grid_retires_nothing_from_another_grid():
    existing = state.merge(green(("a", "x"), grid_id="beta"), "demo", {("a", "y"): WITNESSED})
    merged = state.merge(
        existing,
        "demo",
        {("a", "x"): WITNESSED},
        "d4e5f6",
        product=[("a", "x")],
        at=LATER,
    )
    assert merged["beta"] == existing["beta"]
    assert set(merged["demo"]) == {"a/x"}


def test_merge_does_not_mutate_what_it_was_given():
    existing = green(("a", "x"))
    state.merge(existing, "demo", {("a", "x"): FAILED, ("a", "y"): FAILED}, "d4e5f6", at=LATER)
    assert set(existing["demo"]) == {"a/x"}
    assert existing["demo"]["a/x"].current.result == WITNESSED


# --- Merging: retirement -----------------------------------------------------


def test_a_full_run_drops_a_coordinate_the_manifest_no_longer_describes():
    existing = state.merge(
        {},
        "demo",
        {("a", "x"): WITNESSED, ("retired", "x"): WITNESSED},
        "a1b2c3",
        at=EARLIER,
    )
    merged = state.merge(
        existing,
        "demo",
        {("a", "x"): WITNESSED},
        "d4e5f6",
        product=[("a", "x")],
        at=LATER,
    )
    assert set(merged["demo"]) == {"a/x"}


def test_a_partial_run_retires_nothing():
    existing = state.merge(
        {},
        "demo",
        {("a", "x"): WITNESSED, ("retired", "x"): WITNESSED},
        "a1b2c3",
        at=EARLIER,
    )
    merged = state.merge(existing, "demo", {("a", "x"): FAILED}, "d4e5f6", at=LATER)
    assert set(merged["demo"]) == {"a/x", "retired/x"}


# --- Reading a record without its manifest -----------------------------------


def test_a_record_never_witnessed_is_not_a_regression():
    record = CellRecord(current=Observation(at=LATER, rev="d4e5f6", result=ERRORED))
    assert not state.is_regression(record)


# --- The revision a run stamps -----------------------------------------------


def test_git_rev_reports_the_short_head_sha(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    git("init")
    git("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "--allow-empty", "-m", "x")
    expected = git("rev-parse", "--short", "HEAD").stdout.strip()
    assert state.git_rev() == expected


def test_git_rev_is_none_outside_a_repository(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert state.git_rev() is None


def test_git_rev_is_none_where_git_cannot_be_started(tmp_path: Path, monkeypatch):
    def absent(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr("witnessed.state.subprocess.run", absent)
    assert state.git_rev() is None


def git(*arguments: str) -> subprocess.CompletedProcess[str]:
    """A git invocation the test asserts on, run in the current directory."""
    environment = os.environ | {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    return subprocess.run(
        ["git", *arguments],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
    )
