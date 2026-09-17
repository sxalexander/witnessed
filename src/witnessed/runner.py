"""Running a grid's verifiers: one setup, then every remaining cell at once.

pytest runs one item at a time per process, which is right for CPU-bound Python
tests and wrong for a workload that is entirely waiting on child processes.
Fan-out therefore lives here rather than in pytest's loop: one interpreter, one
event loop, and a semaphore, so sixty-four concurrent verifiers cost sixty-four
child processes rather than sixty-four idle interpreters.

Three failures are indistinguishable to the cell they were meant to read, and
are one state here. A verifier that could not be started, one killed on its
timeout, and one whose last stdout line is not a `VerifyResult` all yield
`Errored`, because in each case nothing was learned. `Errored` is assigned only
here: a verifier that emits one is refused by the parser like any other unknown
shape, since a process cannot report its own failure to report.

A verifier is reached through a shell, so the process this module holds is the
shell and not the verifier. Every command therefore starts its own session, and
an expired command is killed by process group: killing the shell alone leaves
the verifier running, which is the difference between a timeout that bounds a
run and one that only stops waiting for it.
"""

import asyncio
import contextlib
import json
import os
import signal
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from witnessed.model import Errored, Grid, VerifyResult, VerifyResultAdapter

DEFAULT_CONCURRENCY = 8
"""How many verifiers may be alive at once when a caller names no bound.

Provisional: the budget this serves, and the fixture scale it is measured on,
are the subject of the runner self-grid.
"""

DEFAULT_TIMEOUT = 300.0
"""Seconds one verifier may run before it is killed and its cell reads errored.

A bound exists so that one hung client launch cannot stall the other cells; the
value is generous because a verifier may drive a GUI client to observe a render.
"""

DEFAULT_SETUP_TIMEOUT = 900.0
"""Seconds a grid's `setup` may run. Longer than a cell's, because `setup` is
where a corpus is exported or a project is built, once, for the whole grid."""

_STDERR_HEADING = "stderr:"

_REAP_TIMEOUT = 5.0
"""Seconds an interrupted run waits for a killed verifier to end.

A process under SIGKILL ends at once unless it is stuck in the kernel, and an
interrupt that hung on that case would be worse than one that reports it.
"""


