"""The command surface: `verify` runs a pytest session, `check` and `gap` do not.

Three commands, and the split between them is which one is allowed to learn
something. `verify` is the only command that may run a verifier, so it is the
only one that starts a pytest session and the only one that writes the run
file. `check` and `gap` read the manifests and the records and report what is
already known, which is what makes them safe to run in a loop, in a hook, or
against a state directory someone else is writing.

`verify` exposes a curated set of flags rather than pytest passthrough. Under
batch execution half of pytest's flags are lies: `-x` cannot stop on the first
failure because every cell ran before the first report, and `--pdb` would drop
into a subprocess that has already exited.

The session `verify` starts is isolated in both directions. A project's
`addopts`, `conftest.py`, Python tests and `.pytest_cache/` are neither read
nor written, so running `witnessed verify` in a repository cannot be mistaken
for running that repository's test suite.
"""

import argparse
import importlib
import json
import os
import sys
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from pydantic import ValidationError

from witnessed import manifest
from witnessed import manifest as manifest_module
from witnessed import state
from witnessed.model import Grid, is_gap
from witnessed.render import GridView, build_view
from witnessed.runner import DEFAULT_CONCURRENCY, DEFAULT_TIMEOUT

MANIFEST_SUFFIX = ".grid.yaml"

DEFAULT_PROMPT_TEMPLATE = "build a solution for {cell}"
"""What a gap asks for when its grid names no template.

Deliberately not a prompt-engineering surface: the export's value is the
structured record travelling with it, and a grid whose consumer is a recording
session or an animation request writes its own.
"""

TARGET_MODULES = {
    "tui": "witnessed.render.tui",
    "json": "witnessed.render.json_render",
    "md": "witnessed.render.md_render",
    "html": "witnessed.render.html_render",
}
"""Which module draws which target."""

SKIPPED_DIRECTORIES = frozenset({"_darcs", "build", "CVS", "dist", "node_modules", "venv"})
"""Directories a walk does not descend into, matching pytest's own default.

Discovery by `witnessed check` must find the same manifests pytest collection
finds, or a cell would render from a grid that never runs.
"""

