"""Witnessed maintains witness grids.

A grid is a table whose rows are the things a claim asserts, whose columns are
the contexts the claim must survive, and whose every cell is either proven by a
verifier that ran or closed with a stated reason. A cell that stops being true
is reported on the run that observes it, with the revision it was last true at.
"""

from importlib.metadata import PackageNotFoundError, version

from witnessed.model import (
    CellRecord,
    CellState,
    Errored,
    Excepted,
    ExceptionKind,
    Grid,
    Id,
    Observation,
    Policy,
    Verdict,
    VerifyResult,
    VerifyResultAdapter,
    cell_key,
    cell_state,
    exception_for,
    is_gap,
    is_regression,
)

try:
    __version__ = version("witnessed")
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "0+unknown"
"""What `witnessed --version` reports, read from the installed distribution.

A second spelling of the version in this file is a second thing to bump: a
release whose tag matches `pyproject.toml` would still report the old number
here, and the bug form asks a reporter for exactly that output.
"""

__all__ = [
    "CellRecord",
    "CellState",
    "Errored",
    "ExceptionKind",
    "Excepted",
    "Grid",
    "Id",
    "Observation",
    "Policy",
    "Verdict",
    "VerifyResult",
    "VerifyResultAdapter",
    "__version__",
    "cell_key",
    "cell_state",
    "exception_for",
    "is_gap",
    "is_regression",
]
