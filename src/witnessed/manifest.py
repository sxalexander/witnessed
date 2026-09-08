"""Reading one manifest file into a `Grid`, and addressing the cells of one.

A manifest arrives as untrusted text and leaves as a validated `Grid` or as a
sentence a person can act on. Every failure here is a `ValueError` carrying
pydantic's own message with its framing removed, because the caller that
reports it prints a message rather than a traceback: the builder of a violin's
articulation grid is owed the offending key, not forty frames of `_pytest/`.

Discovery is not here. pytest collection finds `*.grid.yaml`; this module
answers only for a path already chosen, and the path itself stays with the
caller, because a `Grid` describes a claim rather than a file and `setup` and
`verify` run in the directory the caller read.
"""

from collections.abc import Iterator, Mapping
from os import PathLike
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from witnessed.model import Excepted, Grid, cell_key

DIMENSION_PLACEHOLDER = "{dimension}"
VARIANT_PLACEHOLDER = "{variant}"

_PYDANTIC_VALUE_ERROR_PREFIX = "Value error, "


def load(path: str | PathLike[str]) -> Grid:
    """The grid a manifest file declares, or a `ValueError` saying what stopped it.

    Unreadable bytes, malformed YAML, and a schema violation are one failure to
    the caller — the manifest did not load — so they are one exception type.
    Distinguishing them would only ask a collector to catch three things to
    print the same sentence.
    """
    manifest_path = Path(path)
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        detail = getattr(exc, "strerror", None) or exc
        raise ValueError(f"{manifest_path} could not be read: {detail}") from exc

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{manifest_path} is not valid YAML:\n{_indent(str(exc))}") from exc

    if document is None:
        raise _invalid(manifest_path, ["the file is empty"])
    if not isinstance(document, Mapping):
        raise _invalid(
            manifest_path,
            [f"a manifest is a mapping of keys, not a {type(document).__name__}"],
        )

    try:
        return Grid.model_validate(document)
    except ValidationError as exc:
        raise _invalid(manifest_path, [_readable(error) for error in exc.errors()]) from exc


def cells(grid: Grid) -> Iterator[tuple[str, str]]:
    """Every coordinate the grid asserts, row-major.

    Totality is generated from the axes rather than declared, so this is the
    only enumeration of a grid's cells and a cell cannot be omitted from it.
    """
    return grid.cells()


def excepted(grid: Grid, dimension: str, variant: str) -> Excepted | None:
    """The exception the manifest declares for a cell, if its author declared one.

    A record is not consulted. A manifest exception and a verifier's are
    separate sources with separate authority, and a loader can only know the
    first: an excepted cell here is one that will never be verified at all.
    """
    return grid.except_.get(cell_key(dimension, variant))


def resolve_verify(grid: Grid, dimension: str, variant: str) -> str:
    """The grid's `verify` command with the coordinate substituted in.

    The result is handed to a shell, so the coordinate must be an axis member
    of this grid and not merely a string: axis members passed the `[a-z0-9-]`
    alphabet during validation, which is what makes substitution incapable of
    introducing a metacharacter. A coordinate from anywhere else has no such
    guarantee, and is refused rather than interpolated.

    Substitution is textual rather than `str.format`, because a `verify` string
    is shell source and may hold braces of its own — `awk '{print $1}'` is a
    command, not a format field. An axis member cannot contain a brace, so the
    two replacements cannot feed each other.
    """
    if dimension not in grid.dimensions or variant not in grid.variants:
        raise ValueError(
            f"cell `{cell_key(dimension, variant)}` is outside "
            f"grid `{grid.id}`'s product, so its `verify` command is undefined"
        )
    return grid.verify.replace(DIMENSION_PLACEHOLDER, dimension).replace(
        VARIANT_PLACEHOLDER, variant
    )


def _invalid(manifest_path: Path, problems: list[str]) -> ValueError:
    return ValueError(
        f"{manifest_path} is not a valid manifest:\n"
        + "\n".join(_indent(problem) for problem in problems)
    )


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" for line in text.splitlines())


def _readable(error: dict[str, Any]) -> str:
    """One pydantic error as a line an author can act on.

    pydantic frames a validator's own words as `Value error, …` and reports a
    pattern mismatch without the value that missed it. Stripping the frame
    keeps a message the spec commits to intact; naming the offending scalar is
    what stops *String should match pattern* from sending an author back to
    guess which of forty rows is wrong.
    """
    message = error["msg"]
    if message.startswith(_PYDANTIC_VALUE_ERROR_PREFIX):
        message = message[len(_PYDANTIC_VALUE_ERROR_PREFIX) :]

    offender = error.get("input")
    if offender is None or isinstance(offender, (str, int, float, bool)):
        message = f"{message} (got {offender!r})"

    location = ".".join(str(part) for part in error["loc"])
    return f"{location}: {message}" if location else message
