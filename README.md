# Witnessed

Witnessed maintains witness grids: a table whose rows are the things a claim
asserts, whose columns are the contexts the claim must survive, and whose
every cell is either proven by a verifier that ran or closed with a stated
reason. A cell that stops being true is reported on the run that observes it,
and named with the date it was last true.

It exists because "not checked" and "does not apply" look identical from the
outside and mean opposite things.

A cell is witnessed when a verifier says so and by no other route. The
manifest carries exceptions, never states, so there is no field to type
`witnessed` into.

Built as a pytest plugin: pytest owns collection, filtering, and outcomes.
Witnessed owns execution, the run record, the policy, and the grid.

[QUICKSTART.md](QUICKSTART.md) builds a working grid in five minutes.

## A stop condition an agent cannot argue with

An autonomous loop needs a finish line stated outside the work. `witnessed
verify` is one command with one exit code: under `on_gap: fail` it exits 0 only
when every cell of the claim is proven, and 1 while any cell is red or has lost
coverage it used to have.

```
witnessed verify grids/                   # 0 when the claim holds, 1 when it does not
witnessed gap grids/ --export | jq -r .prompt   # one line of work per red cell
```

A grid is therefore usable as the goal of a loop, and the gap export as the
queue that feeds it: one prompt per cell that is not yet true. The property
that makes this safe is the same one the grid exists for — a cell is proven by
a verifier in its own process, so the agent that did the work is never the one
that reports the work is done.

Two things a loop cannot learn from its own transcript, and reads off a grid
instead: a context nobody ran reads `?` rather than being absent, which is the
platform, locale, or device that was never checked; and a cell that was green
and is now red reads as a regression rather than as a gap, so a loop that broke
something it had already finished says so.

[Practical loop engineering](https://addyosmani.com/blog/practical-loop-engineering/)
describes the surrounding practice, including why a separate agent should
verify what another produced.

## Not in scope

- Run history beyond each cell's current observation and its last witnessed one.
- Comparing evidence across runs to catch depth thinning before a threshold.
- Receiving a status from outside a verifier run: manual records, attestations,
  human sign-off.
- Grids with more than two axes, and references between grids. A third axis is
  a second grid.
- An interactive TUI.
- Domain-specific integrations. A corpus is a file tree and a verifier is a
  command over it.

Python, pydantic, `uv`.
