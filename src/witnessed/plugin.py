"""The pytest plugin: grids are files, cells are items, and the run is one fan-out.

pytest owns collection, node ids, `-k`, `--collect-only`, skip-with-reason,
outcomes, and the plugin system — fifteen years of undifferentiated work.
What it does not own is execution, the run file, the policy, and the render,
and those are the product. So the plugin collects like a pytest plugin and
runs like a batch job: every cell is verified in `pytest_collection_finish`,
before pytest's loop begins, and the loop then only reports what was stashed.

The plugin is inert unless enabled. There is no `pytest11` entry point, so a
project's own `pytest` invocation never collects a grid, never runs a grid's
`setup`, and never spawns a verifier; `-p witnessed.plugin` is the only way in.

pytest's terminal reporter is unregistered, because its vocabulary — passed,
failed, skipped — collapses `errored` into `failed`, and a cell that errored
told the maintainer nothing while a cell that failed told them something. The
grid is printed in its place.
"""

import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

import pytest

from witnessed import manifest, state
from witnessed.model import (
    Errored,
    Excepted,
    Grid,
    cell_key,
    cell_state,
    is_gap,
    is_regression,
)
from witnessed.render import build_view
from witnessed.runner import (
    DEFAULT_CONCURRENCY,
    DEFAULT_TIMEOUT,
    Job,
    Outcome,
    fan_out,
    run_setup,
)

MANIFEST_SUFFIX = ".grid.yaml"
"""What discovery recognises. The `grids/` folder is a convention; this is the rule."""

_DECLARED_IDS = pytest.StashKey[dict[str, Path]]()
"""Which manifest declared which grid id, for the session that collected them.

Two manifests declaring one id would key one grid's records against the other's
cells, so the second is a collection error rather than a silent overwrite.
"""

_DECLARED_GRIDS = pytest.StashKey[dict[str, Grid]]()
"""Every grid the session collected, by id, whether or not a cell of it is selected.

A `--grid` or `--cell` that names nothing is a usage error rather than an empty
run, and telling the two apart needs the grids a selection could have named.
"""

OUTCOME = pytest.StashKey[Outcome]()
"""Where the fan-out leaves a cell's result for the run loop to read.

The stash rather than an attribute, because the item is pytest's object and the
result is Witnessed's, and pytest documents the stash as the seam between them.
"""

_ERRORED_PROPERTY = ("witnessed", "errored")
"""What marks a failed report as errored rather than failed.

pytest has four outcomes and Witnessed distinguishes five states, so the cell
that pytest cannot spell travels in `user_properties`, where JUnit XML and any
reporting plugin can still read it.
"""


def pytest_addoption(parser: pytest.Parser) -> None:
    """The knobs the CLI turns, spelled so they cannot collide.

    Every option is prefixed because this plugin is loaded into a session that
    may hold others: `-n` belongs to `pytest-xdist` wherever it is installed,
    and a grid run that silently became a distributed run would spawn one idle
    interpreter per waiting verifier.
    """
    group = parser.getgroup("witnessed", "witness grids")
    group.addoption(
        "--witnessed-state-dir",
        dest="witnessed_state_dir",
        default=None,
        metavar="PATH",
        help="directory holding runs.json; defaults to WITNESSED_STATE_DIR or <rootdir>/.witnessed",
    )
    group.addoption(
        "--witnessed-concurrency",
        dest="witnessed_concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        metavar="N",
        help="how many verifiers may be alive at once",
    )
    group.addoption(
        "--witnessed-timeout",
        dest="witnessed_timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help="seconds one verifier may run before it is killed and its cell reads errored",
    )
    group.addoption(
        "--witnessed-grid",
        dest="witnessed_grids",
        action="append",
        default=[],
        metavar="ID",
        help="select every cell of this grid; repeatable",
    )
    group.addoption(
        "--witnessed-cell",
        dest="witnessed_cells",
        action="append",
        default=[],
        metavar="GRID/DIMENSION/VARIANT",
        help="select this cell; repeatable",
    )


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    """Take the terminal away from pytest and register the session's runner.

    `trylast` because the reporter does not exist until `_pytest.terminal`
    configures, and a hook that ran first would find nothing to unregister.
    """
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        config.pluginmanager.unregister(reporter)
    config.pluginmanager.register(WitnessedRunner(config), "witnessed-runner")


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> "GridFile | None":
    """Every `*.grid.yaml` is a grid, wherever it lives.

    Discovery is by collection rather than a registry, because a grid absent
    from a registry produces no error and no cell — a silent absence, which is
    the failure the tool exists to prevent.
    """
    if file_path.name.endswith(MANIFEST_SUFFIX):
        return GridFile.from_parent(parent, path=file_path)
    return None


