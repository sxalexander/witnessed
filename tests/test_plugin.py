"""Tests of the plugin, run as pytest runs it.

`Pytester` starts a real session in a temporary directory, so what is asserted
here is what a user gets: how many cells a manifest collects, which outcome
each cell reports, what a closed cell's reason says, and what a grid's policy
does to the exit code. The self-grids witness the product; these witness the
plumbing.

Each test writes its own manifest and its own verifier. A verifier is a shell
command, so the manifests use a YAML literal block: the JSON a verifier emits
carries quotes the shell would otherwise strip, and a cell whose verifier
printed `{ok: true}` reads errored rather than witnessed.
"""

import pytest

from witnessed import state

ISOLATION = ("-p", "witnessed.plugin", "-p", "no:cacheprovider", "-p", "no:python", "--noconftest")

ALL_STATES = """\
witnessed: 1
id: alpha
claim: "every state the render must hold apart appears once"
dimensions: [a, b, c]
variants: [x, y]
verify: |-
  ./demo {dimension} {variant}
policy: {on_gap: report, on_regression: fail}
except:
  b/y:
    why: not-applicable
    reason: "a layout primitive is composed here, not implemented"
"""

DEMO_VERIFIER = """\
#!/bin/sh
case "$1/$2" in
  a/x) echo '{"ok": true}' ;;
  a/y) echo '{"ok": false, "evidence": {"found": "nothing"}}' ;;
  b/x) echo "the corpus is unreachable" >&2; exit 1 ;;
  c/x) echo '{"why": "unimplemented", "reason": "not recorded yet"}' ;;
  c/y) echo '{"ok": true}' ;;
  *) echo "no cell here"; exit 9 ;;
esac
"""


def write_grid(pytester: pytest.Pytester, manifest: str, verifier: str | None = None):
    """A manifest, its verifier, and the arguments that run them in isolation."""
    path = pytester.path / "one.grid.yaml"
    path.write_text(manifest, encoding="utf-8")
    if verifier is not None:
        executable = pytester.path / "demo"
        executable.write_text(verifier, encoding="utf-8")
        executable.chmod(0o755)
    return [*ISOLATION, "--witnessed-state-dir", str(pytester.path / ".witnessed"), str(path)]


def reports(result):
    return [
        report
        for report in result.reprec.getreports("pytest_runtest_logreport")
        if report.when == "call" or report.skipped
    ]


def test_a_grid_collects_one_item_per_cell(pytester: pytest.Pytester) -> None:
    """Totality is generated from the axes: three dimensions by two variants is six."""
    result = pytester.runpytest_inprocess(
        "--collect-only", *write_grid(pytester, ALL_STATES, DEMO_VERIFIER)
    )
    collected = result.reprec.getcalls("pytest_itemcollected")
    assert len(collected) == 6
    assert [call.item.nodeid for call in collected] == [
        "alpha::a::x",
        "alpha::a::y",
        "alpha::b::x",
        "alpha::b::y",
        "alpha::c::x",
        "alpha::c::y",
    ]


def test_a_collect_only_run_spawns_no_verifier(pytester: pytest.Pytester) -> None:
    """Collection alone learns nothing, so it leaves no record behind."""
    pytester.runpytest_inprocess("--collect-only", *write_grid(pytester, ALL_STATES, DEMO_VERIFIER))
    assert not (pytester.path / ".witnessed").exists()


def test_a_manifest_exception_reports_skipped_with_its_reason(pytester: pytest.Pytester) -> None:
    """A cell the author closed is skipped, and the reason travels with it."""
    result = pytester.runpytest_inprocess(*write_grid(pytester, ALL_STATES, DEMO_VERIFIER))
    skipped = {report.nodeid: report.longrepr[2] for report in reports(result) if report.skipped}
    assert skipped["alpha::b::y"] == (
        "Skipped: not-applicable: a layout primitive is composed here, not implemented"
    )


