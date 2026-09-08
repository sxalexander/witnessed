# Witnessed

Witnessed maintains witness grids: a table whose rows are the things a claim
asserts, whose columns are the contexts the claim must survive, and whose
every cell is either proven by a verifier that ran or closed with a stated
reason. A cell that stops being true is reported on the run that observes it,
with the revision it was last true at.

It exists because "not checked" and "does not apply" look identical from the
outside and mean opposite things.

A cell is witnessed when a verifier says so and by no other route. The
manifest carries exceptions, never states, so there is no field to type
`witnessed` into.

Built as a pytest plugin: pytest owns collection, filtering, and outcomes.
Witnessed owns execution, the run record, the policy, and the grid.

[QUICKSTART.md](QUICKSTART.md) builds a working grid in five minutes.
[docs/product.md](docs/product.md) is who this is for and what is out of scope.
[docs/technical.md](docs/technical.md) is the model, the grammar, and every
decision with its rationale.

Python, pydantic, `uv`.
