"""The runner's process lifecycle, observed through real subprocesses."""

import asyncio
import os
import time

import pytest

from witnessed import runner


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


def test_a_cancelled_run_kills_the_verifier_it_started(tmp_path):
    """An interrupt never reaches a verifier in its own session, so cancellation must kill it."""
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
    assert _gone_within(int(marker.read_text()), 2.0)


def test_an_expired_run_kills_the_verifier_it_started(tmp_path):
    marker = tmp_path / "pid"
    completed = asyncio.run(
        runner._run_shell(f"sh -c 'echo $$ > {marker}; exec sleep 30'", tmp_path, 0.5)
    )
    assert completed.failure == "exceeded 0.5s and was killed"
    assert _gone_within(int(marker.read_text()), 2.0)