class GridFile(pytest.File):
    """One manifest, and the cells its axes generate.

    Totality is generated here rather than declared, so a cell cannot be
    omitted from a grid by forgetting to write it down.
    """

    def collect(self):
        try:
            grid = manifest.load(self.path)
        except ValueError as exc:
            raise self.CollectError(str(exc)) from exc

        declared = self.session.stash.setdefault(_DECLARED_IDS, {})
        claimed_by = declared.get(grid.id)
        if claimed_by is not None and claimed_by != self.path:
            raise self.CollectError(
                f"grid id `{grid.id}` is declared by both {claimed_by} and {self.path}"
            )
        declared[grid.id] = self.path
        self.session.stash.setdefault(_DECLARED_GRIDS, {})[grid.id] = grid

        for dimension, variant in grid.cells():
            item = CellItem.from_parent(
                self,
                name=_node_id(grid.id, dimension, variant),
                nodeid=_node_id(grid.id, dimension, variant),
                grid=grid,
                dimension=dimension,
                variant=variant,
                closure=manifest.excepted(grid, dimension, variant),
            )
            if item.closure is not None:
                item.add_marker(pytest.mark.skip(reason=_closure_reason(item.closure)))
            yield item


class CellItem(pytest.Item):
    """One cell of one grid, addressed by the grid's id rather than by its file.

    The node id is `<grid>::<dimension>::<variant>` so that `-k`, the render,
    the export, and the run file all spell a cell the same way, and so that
    moving a manifest does not rename every cell in it.
    """

    def __init__(
        self,
        *,
        grid: Grid,
        dimension: str,
        variant: str,
        closure: Excepted | None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.grid = grid
        self.dimension = dimension
        self.variant = variant
        self.closure = closure

    @property
    def coordinate(self) -> tuple[str, str]:
        return self.dimension, self.variant

    def runtest(self) -> None:
        """Report what the fan-out already learned. Nothing is verified here.

        A cell with no stash was never reached — its grid's items were filtered
        out of the session after collection, or the run loop began without a
        fan-out — which is itself a thing nobody learned about the cell.
        """
        outcome = self.stash.get(OUTCOME, None)
        if outcome is None:
            self.user_properties.append(_ERRORED_PROPERTY)
            pytest.fail("no verifier ran for this cell", pytrace=False)

        result = outcome.result
        if isinstance(result, Excepted):
            pytest.skip(_closure_reason(result))
        if isinstance(result, Errored):
            self.user_properties.append(_ERRORED_PROPERTY)
            pytest.fail(result.error, pytrace=False)
        if not result.ok:
            pytest.fail(outcome.output or "the verifier reported ok: false", pytrace=False)

    def repr_failure(self, excinfo, style=None):
        """The verifier's own output, never a Python traceback.

        The stack that reaches this point is Witnessed's reporting code, and no
        frame of it is about the cell. What is about the cell is what the
        verifier printed.
        """
        outcome = self.stash.get(OUTCOME, None)
        if outcome is None:
            return super().repr_failure(excinfo, style)
        result = outcome.result
        if isinstance(result, Errored):
            return f"{self.nodeid} errored\n{result.error}"
        return f"{self.nodeid} failed\n{outcome.output}".rstrip()

    def reportinfo(self):
        """Where a report points. The line is zero: a cell is generated from the
        axes, so no line of the manifest declares it."""
        return self.path, 0, self.nodeid


@dataclass
class _GridRun:
    """One grid as the session found it: the manifest, where it runs, and its cells."""

    grid: Grid
    directory: Path
    items: list[CellItem] = field(default_factory=list)


class WitnessedRunner:
    """The session's execution, run file, policy, and render.

    Held as an object rather than module functions so that one session's state
    cannot leak into the next: `pytest.main` may be called many times in one
    interpreter, and a grid remembered across calls would be a grid nobody
    collected.
    """

    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.grids: dict[str, _GridRun] = {}
        self.collect_failed = False
        self.refused = False
        self.verified = False

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        """A manifest that did not load is a wrong input, not a failing test.

        pytest ends a session with collection errors as interrupted; the exit
        code table says 4, because what happened is that a manifest did not
        load and no cell was read either way.
        """
        if not report.failed:
            return
        self.collect_failed = True
        self._uncaptured()
        print(report.longrepr, file=sys.stderr)

    def pytest_collection_modifyitems(
        self, session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        """Select by grid id and by coordinate exactly, never by substring.

        `-k` matches a substring of a node id, so `-k docs::` also selects a grid
        named `api-docs`. `--witnessed-grid` and `--witnessed-cell` compare whole
        names instead, and a name that matches nothing collected is a usage
        error: a typo that selected no cell would otherwise report success
        having verified nothing. Several names select their union; `-k`
        narrows that union further.
        """
        grids = config.getoption("witnessed_grids")
        cells = config.getoption("witnessed_cells")
        if self.collect_failed or not (grids or cells):
            return

        known = session.stash.get(_DECLARED_GRIDS, {})
        found = ", ".join(sorted(known)) or "none"
        for grid_id in grids:
            if grid_id not in known:
                self._refuse(f"--grid `{grid_id}` names no collected grid (found: {found})")

        chosen: set[tuple[str, str, str]] = set()
        for spelled in cells:
            parts = spelled.split("/")
            if len(parts) != 3:
                self._refuse(f"--cell takes <grid>/<dimension>/<variant>, not `{spelled}`")
            grid_id, dimension, variant = parts
            grid = known.get(grid_id)
            if grid is None or dimension not in grid.dimensions or variant not in grid.variants:
                self._refuse(f"--cell `{spelled}` names no cell of a collected grid")
            chosen.add((grid_id, dimension, variant))

        selected: list[pytest.Item] = []
        deselected: list[pytest.Item] = []
        for item in items:
            wanted = isinstance(item, CellItem) and (
                item.grid.id in grids or (item.grid.id, item.dimension, item.variant) in chosen
            )
            (selected if wanted else deselected).append(item)
        if deselected:
            config.hook.pytest_deselected(items=deselected)
            items[:] = selected

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """Run every selected cell, once, before the report loop begins.

        Selection has already filtered `session.items`, so a run costs only the
        cells it names, and the records of the cells it does not name are
        neither read nor written.

        A run that would verify nothing is refused rather than reported as
        success. No manifest under the given paths, or a selection that matched
        no cell, means the question asked was not the one intended, and exit 0
        would let a deleted grid or a misspelled filter pass a gate unnoticed.
        A run file that cannot be read is refused before any verifier starts,
        because the records it holds are what a regression is detected from.
        """
        if self.collect_failed or self.refused:
            return
        items = [item for item in session.items if isinstance(item, CellItem)]
        if self.config.option.collectonly:
            self._uncaptured()
            for item in items:
                print(item.nodeid)
            return
        if not session.stash.get(_DECLARED_GRIDS, {}):
            paths = " ".join(self.config.args) or str(self.config.rootpath)
            self._refuse(f"no *{MANIFEST_SUFFIX} found under {paths}")
        if not items:
            self._refuse("the selection matched no cell")
        try:
            state.load(state_dir(self.config))
        except ValueError as exc:
            self._refuse(str(exc))

        for item in items:
            run = self.grids.get(item.grid.id)
            if run is None:
                run = _GridRun(grid=item.grid, directory=item.path.parent)
                self.grids[item.grid.id] = run
            run.items.append(item)

        jobs: list[Job] = []
        targets: list[CellItem] = []
        for run in self.grids.values():
            verifiable = [item for item in run.items if item.closure is None]
            if not verifiable:
                continue
            failure = run_setup(run.grid, run.directory)
            if failure is not None:
                for item in verifiable:
                    item.stash[OUTCOME] = Outcome(result=failure, output=failure.error)
                continue
            for item in verifiable:
                targets.append(item)
                jobs.append(
                    Job(
                        grid=run.grid,
                        dimension=item.dimension,
                        variant=item.variant,
                        command=manifest.resolve_verify(run.grid, item.dimension, item.variant),
                        cwd=run.directory,
                    )
                )

        if jobs:
            outcomes = asyncio.run(
                fan_out(
                    jobs,
                    self.config.getoption("witnessed_concurrency"),
                    self.config.getoption("witnessed_timeout"),
                )
            )
            for item, outcome in zip(targets, outcomes, strict=True):
                item.stash[OUTCOME] = outcome
        self.verified = True

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        """Record what was observed, apply each grid's policy, and print the grid.

        The exit code comes from the policy rather than from pytest's count of
        failures: under `on_gap: report` a red cell is a rollout in progress
        and does not fail the session, and under `on_regression: fail` a cell
        that emptied does even though pytest saw one failure either way.
        """
        if self.collect_failed:
            session.exitstatus = pytest.ExitCode.USAGE_ERROR
            return
        if exitstatus in (
            pytest.ExitCode.INTERRUPTED,
            pytest.ExitCode.INTERNAL_ERROR,
            pytest.ExitCode.USAGE_ERROR,
        ):
            return
        if not self.verified:
            return

        records = self._record()
        session.exitstatus = (
            pytest.ExitCode.TESTS_FAILED if self._breached(records) else pytest.ExitCode.OK
        )
        self._print(records)

    def _record(self) -> state.RunData:
        """Merge this run's observations into the run file, and replace it atomically.

        A grid whose whole product was in the session declares that product, so
        a record whose coordinate the manifest no longer describes is dropped.
        A partial run declares nothing: it has no standing to retire a record
        for a cell it did not run.
        """
        directory = state_dir(self.config)
        records = state.load(directory)
        rev = state.git_rev()

        for grid_id, run in self.grids.items():
            observed = {
                item.coordinate: item.stash[OUTCOME].result
                for item in run.items
                if OUTCOME in item.stash
            }
            if not observed:
                continue
            expected = {
                (dimension, variant)
                for dimension, variant in run.grid.cells()
                if cell_key(dimension, variant) not in run.grid.except_
            }
            records = state.merge(
                records,
                grid_id,
                observed,
                rev,
                product=run.grid.cells() if set(observed) == expected else None,
            )

        state.save(directory, records)
        return records

    def _breached(self, records: state.RunData) -> bool:
        """Whether any grid's policy is unsatisfied by the cells this run selected.

        Only the selected cells are judged, for the same reason only their
        records are written: a developer iterating on one verifier is told
        about that cell, not about every red cell in the project.
        """
        for grid_id, run in self.grids.items():
            grid = run.grid
            held = records.get(grid_id, {})
            for item in run.items:
                record = held.get(cell_key(item.dimension, item.variant))
                observed = cell_state(grid, item.dimension, item.variant, record)
                if is_gap(observed) and grid.policy.on_gap == "fail":
                    return True
                if is_regression(observed, record) and grid.policy.on_regression == "fail":
                    return True
        return False

    def _print(self, records: state.RunData) -> None:
        """Print the grid in the terminal reporter's place.

        The target registry lives with the flags that name targets, and is
        borrowed at print time so that loading the plugin costs a grid session
        nothing the command surface needs.
        """
        from witnessed.cli import render_view

        self._uncaptured()
        print(render_view(build_view([run.grid for run in self.grids.values()], records), "tui"))

    def _refuse(self, message: str) -> NoReturn:
        """End the session as a usage error before any verifier runs.

        pytest calls `pytest_collection_finish` from a `finally` block, so an
        error raised while selecting still reaches it; the flag is what keeps
        that call from verifying the cells of a run that has already failed.
        """
        self.refused = True
        raise pytest.UsageError(message)

    def _uncaptured(self) -> None:
        """Give the terminal back before writing to it.

        Everything this plugin prints is the session's report rather than a
        cell's output, and a report captured into a buffer nobody replays is a
        report nobody reads. A verifier's own output never passes through here:
        it is read from a pipe the runner owns.
        """
        capture = self.config.pluginmanager.get_plugin("capturemanager")
        if capture is not None:
            capture.suspend_global_capture(in_=False)


def state_dir(config: pytest.Config) -> Path:
    """Where this session's records live.

    An explicit directory outranks the environment, and both outrank the
    default, so a fixture or scale run can be told to write somewhere that is
    not a project's committed run file.
    """
    given = config.getoption("witnessed_state_dir", default=None)
    if given:
        return Path(given).expanduser().resolve()
    from_environment = os.environ.get("WITNESSED_STATE_DIR")
    if from_environment:
        return Path(from_environment).expanduser().resolve()
    return Path(config.rootpath) / ".witnessed"


def _node_id(grid_id: str, dimension: str, variant: str) -> str:
    return f"{grid_id}::{dimension}::{variant}"


def _closure_reason(closure: Excepted) -> str:
    """A closed cell's kind and reason as one line.

    Both travel because the kind says whether the subject must never exist here
    or is absent by choice, and those are different promises to a reader.
    """
    return f"{closure.why}: {closure.reason}"


__all__ = [
    "MANIFEST_SUFFIX",
    "OUTCOME",
    "CellItem",
    "GridFile",
    "WitnessedRunner",
    "state_dir",
]
