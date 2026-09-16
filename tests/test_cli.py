"""The read-only commands refuse an unreadable run file as a usage error."""

import pytest

from witnessed.cli import main

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