def test_a_verifier_exception_reports_skipped_with_its_reason(pytester: pytest.Pytester) -> None:
    """A verifier may close a cell the manifest did not; the corpus is the source
    of truth about itself."""
    result = pytester.runpytest_inprocess(*write_grid(pytester, ALL_STATES, DEMO_VERIFIER))
    skipped = {report.nodeid: report.longrepr[2] for report in reports(result) if report.skipped}
    assert skipped["alpha::c::x"] == "Skipped: unimplemented: not recorded yet"


def test_a_failing_verifier_reports_failed_with_its_output(pytester: pytest.Pytester) -> None:
    """A cell that read false fails, and the report is the verifier's own output."""
    result = pytester.runpytest_inprocess(*write_grid(pytester, ALL_STATES, DEMO_VERIFIER))
    failed = {report.nodeid: report for report in reports(result) if report.failed}
    assert "alpha::a::y" in failed
    assert '"found": "nothing"' in str(failed["alpha::a::y"].longrepr)
    assert "_pytest" not in str(failed["alpha::a::y"].longrepr)


def test_a_crashed_verifier_is_errored_rather_than_failed(pytester: pytest.Pytester) -> None:
    """pytest has four outcomes and Witnessed distinguishes five states, so the
    one pytest cannot spell travels in `user_properties`."""
    result = pytester.runpytest_inprocess(*write_grid(pytester, ALL_STATES, DEMO_VERIFIER))
    failed = {report.nodeid: report for report in reports(result) if report.failed}
    assert ("witnessed", "errored") in failed["alpha::b::x"].user_properties
    assert ("witnessed", "errored") not in failed["alpha::a::y"].user_properties


def test_every_state_is_observed_in_one_run(pytester: pytest.Pytester) -> None:
    """Two green, one red, one errored, two closed, and nothing else."""
    result = pytester.runpytest_inprocess(*write_grid(pytester, ALL_STATES, DEMO_VERIFIER))
    result.reprec.assertoutcome(passed=2, failed=2, skipped=2)


ROLLOUT = """\
witnessed: 1
id: rollout
claim: "a new row starts red because it is red"
dimensions: [a]
variants: [x]
verify: |-
  echo '{"ok": false}'
policy: {on_gap: %s, on_regression: fail}
"""


def test_on_gap_report_exits_zero(pytester: pytest.Pytester) -> None:
    """A rollout row is red by intent, so a gap does not fail the session."""
    result = pytester.runpytest_inprocess(*write_grid(pytester, ROLLOUT % "report"))
    result.reprec.assertoutcome(failed=1)
    assert result.ret == pytest.ExitCode.OK


