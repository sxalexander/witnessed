"""The domain model of a witness grid.

The model exists to make one property structural rather than conventional: a
cell reaches `witnessed` only when a verifier returns `ok=True`. No class here
carries a field that names an observed state, so a manifest author has nowhere
to type one. What a manifest may say is which cells are closed, and why.

Every model forbids unknown fields. That is what stops a manifest from growing
a per-cell key beside `except`, and what keeps the three members of an
`Observation.result` union from matching each other's payloads.
"""

from collections.abc import Iterator
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationInfo
from pydantic import field_validator, model_validator

Id = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$")]
"""An axis member, a grid id, and every other name a coordinate is built from.

Constrained to `[a-z0-9-]` so that interpolating a coordinate into a `verify`
string cannot introduce a shell metacharacter: the only code a manifest runs is
code its author wrote.
"""


class ExceptionKind(StrEnum):
    """Why a cell is closed from view. A human or a verifier may declare one."""

    NOT_APPLICABLE = "not-applicable"  # the subject must never exist here
    UNIMPLEMENTED = "unimplemented"  # the subject is absent by choice


class Excepted(BaseModel):
    """A judgment that a cell is closed, and why.

    Both kinds require a reason because both hide a cell from view, and that is
    the property needing justification. An `Excepted` cannot express a reading
    of the world; only a `Verdict` can.
    """

    model_config = ConfigDict(extra="forbid")

    why: ExceptionKind
    reason: str = Field(min_length=1)


class Verdict(BaseModel):
    """A verifier's reading of a cell. ok=True is the only route to witnessed.

    `evidence` is whatever the verifier found worth reporting. The grid asks no
    question of it: depth thresholds belong inside the verifier, not in a
    comparison Witnessed performs.
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    evidence: dict = Field(default_factory=dict)


class Errored(BaseModel):
    """The verifier did not produce a result. Assigned by the runner, never returned.

    Absent from `VerifyResult`, so a verifier that emits an `error` field is
    rejected rather than believed: a process cannot report its own failure to
    report.
    """

    model_config = ConfigDict(extra="forbid")

    error: str


VerifyResult = Verdict | Excepted
"""Everything a verifier is permitted to say: a reading, or a closure."""

VerifyResultAdapter: TypeAdapter[VerifyResult] = TypeAdapter(VerifyResult)
"""Parser for the single JSON object a verifier writes to its last stdout line.

