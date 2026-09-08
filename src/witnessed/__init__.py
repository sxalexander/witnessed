"""Witnessed maintains witness grids.

A grid is a table whose rows are the things a claim asserts, whose columns are
the contexts the claim must survive, and whose every cell is either proven by a
verifier that ran or closed with a stated reason. A cell that stops being true
is reported on the run that observes it, with the revision it was last true at.
"""

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

__version__ = "0.1.0"

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
