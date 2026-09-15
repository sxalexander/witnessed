# Security

## A manifest is code

A grid's `verify` and `setup` fields are shell commands. `witnessed verify`
runs them with the invoking user's privileges, in the manifest's directory,
for every `*.grid.yaml` it discovers under the paths it is given.
Discovery skips hidden directories and the directories pytest's own
collection skips (`node_modules`, `venv`, `build`, `dist`, and others), so a
vendored dependency's manifests are not run.

Running `witnessed verify` on a directory is therefore equivalent to running
`make` or `npm test` in it. A manifest that executes arbitrary commands is the
design, not a vulnerability; reports of that behaviour are closed as expected.

In scope:

- a manifest that is **not** under a path given to `witnessed verify` causing
  a command to run
- `witnessed check` or `witnessed gap`, which never run verifiers, causing a
  command to run
- a cell id, reason, or verifier output that escapes into a rendered HTML or
  Markdown report as executable markup
- a released distribution whose contents do not match this repository at the
  tagged commit

## Supported versions

Only the latest release. Fixes ship as a new release; nothing is backported.

## Reporting

Use private vulnerability reporting: **Security → Report a vulnerability** on
the repository page. Do not open a public issue.

A report is acknowledged within 7 days. Fix timing depends on severity and is
stated in the acknowledgement.

## Releases

Distributions are published to PyPI only by the tag-triggered workflow in
`.github/workflows/release.yml` through PyPI Trusted Publishing, with no
long-lived upload token. Each file carries a PEP 740 attestation tying it to
that workflow run.