Anything this rejects is `Errored`, which is the runner's to assign.
"""


class Observation(BaseModel):
    """One reading of one cell, at a time and a revision.

    `rev` is what a regression report names, so a maintainer learns not only
    that a cell emptied but the revision it was last true at.
    """

    model_config = ConfigDict(extra="forbid")

    at: datetime
    rev: str | None = None
    result: Verdict | Excepted | Errored


class CellRecord(BaseModel):
    """The whole memory of one cell: what it is, and the last time it was witnessed.

    Two observations are enough. A regression is `witnessed -> not witnessed`,
    which needs exactly one prior fact rather than a history, and a bounded
    record has no ordering problem and needs no merge driver.
    """

    model_config = ConfigDict(extra="forbid")

    current: Observation
    last_witnessed: Observation | None = None

    @model_validator(mode="after")
    def _last_witnessed_actually_witnessed(self) -> "CellRecord":
        """A prior observation that did not witness the cell is not evidence of one.

        Nothing in this package writes such a record, so one can only arrive by
        hand or from another tool. Loaded rather than refused, it reports a
        regression naming a revision at which the verifier said the cell was not
        ok, and under the default policy that fails a build on invented evidence.
        """
        prior = self.last_witnessed
        if prior is not None and not (isinstance(prior.result, Verdict) and prior.result.ok):
            raise ValueError("`last_witnessed` must hold a verdict with `ok: true`")
        return self


class Policy(BaseModel):
    """What a grid's red cells cost.

    Policy is per manifest so that one project can hold a locked grid and an
    in-progress grid at once. A gap defaults to reporting because a rollout row
    is red by intent; a regression defaults to failing because a lost green is
    the event the tool exists to catch.
    """

    model_config = ConfigDict(extra="forbid")

    on_gap: Literal["report", "fail"] = "report"
    on_regression: Literal["report", "fail"] = "fail"


class Grid(BaseModel):
    """A claim, its two axes, and the command that reads any cell of them.

    Totality is generated from the axes, so a cell cannot be omitted and there
    is no per-cell declaration beyond `except`. There is no per-cell `verify`
    either: one command receives the coordinates and answers for every cell,
    which makes a command whose coordinates disagree with its key
    unrepresentable.
    """

    model_config = ConfigDict(extra="forbid")

    witnessed: Literal[1]  # manifest schema version, not a cell state
    id: Id
    claim: str
    dimensions: list[Id]
    variants: list[Id]
    verify: str
    setup: str | None = None
    policy: Policy = Field(default_factory=Policy)
    except_: dict[str, Excepted] = Field(default_factory=dict, alias="except")
    export: dict = Field(default_factory=dict)

    @field_validator("dimensions", "variants")
    @classmethod
    def _members_are_distinct(cls, members: list[str], info: ValidationInfo) -> list[str]:
        """A repeated axis member would place two cells at one coordinate, where
        the second silently overwrites the first in every keyed structure."""
        seen: set[str] = set()
        duplicates: list[str] = []
        for member in members:
            if member in seen:
                duplicates.append(member)
            seen.add(member)
        if duplicates:
            raise ValueError(f"{info.field_name} contains duplicate members: {duplicates}")
        return members

    @model_validator(mode="after")
    def _except_keys_are_inside_the_product(self) -> "Grid":
        """An exception addressing a cell that does not exist closes nothing, and
        survives the axis edit that made it meaningless."""
        product = {cell_key(dimension, variant) for dimension, variant in self.cells()}
        for key in self.except_:
            if key not in product:
                raise ValueError(f"`except` key `{key}` is outside the grid's product")
        return self

    def cells(self) -> Iterator[tuple[str, str]]:
        """Every coordinate the grid asserts, row-major."""
        for dimension in self.dimensions:
            for variant in self.variants:
                yield dimension, variant


class CellState(StrEnum):
    """The five states a render must hold apart.

    `unknown` is a state rather than a blank because *never checked* and *does
    not apply* look identical from the outside and mean opposite things.
    """

    WITNESSED = "witnessed"
    FAILED = "failed"
    ERRORED = "errored"
    EXCEPTED = "excepted"
    UNKNOWN = "unknown"


def cell_key(dimension: str, variant: str) -> str:
    """The one spelling of a coordinate inside a grid.

    Used by the run file, the render, the export, and `except` keys, so that a
    cell is addressed the same way wherever it is named.
    """
    return f"{dimension}/{variant}"


def cell_state(
    grid: Grid,
    dimension: str,
    variant: str,
    record: CellRecord | None = None,
) -> CellState:
    """The state of one cell, in the order of precedence the spec sets.

    A manifest exception outranks any record: the verifier does not run for a
    cell the author closed, so a record left behind by an earlier shape of the
    grid cannot reopen it.
    """
    if cell_key(dimension, variant) in grid.except_:
        return CellState.EXCEPTED
    if record is None:
        return CellState.UNKNOWN
    result = record.current.result
    if isinstance(result, Excepted):
        return CellState.EXCEPTED
    if isinstance(result, Errored):
        return CellState.ERRORED
    return CellState.WITNESSED if result.ok else CellState.FAILED


def exception_for(
    grid: Grid,
    dimension: str,
    variant: str,
    record: CellRecord | None = None,
) -> Excepted | None:
    """The closure behind an excepted cell, from whichever source declared it.

    Every render target must reach an excepted cell's reason, and the precedence
    that decides which of two exceptions applies is the same one `cell_state`
    uses.
    """
    declared = grid.except_.get(cell_key(dimension, variant))
    if declared is not None:
        return declared
    if record is not None and isinstance(record.current.result, Excepted):
        return record.current.result
    return None


def is_gap(state: CellState) -> bool:
    """Whether a cell is a hole rather than merely unproven.

    An unknown cell is not a gap: nothing has been learned about it, so a fresh
    clone has zero gaps until someone runs.
    """
    return state in (CellState.FAILED, CellState.ERRORED)


def is_regression(state: CellState, record: CellRecord | None = None) -> bool:
    """Whether a cell that was once witnessed no longer is.

    A rollout row has no `last_witnessed` and a broken row does. That is the
    whole distinction between a red cell that is expected and one that is news.
    """
    return record is not None and record.last_witnessed is not None and state != CellState.WITNESSED
