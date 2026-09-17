"""The runner's process lifecycle, observed through real subprocesses."""

import asyncio
import gc
import os
import subprocess
import sys
import time

import pytest

from witnessed import runner, state
from witnessed.cli import main


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _gone_within(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_a_cancelled_run_collects_the_process_it_killed(tmp_path, monkeypatch):
    """A killed process must be waited for, or its destructor reports it still running.

    The wait, rather than the absence of a warning, is what is asserted: whether
    an uncollected child is noticed at all depends on when the garbage collector
    runs and on which platform, so the condition itself is read off the process.
    """
    started: list[asyncio.subprocess.Process] = []
    real = asyncio.create_subprocess_shell

    async def remember(*arguments, **keywords):
        process = await real(*arguments, **keywords)
        started.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_shell", remember)

    async def cancel_once_started():
        task = asyncio.create_task(runner._run_shell("exec sleep 30", tmp_path, 60))
        while not started:
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_once_started())
    assert started and started[0].returncode is not None


def test_a_cancelled_run_kills_the_verifier_it_started(tmp_path):
    """An interrupt never reaches a verifier in its own session, so cancellation must kill it.

    The collection is explicit: a transport left for the garbage collector is
    closed after the loop is gone, and the destructor's complaint reaches a
    reader who pressed Ctrl-C as though Witnessed had crashed. `filterwarnings`
    turns that complaint into a failure here.
    """
    marker = tmp_path / "pid"

    async def cancel_once_started():
        task = asyncio.create_task(
            runner._run_shell(f"sh -c 'echo $$ > {marker}; exec sleep 30'", tmp_path, 60)
        )
        while not marker.exists() or not marker.read_text().strip():
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_once_started())
    gc.collect()
    assert _gone_within(int(marker.read_text()), 2.0)


def test_an_expired_run_kills_the_verifier_it_started(tmp_path):
    marker = tmp_path / "pid"
    completed = asyncio.run(
        runner._run_shell(f"sh -c 'echo $$ > {marker}; exec sleep 30'", tmp_path, 0.5)
    )
    assert completed.failure == "exceeded 0.5s and was killed"
    assert _gone_within(int(marker.read_text()), 2.0)


def _grid(directory, verify: str, dimensions: str = "[a]", policy: str = "report") -> None:
    (directory / "g.grid.yaml").write_text(
        "witnessed: 1\n"
        "id: g\n"
        'claim: "the runner answers for every cell it started"\n'
        f"dimensions: {dimensions}\n"
        "variants: [x]\n"
        f"verify: |-\n  {verify}\n"
        f"policy: {{on_gap: {policy}, on_regression: fail}}\n",
        encoding="utf-8",
    )


def _verify(directory, *arguments) -> int:
    """Run the grid the way the CLI does, against records of its own."""
    return main(
        ["verify", str(directory), "--state-dir", str(directory / ".witnessed"), *arguments]
    )


def test_a_verifier_that_answers_and_leaves_work_behind_keeps_its_verdict(tmp_path):
    """The descendant holds the pipe; the cell was answered before it did.

    Read through `communicate()`, the verdict is lost with the cancelled task and
    a proven cell reports errored -- the one outcome the tool exists to prevent.
    """
    _grid(tmp_path, "sleep 240 & echo '{\"ok\": true}'; exit 0")
    try:
        assert _verify(tmp_path, "--timeout", "2") == 0
        record = state.load(tmp_path / ".witnessed")["g"]["a/x"]
        assert getattr(record.current.result, "ok", None) is True
    finally:
        subprocess.run(["pkill", "-f", "sleep 240"], capture_output=True)


def test_an_expired_verifier_takes_its_grandchildren_with_it(tmp_path):
    """Signalling the shell alone leaves the tree it started; the group is the unit."""
    _grid(tmp_path, "(sleep 241 &); sleep 30")
    try:
        _verify(tmp_path, "--timeout", "1")
        assert _gone_within_pattern("sleep 241", 3.0)
    finally:
        subprocess.run(["pkill", "-f", "sleep 241"], capture_output=True)


def test_a_descendant_outside_the_group_cannot_hang_the_run(tmp_path):
    """A process that leaves the session survives the kill, so the wait after it is bounded."""
    escape = tmp_path / "escape.py"
    escape.write_text(
        "import os, time\nos.setsid()\nprint('holding', flush=True)\ntime.sleep(240)\n"
    )
    _grid(tmp_path, f"{sys.executable} escape.py & sleep 30")
    started = time.monotonic()
    try:
        _verify(tmp_path, "--timeout", "1")
        elapsed = time.monotonic() - started
        assert elapsed < runner._REAP_TIMEOUT + 15, f"the run took {elapsed:.1f}s"
        result = state.load(tmp_path / ".witnessed")["g"]["a/x"].current.result
        assert getattr(result, "error", "").endswith("was killed")
    finally:
        subprocess.run(["pkill", "-f", "escape.py"], capture_output=True)


def test_only_the_last_line_of_stdout_is_read(tmp_path):
    """A verifier that logs before it answers is read by its answer, not its first line."""
    _grid(tmp_path, "echo '{\"ok\": false}'; echo 'progress'; echo '{\"ok\": true}'")
    _verify(tmp_path)
    record = state.load(tmp_path / ".witnessed")["g"]["a/x"]
    assert getattr(record.current.result, "ok", None) is True


def test_a_verdict_is_read_whatever_the_exit_status(tmp_path):
    """The exit code does not decide: a verifier that reported its cell has read it."""
    _grid(tmp_path, 'echo \'{"ok": false, "evidence": {"found": 0}}\'; exit 3')
    _verify(tmp_path)
    result = state.load(tmp_path / ".witnessed")["g"]["a/x"].current.result
    assert getattr(result, "ok", None) is False
    assert getattr(result, "evidence", None) == {"found": 0}


def test_no_more_verifiers_run_at_once_than_the_bound_allows(tmp_path):
    """An unbounded fan-out would spawn one process per cell however large the grid."""
    (tmp_path / "live").mkdir()
    (tmp_path / "seen").mkdir()
    _grid(
        tmp_path,
        'touch live/"$$"; sleep 0.4; ls live | wc -l > seen/"$$"; rm live/"$$"; '
        "echo '{\"ok\": true}'",
        dimensions="[a, b, c, d, e, f]",
    )
    _verify(tmp_path, "-n", "2")
    observed = [int(path.read_text().strip()) for path in (tmp_path / "seen").iterdir()]
    assert observed and max(observed) <= 2, observed


def _gone_within_pattern(pattern: str, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode != 0:
            return True
        time.sleep(0.05)
    return False