def test_on_gap_fail_exits_one(pytester: pytest.Pytester) -> None:
    """An acceptance gate is not done while a claim is unproven."""
    result = pytester.runpytest_inprocess(*write_grid(pytester, ROLLOUT % "fail"))
    result.reprec.assertoutcome(failed=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_the_exit_code_comes_from_the_policy_not_the_failure_count(
    pytester: pytest.Pytester,
) -> None:
    """One red cell under two policies is one pytest failure and two exit codes."""
    reported = pytester.runpytest_inprocess(*write_grid(pytester, ROLLOUT % "report"))
    failing = pytester.runpytest_inprocess(*write_grid(pytester, ROLLOUT % "fail"))
    assert reported.reprec.countoutcomes() == failing.reprec.countoutcomes()
    assert reported.ret != failing.ret


REGRESSION = """\
witnessed: 1
id: regression
claim: "a cell that emptied is news"
dimensions: [a]
variants: [x]
verify: |-
  test -f fixture && echo '{"ok": true}' || echo '{"ok": false}'
policy: {on_gap: report, on_regression: fail}
"""


def test_a_lost_green_fails_where_a_red_row_does_not(pytester: pytest.Pytester) -> None:
    """The whole distinction between a gap and a regression is `last_witnessed`."""
    arguments = write_grid(pytester, REGRESSION)
    (pytester.path / "fixture").write_text("", encoding="utf-8")
    assert pytester.runpytest_inprocess(*arguments).ret == pytest.ExitCode.OK

    (pytester.path / "fixture").unlink()
    result = pytester.runpytest_inprocess(*arguments)
    assert result.ret == pytest.ExitCode.TESTS_FAILED

    records = state.load(pytester.path / ".witnessed")["regression"]["a/x"]
    assert state.is_regression(records)
    assert records.last_witnessed is not None


UNLOADABLE = """\
witnessed: 1
id: unloadable
claim: "an exception outside the product closes nothing"
dimensions: [a]
variants: [x]
except:
  q/z:
    why: not-applicable
    reason: "a coordinate this grid does not have"
verify: |-
  echo '{"ok": true}'
"""


def test_a_manifest_that_does_not_load_exits_four(pytester: pytest.Pytester) -> None:
    """A manifest that did not load is a wrong input, not a failing cell, and the
    author is shown the offending key rather than a traceback."""
    result = pytester.runpytest_inprocess(*write_grid(pytester, UNLOADABLE))
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*`except` key `q/z` is outside the grid's product*"])
    assert not (pytester.path / ".witnessed").exists()


ALL_EXCEPTED = """\
witnessed: 1
id: closed
claim: "a grid with nothing to run is a valid grid"
dimensions: [a]
variants: [x]
verify: |-
  echo '{"ok": true}'
policy: {on_gap: fail, on_regression: fail}
except:
  a/x:
    why: not-applicable
    reason: "the subject must never exist here"
"""


def test_a_grid_whose_every_cell_is_excepted_exits_zero(pytester: pytest.Pytester) -> None:
    """Nothing ran, and nothing was owed."""
    result = pytester.runpytest_inprocess(*write_grid(pytester, ALL_EXCEPTED))
    result.reprec.assertoutcome(skipped=1)
    assert result.ret == pytest.ExitCode.OK


def test_a_selection_writes_only_the_cells_it_ran(pytester: pytest.Pytester) -> None:
    """A developer iterating on one cell cannot erase the evidence for the rest."""
    arguments = write_grid(pytester, ALL_STATES, DEMO_VERIFIER)
    pytester.runpytest_inprocess(*arguments)
    before = state.load(pytester.path / ".witnessed")["alpha"]

    pytester.runpytest_inprocess("-k", "alpha::a::x", *arguments)
    after = state.load(pytester.path / ".witnessed")["alpha"]

    assert set(after) == set(before)
    assert after["a/y"] == before["a/y"]
    assert after["a/x"].current.at > before["a/x"].current.at


TWO_GRIDS = """\
witnessed: 1
id: alpha
claim: "one id belongs to one manifest"
dimensions: [a]
variants: [x]
verify: |-
  echo '{"ok": true}'
"""


def test_two_manifests_with_one_id_are_a_collection_error(pytester: pytest.Pytester) -> None:
    """One id keyed against two grids' cells would silently overwrite one of them."""
    arguments = write_grid(pytester, TWO_GRIDS)
    (pytester.path / "two.grid.yaml").write_text(TWO_GRIDS, encoding="utf-8")
    result = pytester.runpytest_inprocess(*arguments[:-1], str(pytester.path))
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*grid id `alpha` is declared by both*"])


def test_the_plugin_is_inert_unless_it_is_loaded(pytester: pytest.Pytester) -> None:
    """A project's own pytest invocation never collects a grid."""
    write_grid(pytester, ALL_STATES, DEMO_VERIFIER)
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider", "--noconftest")
    assert not result.reprec.getcalls("pytest_itemcollected")
    assert not (pytester.path / ".witnessed").exists()


SLOW = """\
witnessed: 1
id: slow
claim: "one hung verifier does not stall the others"
dimensions: [a]
variants: [x]
verify: |-
  sleep 30
policy: {on_gap: report, on_regression: fail}
"""


def test_an_expired_verifier_reads_errored(pytester: pytest.Pytester) -> None:
    """A killed verifier learned nothing, which is errored rather than failed."""
    result = pytester.runpytest_inprocess("--witnessed-timeout", "0.5", *write_grid(pytester, SLOW))
    failed = [report for report in reports(result) if report.failed]
    assert ("witnessed", "errored") in failed[0].user_properties
    assert "killed" in str(failed[0].longrepr)
