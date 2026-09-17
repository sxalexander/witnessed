"""The read-only commands refuse an unreadable run file as a usage error."""

import os

import pytest

from witnessed import state
from witnessed.cli import main, state_directory

MANIFEST = """\
witnessed: 1
id: docs
claim: "c"
dimensions: [a]
variants: [x]
verify: "true"
"""


@pytest.mark.parametrize("command", [["check"], ["gap", "--export"]])
def test_a_conflicted_run_file_is_a_usage_error_not_a_traceback(tmp_path, capsys, command):
    (tmp_path / "docs.grid.yaml").write_text(MANIFEST, encoding="utf-8")
    state_dir = tmp_path / ".witnessed"
    state_dir.mkdir()
    (state_dir / "runs.json").write_text("<<<<<<< HEAD\n{}\n=======\n{}\n>>>>>>> x\n")

    code = main([*command, str(tmp_path), "--state-dir", str(state_dir)])

    assert code == int(pytest.ExitCode.USAGE_ERROR)
    assert "unresolved merge conflict markers" in capsys.readouterr().err


GATE = """\
witnessed: 1
id: g
claim: "a gate cannot be turned off from outside"
dimensions: [a]
variants: [x]
verify: "echo '{\\"ok\\": false}'"
policy: {on_gap: fail, on_regression: fail}
"""


def test_the_session_ignores_pytest_addopts(tmp_path, monkeypatch):
    """pytest applies PYTEST_ADDOPTS whatever config it is given; tox and CI set it."""
    (tmp_path / "g.grid.yaml").write_text(GATE, encoding="utf-8")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")

    code = main(["verify", str(tmp_path)])

    assert code == int(pytest.ExitCode.TESTS_FAILED)
    assert state.load(tmp_path / ".witnessed")["g"]["a/x"].current.result.ok is False
    assert os.environ["PYTEST_ADDOPTS"] == "--collect-only"


def test_records_are_anchored_to_the_project_not_the_working_directory(tmp_path, monkeypatch):
    """Anchored to the working directory, the same grid run one level down reads an
    empty memory: nothing can be a regression and `on_regression: fail` passes."""
    (tmp_path / ".git").mkdir()
    grids = tmp_path / "grids"
    grids.mkdir()
    (grids / "g.grid.yaml").write_text(GATE, encoding="utf-8")

    from_root = state_directory(None, [str(grids)])
    monkeypatch.chdir(grids)
    from_inside = state_directory(None, None)

    assert from_root == tmp_path / ".witnessed"
    assert from_inside == from_root