_PROMPT_FIELDS = ("cell", "grid", "dimension", "variant", "claim", "state")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and return its exit code."""
    parser = _parser()
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    return arguments.run(arguments)


def render_view(view: Sequence[GridView], target: str) -> str:
    """The view as one target's text.

    A target whose module is absent resolves to json, which carries every field
    the view holds: a caller asking for a drawing this install cannot make is
    owed the whole reading rather than an error.
    """
    try:
        module = importlib.import_module(TARGET_MODULES[target])
    except ModuleNotFoundError:
        module = importlib.import_module(TARGET_MODULES["json"])
    return module.render(view)


def discover(paths: Sequence[str] | None) -> list[Path]:
    """Every manifest under the given paths, or under the working directory.

    A path naming a file is taken as a manifest whatever it is called, because
    a caller who named it meant it. A path naming a directory is walked for
    `*.grid.yaml`, skipping the directories pytest's own collection skips.
    """
    roots = [Path(path) for path in (paths or [Path.cwd()])]
    found: list[Path] = []
    for root in roots:
        if root.is_file():
            found.append(root)
        elif root.is_dir():
            found.extend(_walk(root))
        else:
            raise ValueError(f"{root} does not exist")
    return sorted(dict.fromkeys(path.resolve() for path in found))


def load_grids(paths: Sequence[str] | None) -> list[tuple[Grid, Path]]:
    """Every discovered manifest as a grid, with the directory its commands run in.

    Two manifests declaring one id would key one grid's records against the
    other's cells, so the second is refused here exactly as it is refused
    during collection.
    """
    loaded: list[tuple[Grid, Path]] = []
    declared: dict[str, Path] = {}
    for path in discover(paths):
        grid = manifest.load(path)
        claimed_by = declared.get(grid.id)
        if claimed_by is not None:
            raise ValueError(f"grid id `{grid.id}` is declared by both {claimed_by} and {path}")
        declared[grid.id] = path
        loaded.append((grid, path.parent))
    return loaded


def state_directory(given: str | None) -> Path:
    """Where the records live: the flag, then the environment, then the default.

    A fixture or scale run is told to write somewhere that is not a project's
    committed run file, which is the whole reason the location is a flag.
    """
    if given:
        return Path(given).expanduser().resolve()
    from_environment = os.environ.get("WITNESSED_STATE_DIR")
    if from_environment:
        return Path(from_environment).expanduser().resolve()
    return Path.cwd() / ".witnessed"


def _verify(arguments: argparse.Namespace) -> int:
    """Run the selected cells in an isolated pytest session.

    The session is given a configuration file that is not the project's, so
    that a project's `addopts` cannot reach a grid run, and `-p no:python` so
    that a bare `witnessed verify` in a repository collects that repository's
    grids and not its unit tests.
    """
    try:
        selection = _selection(arguments)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return int(pytest.ExitCode.USAGE_ERROR)

    with tempfile.TemporaryDirectory(prefix="witnessed-session-") as scratch:
        configuration = Path(scratch) / "pytest.ini"
        configuration.write_text("[pytest]\n", encoding="utf-8")
        session = [
            "-p",
            "witnessed.plugin",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:python",
            "--noconftest",
            "-c",
            str(configuration),
            "--rootdir",
            str(Path.cwd()),
            "--witnessed-state-dir",
            str(state_directory(arguments.state_dir)),
            "--witnessed-concurrency",
            str(arguments.concurrency),
            "--witnessed-timeout",
            str(arguments.timeout),
        ]
        if selection:
            session += ["-k", selection]
        session += [str(path) for path in (arguments.paths or [Path.cwd()])]
        return int(pytest.main(session))


def _check(arguments: argparse.Namespace) -> int:
    """Render what is already known. No verifier runs."""
    try:
        grids = load_grids(arguments.paths)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return int(pytest.ExitCode.USAGE_ERROR)
    records = state.load(state_directory(arguments.state_dir))
    print(render_view(build_view([grid for grid, _ in grids], records), arguments.target))
    return int(pytest.ExitCode.OK)


def _gap(arguments: argparse.Namespace) -> int:
    """One JSON object per gap cell, one per line.

    A gap is a failed or errored cell. An unknown cell is not one: nothing has
    been learned about it, so a fresh clone exports nothing until someone runs.
    """
    try:
        grids = load_grids(arguments.paths)
        templates = {grid.id: _template(grid, directory) for grid, directory in grids}
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return int(pytest.ExitCode.USAGE_ERROR)

    claims = {grid.id: grid.claim for grid, _ in grids}
    records = state.load(state_directory(arguments.state_dir))

    for view in build_view([grid for grid, _ in grids], records):
        for cell in view.cells:
            if not is_gap(cell.state):
                continue
            record = {
                "grid": view.id,
                "claim": claims[view.id],
                "cell": cell.id,
                "dimension": cell.dimension,
                "variant": cell.variant,
                "state": str(cell.state),
                "regression": cell.regression,
                "last_witnessed_rev": cell.last_witnessed_rev,
                "evidence": cell.evidence,
            }
            record["prompt"] = _fill(templates[view.id], record)
            print(json.dumps(record))
    return int(pytest.ExitCode.OK)


STUB_VERIFIER = """#!/usr/bin/env python3
\"\"\"Answer one cell of the {id} grid.

Receives a dimension and a variant, and prints one JSON object on its last
line. `ok` true is the only route to a witnessed cell; `why` with a reason
closes a cell the corpus knows can never be green.
\"\"\"

import json
import sys

dimension, variant = sys.argv[1], sys.argv[2]