@dataclass(frozen=True, slots=True)
class Job:
    """One cell's verifier, resolved to the exact command and directory it runs in.

    The command arrives already interpolated because substitution is only sound
    for a coordinate the manifest declares, and that check belongs with the
    manifest. The grid travels alongside so a caller collecting results can key
    them back to the grid that asked.
    """

    grid: Grid
    dimension: str
    variant: str
    command: str
    cwd: Path


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one verifier said, and what it printed while saying it.

    The text is kept beside the result because a failing cell is reported with
    the verifier's own output rather than a Python traceback, and a cell that
    errored has no verdict to carry evidence at all.
    """

    result: VerifyResult | Errored
    output: str


@dataclass(frozen=True, slots=True)
class _Completed:
    """A finished shell command, or the reason there is nothing to read from it."""

    returncode: int | None
    stdout: str
    stderr: str
    failure: str | None


def run_setup(
    grid: Grid,
    cwd: Path | str,
    *,
    timeout: float = DEFAULT_SETUP_TIMEOUT,
) -> Errored | None:
    """Run a grid's `setup` in the manifest's directory, once, before any cell.

    Returns the error every cell of the grid inherits, or nothing when the grid
    declares no setup or its setup succeeded. A failing setup leaves the cells
    errored rather than failed: the build that was to make them readable never
    ran, so nothing was learned about them.
    """
    if grid.setup is None:
        return None
    completed = asyncio.run(_run_shell(grid.setup, Path(cwd), timeout))
    if completed.failure is not None:
        return Errored(error=f"grid `{grid.id}` setup: {completed.failure}")
    if completed.returncode:
        lines = [f"grid `{grid.id}` setup exited {completed.returncode}"]
        printed = _output(completed)
        if printed:
            lines.append(printed)
        return Errored(error="\n".join(lines))
    return None


async def fan_out(
    jobs: Sequence[Job],
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[Outcome]:
    """Every job's verifier, run under a bound, in the order the jobs were given.

    Results are positional rather than keyed so that a caller holding items and
    jobs in one order needs no second spelling of a coordinate to match them.

    One crash becomes one errored cell rather than an aborted run: tasks are
    gathered with `return_exceptions=True`, and an exception that is not an
    `Exception` — an interrupt — is re-raised, so a killed run still leaves the
    previous records whole.
    """
    scheduled = list(jobs)
    semaphore = asyncio.Semaphore(max(1, concurrency))
    gathered = await asyncio.gather(
        *(_verify(job, semaphore, timeout) for job in scheduled),
        return_exceptions=True,
    )

    outcomes: list[Outcome] = []
    for job, gathering in zip(scheduled, gathered, strict=True):
        if isinstance(gathering, BaseException):
            if not isinstance(gathering, Exception):
                raise gathering
            outcomes.append(
                Outcome(result=Errored(error=f"`{job.command}`: {gathering!r}"), output="")
            )
        else:
            outcomes.append(gathering)
    return outcomes


async def _verify(job: Job, semaphore: asyncio.Semaphore, timeout: float) -> Outcome:
    async with semaphore:
        completed = await _run_shell(job.command, job.cwd, timeout)
    return Outcome(result=read_result(job.command, completed), output=_output(completed))


def read_result(command: str, completed: _Completed) -> VerifyResult | Errored:
    """The one JSON object on the last stdout line, or the reason there is none.

    The exit code does not decide. A verifier that reports `{"ok": false}` and
    exits non-zero has read its cell and said so; a verifier that exits zero
    having printed prose has not. What separates them is whether the last line
    is a `VerifyResult`, which is the whole of the contract.
    """
    if completed.failure is not None:
        return Errored(error=f"`{command}`: {completed.failure}")

    line = _last_line(completed.stdout)
    if line is None:
        return Errored(error=_unreadable(command, completed, "wrote nothing to stdout"))

    try:
        document = json.loads(line)
    except ValueError as exc:
        return Errored(
            error=_unreadable(command, completed, f"last stdout line is not JSON: {exc}")
        )

    try:
        return VerifyResultAdapter.validate_python(document)
    except ValidationError as exc:
        return Errored(
            error=_unreadable(
                command,
                completed,
                f"last stdout line is not a verdict or an exception:\n{exc}",
            )
        )


async def _run_shell(command: str, cwd: Path, timeout: float) -> _Completed:
    """One command through a shell, bounded, with its whole process group killed on expiry.

    Both pipes are drained as the command runs, because a verifier that fills a
    pipe deadlocks against anything that waits for it to exit first, and because
    what it printed before a timeout is evidence rather than noise.

    A timeout does not always mean the verifier overran. A command that leaves
    something running in the background exits at once and its descendant holds
    the pipes open; the shell's own exit status and the verdict already read are
    what separate that from a verifier that never answered.

    The command runs in its own session, so an interrupt at the terminal never
    reaches it. A cancelled run therefore kills the group itself before the
    cancellation propagates; otherwise every verifier in flight outlives the
    run that started it.

    `asyncio.timeout()` rather than `asyncio.wait_for()`: before Python 3.12,
    `wait_for` can swallow a cancellation that arrives as the awaited call
    completes (CPython gh-86296), which would turn an interrupt into a finished
    cell.
    """
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        return _Completed(None, "", "", f"could not be started: {exc}")

    printed: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    readers = (
        asyncio.create_task(_drain(process.stdout, printed["stdout"])),
        asyncio.create_task(_drain(process.stderr, printed["stderr"])),
    )
    try:
        async with asyncio.timeout(timeout):
            await asyncio.gather(*readers)
            await process.wait()
    except TimeoutError:
        _kill_group(process)
        for reader in readers:
            reader.cancel()
        await _reap(process)
        if process.returncode == 0 and _last_line(_joined(printed["stdout"])) is not None:
            return _Completed(0, _joined(printed["stdout"]), _joined(printed["stderr"]), None)
        return _Completed(None, "", "", f"exceeded {timeout:g}s and was killed")
    except asyncio.CancelledError:
        _kill_group(process)
        for reader in readers:
            reader.cancel()
        await _reap(process)
        raise

    return _Completed(
        process.returncode, _joined(printed["stdout"]), _joined(printed["stderr"]), None
    )


async def _drain(stream: asyncio.StreamReader | None, into: list[bytes]) -> None:
    """Read one pipe to its end, keeping what arrived where the caller can reach it.

    Reading rather than `communicate()`, because `communicate()` accumulates
    inside a task of its own: cancelled at a timeout, everything the verifier
    printed is lost with it. A verifier that answered its cell and then left
    something in the background holds the pipe open without having failed, and
    its verdict is exactly what must survive.

    Chunked rather than by line, because a pipe nobody empties fills, and a
    verifier blocked on a full pipe never exits.
    """
    if stream is None:
        return
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return
        into.append(chunk)


async def _reap(process: asyncio.subprocess.Process) -> None:
    """Collect a killed process, and its pipes, before the loop that owns them ends.

    Awaiting inside a cancelled task is what makes this possible: the
    cancellation has already been delivered, so cleanup may await once more
    before re-raising it. The wait is bounded because a process that has been
    sent SIGKILL either ends or is unkillable, and a run being interrupted must
    not hang on the second case.

    Left to the garbage collector instead, the child is collected after the loop
    is gone: the destructors then report a still-running subprocess and a closed
    event loop, which reach a reader who pressed Ctrl-C as though Witnessed had
    crashed.
    """
    with contextlib.suppress(BaseException):
        async with asyncio.timeout(_REAP_TIMEOUT):
            await process.wait()
    transport = getattr(process, "_transport", None)
    if transport is not None:
        with contextlib.suppress(Exception):
            transport.close()


def _kill_group(process: asyncio.subprocess.Process) -> None:
    """Kill the shell and everything it started.

    The process held here is the shell, so signalling it alone leaves the
    verifier it spawned running. The command was started in its own session, so
    its group id is its process id, and the group is signalled by that pid
    directly: asking the system for the group instead fails once the shell has
    been reaped, which is precisely the case where a descendant is the only
    thing still holding the pipes -- and then nothing was killed at all.
    """
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError, OSError):
            process.kill()


def _last_line(stdout: str) -> str | None:
    for line in reversed(stdout.splitlines()):
        if line.strip():
            return line
    return None


def _joined(chunks: list[bytes]) -> str:
    return b"".join(chunks).decode("utf-8", errors="replace")


def _output(completed: _Completed) -> str:
    """Everything the command printed, in the shape a failure report prints it."""
    parts = []
    if completed.stdout.strip():
        parts.append(completed.stdout.rstrip())
    if completed.stderr.strip():
        parts.append(f"{_STDERR_HEADING}\n{completed.stderr.rstrip()}")
    return "\n".join(parts)


def _unreadable(command: str, completed: _Completed, detail: str) -> str:
    """The account of why a cell reads errored, including what the command printed.

    An errored cell has no verdict to carry evidence, so this string is the only
    place a render or a maintainer can learn what the runner could not do.
    """
    lines = [f"`{command}` exited {completed.returncode} and {detail}"]
    printed = _output(completed)
    if printed:
        lines.append(printed)
    return "\n".join(lines)
