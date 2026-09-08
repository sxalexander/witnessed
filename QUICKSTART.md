# Quickstart

A witness grid in five minutes. Every command below is run in order; each one
prints what this page says it prints.

The example claims *every guide exists in every supported language*. It needs
no build, no test framework, and no language runtime beyond Witnessed itself.

## Install

```
uv add git+https://github.com/<owner>/witnessed
```

Or, to try it without adding a dependency:

```
uvx --from git+https://github.com/<owner>/witnessed witnessed --version
```

## A corpus to make claims about

```
mkdir -p docs/en
echo "# Install" > docs/en/install.md
echo "# Usage"   > docs/en/usage.md
```

Two guides, one language. The claim is that there should be three guides in
two languages.

## The grid

Write `docs.grid.yaml` **beside the thing it describes**, not in a
subdirectory. A verifier runs in the manifest's own directory, so a manifest
at the project root sees the project's paths.

```yaml
witnessed: 1
id: docs
claim: "Every guide exists in every supported language"

dimensions: [install, usage, api]
variants:   [en, es]

verify: "test -f docs/{variant}/{dimension}.md && echo '{\"ok\": true}' || echo '{\"ok\": false}'"

policy:
  on_gap: report
  on_regression: fail
```

Six cells, and nothing says which are true. There is no field for that. A
cell is witnessed when the verifier says so.

The verifier is any command that prints one JSON object on its last line.
This one is a shell test; yours might be a script that reads an asset
database, a graph, or an API.

## The first run

```
witnessed verify docs.grid.yaml
```

```
key  + witnessed  - failed  ! errored  ~ excepted  ? unknown  * regression

docs  on_gap: report  on_regression: fail
  Every guide exists in every supported language

           en  es
  install  +   -
  usage    +   -
  api      -   -
```

Two witnessed, four gaps, exit 0 — `on_gap: report` means a gap is news, not
a failure. Nothing was written down to produce that picture. The grid asked
six questions and a verifier answered each one.

## Closing a gap

```
mkdir -p docs/es
echo "# Instalación" > docs/es/install.md
witnessed verify docs.grid.yaml
```

```
           en  es
  install  +   +
  usage    +   -
  api      -   -
```

`install/es` is green because a file exists, not because anything was edited
in the manifest. That is the whole discipline: to make a cell green, make the
claim true.

## Closing a cell that should never be green

Some cells are not gaps. Say the API guide is generated from source and the
generator has no Spanish output. Add an exception, with a reason:

```yaml
except:
  api/es:
    why: unimplemented
    reason: "the API guide is generated from source; the generator has no Spanish output"
```

```
           en  es
  install  +   +
  usage    +   -
  api      -   ~
```

`~` is excepted, and it is a different mark from `-`. That difference is the
reason this tool exists: *nobody checked* and *this does not apply* look
identical in a hand-maintained table and mean opposite things.

`why` is `unimplemented` when the subject is absent by choice, and
`not-applicable` when it must never exist. Both require a reason.

## Losing coverage

Delete the Spanish install guide, the way a refactor or a bad merge would:

```
rm docs/es/install.md
witnessed verify docs.grid.yaml
```

```
           en  es
  install  +   -*
  usage    +   -
  api      -   ~

  install/es  regression: last witnessed at rev a1b2c3
```

```
echo $?
1
```

`-*` is a regression, not a gap, and it exits 1 even though the other four
reds exit 0. The grid remembers that this cell was green and names the
revision it was green at. That is the event nothing else catches: no test
failed, no build broke, and the coverage is gone.

Commit `.witnessed/runs.json` so a fresh clone — CI in particular — knows what
was green before.

## The gaps as work

```
witnessed gap docs.grid.yaml --export
```

```json
{"grid": "docs", "cell": "usage/es", "state": "failed", "regression": false,
 "prompt": "build a solution for usage/es"}
```

One JSON object per gap. The `prompt` is rendered from the grid's template, or
the built-in default. Pipe it wherever work comes from:

```
witnessed gap docs.grid.yaml --export | jq -r .prompt
```

## Reading the grid elsewhere

```
witnessed check docs.grid.yaml --md    > COVERAGE.md
witnessed check docs.grid.yaml --html  > coverage.html
witnessed check docs.grid.yaml --json  | jq '.grids.docs.cells[] | select(.state != "witnessed")'
```

`check` renders what is already known and runs nothing. Only `verify` runs
verifiers.

## What to know next

- A grid has two axes. A third axis is a second grid.
- `on_gap: fail` turns the grid into an acceptance gate. `report` keeps it a
  roadmap. One project can hold both.
- A verifier may itself return an exception — `{"why": "not-applicable",
  "reason": "..."}` — when the corpus knows something the author would
  otherwise have to retype.
- Witnessed is a pytest plugin. `-k` filters, node ids are
  `<grid>::<dimension>::<variant>`, and a project's own `pytest` run never
  touches grids.

[docs/product.md](docs/product.md) is who this is for and what is out of
scope. [docs/technical.md](docs/technical.md) is the model, the grammar, and
every decision with its rationale.
