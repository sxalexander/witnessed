"""`witnessed init` writes a manifest the loader accepts, and nothing else.

The command exists because a first grid is otherwise hand-authored YAML with a
version field its author has never seen. Every field is a flag so the command
is scriptable, and the manifest is validated as a `Grid` before it is written,
so `init` cannot produce a file `verify` would reject.
"""

import json
import subprocess
import sys

import pytest

from witnessed import manifest
from witnessed.cli import main


def run(*arguments):
    return main(list(arguments))


def test_defaults_write_a_manifest_and_a_stub(tmp_path):
    assert (
        run(
            "init",
            "--id=docs",
            "--claim=guides exist",
            "--dimensions=install,usage",
            "--variants=en,es",
            f"--path={tmp_path}",
        )
        == 0
    )
    grid = manifest.load(tmp_path / "docs.grid.yaml")
    assert grid.id == "docs"
    assert grid.dimensions == ["install", "usage"]
    assert grid.variants == ["en", "es"]
    assert grid.verify == "verify/docs {dimension} {variant}"
    stub = tmp_path / "verify" / "docs"
    assert stub.exists() and stub.stat().st_mode & 0o111


def test_the_stub_answers_every_cell_red(tmp_path):
    run("init", "--id=docs", "--claim=c", "--dimensions=a,b", "--variants=x", f"--path={tmp_path}")
    stub = tmp_path / "verify" / "docs"
    for dimension in ("a", "b"):
        finished = subprocess.run(
            [sys.executable, str(stub), dimension, "x"], capture_output=True, text=True
        )
        assert finished.returncode == 0
        assert json.loads(finished.stdout.strip().splitlines()[-1])["ok"] is False


def test_an_explicit_verify_writes_no_stub(tmp_path):
    run(
        "init",
        "--id=docs",
        "--claim=c",
        "--dimensions=a",
        "--variants=x",
        "--verify=true",
        f"--path={tmp_path}",
    )
    assert not (tmp_path / "verify").exists()
    assert manifest.load(tmp_path / "docs.grid.yaml").verify == "true"


def test_every_field_round_trips(tmp_path):
    run(
        "init",
        "--id=frameworks",
        "--claim=components render",
        "--dimensions=button,text-input",
        "--variants=react,web-components",
        "--verify=check {dimension} {variant}",
        "--setup=pnpm build",
        "--template=prompts/x.j2",
        "--on-gap=fail",
        "--on-regression=report",
        f"--path={tmp_path}",
    )
    grid = manifest.load(tmp_path / "frameworks.grid.yaml")
    assert grid.setup == "pnpm build"
    assert grid.policy.on_gap == "fail"
    assert grid.policy.on_regression == "report"
    assert grid.export == {"template": "prompts/x.j2"}


def test_stdout_writes_nothing(tmp_path, capsys):
    assert (
        run(
            "init",
            "--id=docs",
            "--claim=c",
            "--dimensions=a",
            "--variants=x",
            f"--path={tmp_path}",
            "--stdout",
        )
        == 0
    )
    assert "witnessed: 1" in capsys.readouterr().out
    assert not (tmp_path / "docs.grid.yaml").exists()


def test_an_existing_manifest_is_not_overwritten(tmp_path):
    arguments = [
        "init",
        "--id=docs",
        "--claim=c",
        "--dimensions=a",
        "--variants=x",
        f"--path={tmp_path}",
    ]
    assert run(*arguments) == 0
    assert run(*arguments) == int(pytest.ExitCode.USAGE_ERROR)
    assert run(*arguments, "--force") == 0


@pytest.mark.parametrize("flag", ["--id=Docs Grid", "--dimensions=a,a", "--variants=X"])
def test_an_invalid_grid_is_never_written(tmp_path, flag):
    defaults = {"--id": "docs", "--claim": "c", "--dimensions": "a", "--variants": "x"}
    name, value = flag.split("=", 1)
    defaults[name] = value
    arguments = [f"{k}={v}" for k, v in defaults.items()]
    assert run("init", *arguments, f"--path={tmp_path}") == int(pytest.ExitCode.USAGE_ERROR)
    assert not list(tmp_path.glob("*.grid.yaml"))


def test_no_exception_flag_exists():
    """Exceptions are written by reacting to a run, never before one, so the
    command that creates a grid cannot close one of its cells."""
    from witnessed.cli import _parser

    init = _parser()._subparsers._group_actions[0].choices["init"]
    assert not [action for action in init._actions if "except" in action.dest]
