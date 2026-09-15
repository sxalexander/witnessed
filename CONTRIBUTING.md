# Contributing

Witnessed is maintained on a fixed weekly budget. Every rule below exists so
that a contribution reaches a maintainer only when it has already been agreed
to, and so that nothing waits on a person except a security report.

## Issues

Open an issue with one of the two forms: **Bug** or **Proposal**. Blank issues
are disabled, and questions are not answered in issues;
[QUICKSTART.md](QUICKSTART.md) and [docs/](docs/) are the support surface.

Issues are triaged weekly. Triage ends one of two ways:

- the `approved` label: the work is wanted, and a pull request for it is welcome
- closed as not planned, with a one-line reason

An issue that has not been approved 30 days after its last activity is closed
automatically as not planned. That closure is the scope boundary being held,
not a judgement of the idea; [docs/product.md](docs/product.md) states what is
out of scope.

## Pull requests

A pull request from outside the maintainers must close an **approved** issue,
written in its description as `Closes #123`. A pull request that does not is
closed automatically with an explanation, and reopens itself when its
description is edited to reference an approved issue.

Each contributor may have one open pull request at a time. Draft pull requests
do not count.

Pull requests are reviewed weekly. Work beyond a week's review capacity waits
for the next week.

A pull request is ready for review when:

- `uv run pytest` passes
- `uv run witnessed verify grids/` exits 0
- a change to documented behaviour changes [QUICKSTART.md](QUICKSTART.md) or
  [docs/technical.md](docs/technical.md) in the same pull request

## Compatibility

| | supported |
|---|---|
| Python | 3.11 and newer, each until CPython ends its security support |
| pydantic, pytest, PyYAML | feature releases from the last two years |
| Witnessed | the latest release only |

The Python floor is set by the projects Witnessed is added to rather than by
Witnessed itself: a dependency's `requires-python` constrains every project
that depends on it, so the floor moves only when CPython drops a version
([status of Python versions](https://devguide.python.org/versions/)). Library
floors follow [SPEC 0](https://scientific-python.org/specs/spec-0000/). CI tests
the floors and the latest releases; a weekly run against upstream pre-releases
opens an `upstream-break` issue when one breaks Witnessed.

An upstream release that breaks Witnessed is handled by releasing an upper
bound first and a fix afterwards, so a user is never left with a broken
install while the fix is written.

## Security

Never in a public issue. See [SECURITY.md](SECURITY.md).