# Read the corpus and decide. Until this is written, every cell is honestly red.
print(json.dumps({{"ok": False, "evidence": {{"note": "verifier not implemented"}}}}))
"""


def _init(arguments: argparse.Namespace) -> int:
    """Write one manifest, and a stub verifier when no command was supplied.

    The manifest is built as a `Grid` before it is serialised, so a written
    manifest is one the loader accepts. `init` takes no exceptions: a cell is
    excepted by reacting to a run, never before one.
    """
    directory = Path(arguments.path)
    identifier = arguments.id
    verify = arguments.verify or f"verify/{identifier} {{dimension}} {{variant}}"

    fields = {
        "witnessed": 1,
        "id": identifier,
        "claim": arguments.claim,
        "dimensions": _members(arguments.dimensions),
        "variants": _members(arguments.variants),
        "verify": verify,
        "policy": {"on_gap": arguments.on_gap, "on_regression": arguments.on_regression},
    }
    if arguments.setup:
        fields["setup"] = arguments.setup
    if arguments.template:
        fields["export"] = {"template": arguments.template}

    try:
        grid = Grid.model_validate(fields)
    except ValidationError as invalid:
        for problem in invalid.errors():
            sys.stderr.write(f"witnessed init: {manifest_module._readable(problem)}\n")
        return int(pytest.ExitCode.USAGE_ERROR)

    document = _manifest_text(grid)
    if arguments.stdout:
        sys.stdout.write(document)
        return 0

    manifest = directory / f"{identifier}.grid.yaml"
    if manifest.exists() and not arguments.force:
        sys.stderr.write(f"witnessed init: {manifest} exists; pass --force to overwrite\n")
        return int(pytest.ExitCode.USAGE_ERROR)

    directory.mkdir(parents=True, exist_ok=True)
    manifest.write_text(document)
    written = [manifest]

    if not arguments.verify:
        stub = directory / "verify" / identifier
        if not stub.exists() or arguments.force:
            stub.parent.mkdir(parents=True, exist_ok=True)
            stub.write_text(STUB_VERIFIER.format(id=identifier))
            stub.chmod(0o755)
            written.append(stub)

    for path in written:
        sys.stdout.write(f"{path}\n")
    cells = len(grid.dimensions) * len(grid.variants)
    sys.stdout.write(f"\n{cells} cells, none witnessed. Run: witnessed verify {manifest}\n")
    return 0


def _members(raw: str) -> list[str]:
    """Split a comma-separated axis, dropping the empty members a trailing
    comma or a stray space would otherwise introduce."""
    return [member.strip() for member in raw.split(",") if member.strip()]


def _manifest_text(grid: Grid) -> str:
    """Serialise a validated grid in the order the grammar documents.

    Round-tripping through a YAML dumper would sort keys and fold the verify
    command, so the document is composed directly.
    """
    lines = [
        f"witnessed: {grid.witnessed}",
        f"id: {grid.id}",
        f"claim: {json.dumps(grid.claim)}",
        "",
        f"dimensions: [{', '.join(grid.dimensions)}]",
        f"variants:   [{', '.join(grid.variants)}]",
        "",
    ]
    if grid.setup:
        lines.append(f"setup:  {json.dumps(grid.setup)}")
    lines.append(f"verify: {json.dumps(grid.verify)}")
    lines += [
        "",
        "policy:",
        f"  on_gap: {grid.policy.on_gap}",
        f"  on_regression: {grid.policy.on_regression}",
    ]
    if grid.export:
        lines += ["", "export:", f"  template: {json.dumps(grid.export['template'])}"]
    lines += [
        "",
        "# A cell is witnessed when the verifier says so. To close one the verifier",
        "# can never answer, add it here after a run, with a reason:",
        "#",
        "# except:",
        f"#   {grid.dimensions[0]}/{grid.variants[0]}:",
        "#     why: not-applicable",
        '#     reason: "..."',
        "",
    ]
    return "\n".join(lines)


def _template(grid: Grid, directory: Path) -> str:
    """The prose a gap of this grid asks for, from the grid's template or the default.

    A template path resolves against the manifest's directory, like every other
    relative path a manifest names, so a grid works from wherever it is run.
    """
    named = grid.export.get("template")
    if not named:
        return DEFAULT_PROMPT_TEMPLATE
    path = Path(named)
    if not path.is_absolute():
        path = directory / path
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"grid `{grid.id}` export template {path} could not be read: {exc}")


def _fill(template: str, record: dict) -> str:
    """A template with the cell's fields substituted in.

    Substitution is textual rather than `str.format`, because a template is
    prose and may hold braces of its own; a coordinate cannot contain a brace,
    so the replacements cannot feed each other.
    """
    filled = template
    for field in _PROMPT_FIELDS:
        filled = filled.replace("{" + field + "}", str(record[field]))
    return filled


def _selection(arguments: argparse.Namespace) -> str:
    """`-k`, `--grid` and `--cell` as one pytest expression.

    `--grid` and `--cell` are spellings of `-k` over the node id, so a grid or
    a cell is named the same way on the command line, in the render, and in the
    run file. Several are combined with `and`, which is what makes `--grid x -k
    vue` narrow rather than replace.
    """
    expressions: list[str] = []
    if arguments.k:
        expressions.append(f"({arguments.k})")
    if arguments.grid:
        expressions.append(f"{arguments.grid}::")
    for cell in arguments.cell or []:
        parts = cell.split("/")
        if len(parts) != 3:
            raise ValueError(f"--cell takes <grid>/<dimension>/<variant>, not `{cell}`")
        expressions.append("::".join(parts))
    return " and ".join(expressions)


def _walk(root: Path) -> Iterator[Path]:
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            if not _skipped(entry.name):
                yield from _walk(entry)
        elif entry.name.endswith(MANIFEST_SUFFIX):
            yield entry


def _skipped(name: str) -> bool:
    return name.startswith(".") or name.endswith(".egg") or name in SKIPPED_DIRECTORIES


class _Parser(argparse.ArgumentParser):
    """An argument parser whose usage errors carry Witnessed's exit code.

    A wrong flag and a manifest that did not load are the same failure to a
    caller — the run never started — and the exit table gives them one number.
    """

    def error(self, message: str):
        self.print_usage(sys.stderr)
        self.exit(int(pytest.ExitCode.USAGE_ERROR), f"{self.prog}: error: {message}\n")


def _parser() -> argparse.ArgumentParser:
    from witnessed import __version__

    parser = _Parser(prog="witnessed", description="Maintain witness grids.")
    parser.add_argument("--version", action="version", version=f"witnessed {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="write one manifest and a stub verifier")
    init.add_argument("--id", required=True, metavar="ID", help="the grid's identifier")
    init.add_argument(
        "--claim", required=True, metavar="TEXT", help="the sentence the grid defends"
    )
    init.add_argument(
        "--dimensions", required=True, metavar="A,B,C", help="the rows, comma separated"
    )
    init.add_argument(
        "--variants", required=True, metavar="X,Y", help="the columns, comma separated"
    )
    init.add_argument(
        "--verify",
        metavar="COMMAND",
        help="the command answering one cell; a stub verifier is written when this is omitted",
    )
    init.add_argument("--path", default=".", metavar="DIR", help="where to write (default: .)")
    init.add_argument("--setup", metavar="COMMAND", help="a command run once before any verifier")
    init.add_argument(
        "--template", metavar="PATH", help="a jinja template rendering the gap prompt"
    )
    init.add_argument("--on-gap", choices=("report", "fail"), default="report", dest="on_gap")
    init.add_argument(
        "--on-regression", choices=("report", "fail"), default="fail", dest="on_regression"
    )
    init.add_argument("--stdout", action="store_true", help="print the manifest and write nothing")
    init.add_argument("--force", action="store_true", help="overwrite an existing manifest")
    init.set_defaults(run=_init)

    verify = commands.add_parser("verify", help="run every selected cell's verifier")
    verify.add_argument("paths", nargs="*", metavar="PATH")
    verify.add_argument("-k", dest="k", metavar="EXPR", help="pytest expression over node ids")
    verify.add_argument("--grid", metavar="ID", help="restrict the run to one grid")
    verify.add_argument(
        "--cell", action="append", metavar="GRID/DIM/VAR", help="restrict the run to one cell"
    )
    verify.add_argument(
        "-n",
        dest="concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        metavar="N",
        help="how many verifiers may be alive at once",
    )
    verify.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help="seconds one verifier may run before its cell reads errored",
    )
    verify.add_argument("--state-dir", metavar="PATH")
    verify.set_defaults(run=_verify)

    check = commands.add_parser("check", help="render what is already known")
    check.add_argument("paths", nargs="*", metavar="PATH")
    target = check.add_mutually_exclusive_group()
    for name in TARGET_MODULES:
        target.add_argument(
            f"--{name}", dest="target", action="store_const", const=name, help=f"render as {name}"
        )
    check.add_argument("--state-dir", metavar="PATH")
    check.set_defaults(run=_check, target="tui")

    gap = commands.add_parser("gap", help="emit one JSON object per gap cell")
    gap.add_argument("paths", nargs="*", metavar="PATH")
    gap.add_argument("--export", action="store_true", required=True, help="write the records")
    gap.add_argument("--state-dir", metavar="PATH")
    gap.set_defaults(run=_gap)

    return parser


__all__ = [
    "DEFAULT_PROMPT_TEMPLATE",
    "TARGET_MODULES",
    "discover",
    "load_grids",
    "main",
    "render_view",
    "state_directory",
]
