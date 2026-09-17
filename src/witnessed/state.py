"""The run file: the whole memory of what every cell was last observed to be.

`.witnessed/runs.json` is committed, so a fresh clone — CI in particular — can
tell a cell that was never witnessed from one that was and no longer is. Two
observations per cell are enough for that distinction and nothing appends, so
the file stays bounded and needs no merge driver: a conflict is resolved by
keeping either side whole, because re-running `verify` replaces both.

Three properties are structural here rather than conventional. A record reaches
`last_witnessed` only through a `Verdict` with `ok=True`, so the file cannot be
edited into agreement with a claim. A merge touches only the coordinates a run
observed, so a developer iterating on one cell cannot erase the regression
evidence for the rest. And the file is replaced by rename, so an interrupted
run leaves the previous file whole rather than truncated.
"""

import contextlib
import fcntl
import json
import os
import subprocess
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from witnessed.model import (
    CellRecord,
    Errored,
    Observation,
    Verdict,
    VerifyResult,
    cell_key,
)

RUN_FILE_NAME = "runs.json"
"""The one file inside the state directory. `--state-dir` names the directory."""

LOCK_FILE_NAME = ".lock"
"""What `held` locks. Regenerated on demand and never read, so it is not committed."""

Coordinate = tuple[str, str]
"""A cell addressed as its axes yield it, which is what `Grid.cells()` produces."""

RunData = dict[str, dict[str, CellRecord]]
"""Records by grid id, then by `<dimension>/<variant>`.

Keyed by grid id rather than by manifest path because one run file serves every
grid found, and a manifest that moves is the same grid.
"""


class RunFile(BaseModel):
    """The document on disk: a schema version and the records it explains.

    The version is required for the same reason a manifest declares one — the
    first breaking change to the format is otherwise undetectable — and unknown
    fields are forbidden so that a record shape from a later version is refused
    rather than silently dropped on the next write.
    """

    model_config = ConfigDict(extra="forbid")

    witnessed: Literal[1]
    grids: RunData = Field(default_factory=dict)


def run_file_path(state_dir: Path | str) -> Path:
    """Where the records live under a state directory."""
    return Path(state_dir) / RUN_FILE_NAME


def load(state_dir: Path | str) -> RunData:
    """Every record the state directory holds.

    An absent file is the unknown state, not an error: a fresh clone has learned
    nothing about any cell and therefore has no gaps. A file that exists but does
    not parse raises a `ValueError` a person can act on, because discarding
    unreadable records would erase exactly the evidence a regression is detected
    from. The file is committed, so the likeliest cause is a merge left
    unresolved, and that cause is named when its markers are present.
    """
    path = run_file_path(state_dir)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        detail = getattr(exc, "strerror", None) or exc
        raise ValueError(f"{path} could not be read: {detail}") from exc
    try:
        return RunFile.model_validate_json(text).grids
    except ValidationError as exc:
        if any(line.startswith(("<<<<<<< ", ">>>>>>> ")) for line in text.splitlines()):
            raise ValueError(
                f"{path} has unresolved merge conflict markers. Keep either side whole "
                "and run `witnessed verify` to record every cell again."
            ) from exc
        problems = "\n".join(
            f"  {'.'.join(str(part) for part in error['loc']) or '(document)'}: {error['msg']}"
            for error in exc.errors()[:5]
        )
        raise ValueError(f"{path} is not a valid run file:\n{problems}") from exc


@contextlib.contextmanager
def held(state_dir: Path | str) -> Iterator[None]:
    """Hold the state directory for one load-modify-save cycle.

    `save` is atomic, but the cycle around it is not: a run loads the records,
    merges its own observations into them, and writes the result. Two runs that
    load the same records leave only the second run's merge, and the first
    run's cells are not stale but absent -- a cell that was proven can then
    never be recognised as a regression. Concurrent runs are ordinary here: one
    grid per agent, several agents, one project.

    The lock is advisory and POSIX, which is the same assumption the runner
    already makes in signalling a process group.
    """
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / LOCK_FILE_NAME, "w", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def merge(
    existing: RunData,
    grid_id: str,
    results: Mapping[Coordinate, VerifyResult | Errored],
    rev: str | None = None,
    *,
    product: Iterable[Coordinate] | None = None,
    at: datetime | None = None,
) -> RunData:
    """The records a run leaves behind, given the records it found.

    Every observed coordinate gets a new `current`. `last_witnessed` moves only
    under a `Verdict` with `ok=True`; an exception and an error leave the last
    green where it was, so a cell closed after it was witnessed still reports the
    revision it was true at.

    A coordinate absent from `results` is absent from the selection, and is left
    exactly as it was found. Passing `product` declares that this run covered the
    grid's whole product, which is the only condition under which a record whose
    coordinate the manifest no longer describes is dropped: a partial run has no
    standing to retire anything.

    Returns a new mapping; `existing` and the records inside it are untouched.
    """
    observed_at = at if at is not None else datetime.now(UTC)
    merged: RunData = {known: dict(cells) for known, cells in existing.items()}
    cells = merged.setdefault(grid_id, {})

    for coordinate, result in results.items():
        key = cell_key(*coordinate)
        observation = Observation(at=observed_at, rev=rev, result=result)
        prior = cells.get(key)
        cells[key] = CellRecord(
            current=observation,
            last_witnessed=(
                observation
                if _witnesses(result)
                else (prior.last_witnessed if prior is not None else None)
            ),
        )

    if product is not None:
        described = {cell_key(*coordinate) for coordinate in product}
        merged[grid_id] = {key: record for key, record in cells.items() if key in described}

    return merged


def save(state_dir: Path | str, data: RunData) -> Path:
    """Replace the run file with these records, atomically.

    The document is written to a temporary file beside the target and renamed
    over it, so a run killed mid-write leaves the previous file intact rather
    than a truncated one. Keys are sorted because the file is committed and a
    stable order keeps a diff to the cells that actually moved.
    """
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / RUN_FILE_NAME
    document = json.dumps(
        RunFile(witnessed=1, grids=data).model_dump(mode="json"), indent=2, sort_keys=True
    )

    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=RUN_FILE_NAME, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(document + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def is_regression(record: CellRecord) -> bool:
    """Whether this record alone shows a cell that was witnessed and no longer is.

    This reads the record and nothing else, which is what a caller holding the run
    file without its manifests can answer. Policy uses `model.is_regression`
    instead: a cell the author has since closed in the manifest is excepted, and
    an excepted cell reads as a regression here.
    """
    return record.last_witnessed is not None and not _witnesses(record.current.result)


def git_rev() -> str | None:
    """The short HEAD sha, or None where there is no repository to ask.

    A regression report names the revision a cell was last true at, so a run
    outside a repository records `None` rather than refusing to run: the record
    is still evidence of when the cell emptied.
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _witnesses(result: VerifyResult | Errored) -> bool:
    """The single route to witnessed, applied wherever this module decides one."""
    return isinstance(result, Verdict) and result.ok
