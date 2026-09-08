"""Tests of loading a manifest file and addressing the cells of what loaded.

Manifests are written to disk as text rather than built as dicts, because the
loader's whole subject is the journey from bytes to a `Grid` — a YAML syntax
error and an empty file are failures no dict can express.

The two valid manifests are copied from the spec: the grammar example and the
render self-grid. If a documented manifest does not load, the spec and the
loader disagree and one of them is wrong.
"""

import pytest

from witnessed.manifest import cells, excepted, load, resolve_verify
from witnessed.model import ExceptionKind

FRAMEWORKS = """
witnessed: 1
id: frameworks
claim: "Every core component renders in every supported framework"

dimensions: [button, container, text-input, voice-input]
variants:   [react, vue, angular, web-components]

setup:  "pnpm build"
verify: "verify/check-framework {dimension} {variant}"

policy:
  on_gap: report
  on_regression: fail

except:
  container/web-components:
    why: not-applicable
    reason: "container is a layout primitive; web components compose it, not implement it"

export:
  template: grids/prompts/implement.md.j2
"""

RENDER_SELF_GRID = """
witnessed: 1
id: render
claim: "Witnessed renders any grid to any target"
dimensions: [cell-state, except-reason, regression, gap-policy, multi-grid]
variants:   [tui, json, md, html]
setup: |
  set -e
  mkdir -p .witnessed/snap .fixtures/demo/.witnessed
  cp .fixtures/demo/seed-runs.json .fixtures/demo/.witnessed/runs.json
verify: "verify/claim-check .witnessed/snap {dimension} {variant}"
policy: { on_gap: fail, on_regression: fail }
except:
  except-reason/tui:
    why: not-applicable
    reason: "an 80-column table has no room for prose; the json target carries it"
"""

MINIMAL = """
witnessed: 1
id: frameworks
claim: "Every core component renders in every supported framework"
dimensions: [button, container]
variants: [react, vue]
verify: "verify/check-framework {dimension} {variant}"
"""


@pytest.fixture
def write(tmp_path):
    """Put a manifest on disk and hand back its path."""

    def _write(text: str, name: str = "frameworks.grid.yaml"):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    return _write


def loaded(write, text: str = MINIMAL):
    return load(write(text))


# --- A documented manifest loads -------------------------------------------


def test_the_grammar_example_loads(write):
    grid = loaded(write, FRAMEWORKS)
    assert grid.id == "frameworks"
    assert grid.claim == "Every core component renders in every supported framework"
    assert grid.dimensions == ["button", "container", "text-input", "voice-input"]
    assert grid.variants == ["react", "vue", "angular", "web-components"]
    assert grid.setup == "pnpm build"
    assert grid.verify == "verify/check-framework {dimension} {variant}"
    assert grid.policy.on_gap == "report"
    assert grid.policy.on_regression == "fail"
    assert grid.export == {"template": "grids/prompts/implement.md.j2"}


def test_the_render_self_grid_loads(write):
    """The self-grid uses a block `setup` and a flow-mapping `policy`."""
    grid = loaded(write, RENDER_SELF_GRID)
    assert grid.policy.on_gap == "fail"
    assert grid.setup is not None and grid.setup.startswith("set -e\n")
    assert excepted(grid, "except-reason", "tui") is not None


def test_a_manifest_without_setup_export_or_except_loads(write):
    grid = loaded(write)
    assert grid.setup is None
    assert grid.export == {}
    assert grid.except_ == {}


def test_a_path_is_accepted_as_a_string(write):
    assert load(str(write(MINIMAL))).id == "frameworks"


# --- Identifiers -----------------------------------------------------------


def test_a_bad_dimension_identifier_is_rejected(write):
    with pytest.raises(ValueError) as caught:
        loaded(write, MINIMAL.replace("[button, container]", '["Button", container]'))
    assert "dimensions.0" in str(caught.value)
    assert "'Button'" in str(caught.value)


def test_a_bad_variant_identifier_is_rejected(write):
    with pytest.raises(ValueError) as caught:
        loaded(write, MINIMAL.replace("[react, vue]", '[react, "voice input"]'))
    assert "variants.1" in str(caught.value)
    assert "'voice input'" in str(caught.value)


def test_a_bad_grid_id_is_rejected(write):
    with pytest.raises(ValueError, match="id:"):
        loaded(write, MINIMAL.replace("id: frameworks", "id: Frameworks"))


def test_a_hyphenated_lowercase_identifier_is_accepted(write):
    grid = loaded(write, MINIMAL.replace("[button, container]", "[voice-input, text-input]"))
    assert grid.dimensions == ["voice-input", "text-input"]


# --- Except keys -----------------------------------------------------------


def test_an_except_key_outside_the_product_is_rejected(write):
    """The message the spec commits to, reaching the author unframed."""
    text = (
        MINIMAL
        + """
except:
  pizz/altissimo:
    why: not-applicable
    reason: "instrument range ends at C7"
"""
    )
    with pytest.raises(ValueError) as caught:
        loaded(write, text)
    assert "`except` key `pizz/altissimo` is outside the grid's product" in str(caught.value)


def test_an_except_key_inside_the_product_loads(write):
    text = (
        MINIMAL
        + """
except:
  container/vue:
    why: unimplemented
    reason: "not built"
"""
    )
    grid = loaded(write, text)
    closed = excepted(grid, "container", "vue")
    assert closed is not None
    assert closed.why is ExceptionKind.UNIMPLEMENTED
    assert closed.reason == "not built"


def test_an_exception_without_a_reason_is_rejected(write):
    text = (
        MINIMAL
        + """
except:
  container/vue:
    why: not-applicable
"""
    )
    with pytest.raises(ValueError, match=r"except\.container/vue\.reason"):
        loaded(write, text)


def test_a_cell_the_manifest_did_not_close_has_no_exception(write):
    assert excepted(loaded(write), "button", "react") is None


# --- The manifest schema version and unknown keys --------------------------


def test_a_manifest_without_a_version_is_rejected(write):
    with pytest.raises(ValueError, match="witnessed: Field required"):
        loaded(write, MINIMAL.replace("witnessed: 1\n", ""))


def test_a_manifest_with_an_unknown_version_is_rejected(write):
    with pytest.raises(ValueError, match="witnessed: Input should be 1"):
        loaded(write, MINIMAL.replace("witnessed: 1", "witnessed: 2"))


def test_a_per_cell_key_other_than_except_is_rejected(write):
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        loaded(write, MINIMAL + "\ncells:\n  button/vue: witnessed\n")


# --- Text that is not a manifest -------------------------------------------


def test_malformed_yaml_is_rejected_without_a_traceback(write):
    with pytest.raises(ValueError) as caught:
        loaded(write, "witnessed: 1\nid: [unclosed\n")
    message = str(caught.value)
    assert "not valid YAML" in message
    assert "Traceback" not in message


def test_an_empty_file_is_rejected(write):
    with pytest.raises(ValueError, match="the file is empty"):
        loaded(write, "")


def test_a_yaml_document_that_is_not_a_mapping_is_rejected(write):
    with pytest.raises(ValueError, match="a manifest is a mapping of keys, not a list"):
        loaded(write, "- button\n- container\n")


def test_a_missing_file_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="could not be read"):
        load(tmp_path / "absent.grid.yaml")


def test_a_file_that_is_not_text_is_rejected_by_name(tmp_path):
    """A decoding failure names the manifest; a bare `UnicodeDecodeError` names
    only the byte, which tells a collector nothing to print."""
    path = tmp_path / "binary.grid.yaml"
    path.write_bytes(b"\xff\xfe\x00garbage")
    with pytest.raises(ValueError, match="could not be read"):
        load(path)


def test_a_failure_message_names_the_file_and_carries_no_traceback(write):
    path = write(MINIMAL.replace("id: frameworks", "id: Frameworks"))
    with pytest.raises(ValueError) as caught:
        load(path)
    message = str(caught.value)
    assert str(path) in message
    assert "Traceback" not in message
    assert "ValidationError" not in message
    assert "pydantic" not in message


# --- Substitution ----------------------------------------------------------


def test_substitution_fills_both_coordinates(write):
    grid = loaded(write, FRAMEWORKS)
    assert (
        resolve_verify(grid, "voice-input", "web-components")
        == "verify/check-framework voice-input web-components"
    )


def test_substitution_leaves_a_shell_pipeline_as_written(write):
    text = MINIMAL.replace(
        'verify: "verify/check-framework {dimension} {variant}"',
        'verify: "verify/check {dimension} {variant} | jq -c ."',
    )
    assert (
        resolve_verify(loaded(write, text), "button", "vue") == "verify/check button vue | jq -c ."
    )


def test_substitution_survives_braces_the_command_owns(write):
    """A `verify` string is shell source; `awk '{print $1}'` is a command, not a
    format field."""
    text = MINIMAL.replace(
        'verify: "verify/check-framework {dimension} {variant}"',
        "verify: \"verify/check {dimension} {variant} | awk '{print $1}'\"",
    )
    resolved = resolve_verify(loaded(write, text), "button", "vue")
    assert resolved == "verify/check button vue | awk '{print $1}'"


def test_a_placeholder_may_appear_more_than_once(write):
    text = MINIMAL.replace(
        'verify: "verify/check-framework {dimension} {variant}"',
        'verify: "check {dimension}/{variant} out/{dimension}"',
    )
    assert resolve_verify(loaded(write, text), "button", "vue") == "check button/vue out/button"


def test_a_command_naming_no_placeholder_resolves_unchanged(write):
    text = MINIMAL.replace(
        'verify: "verify/check-framework {dimension} {variant}"',
        'verify: "verify/check-everything"',
    )
    assert resolve_verify(loaded(write, text), "button", "vue") == "verify/check-everything"


def test_a_coordinate_outside_the_product_has_no_command(write):
    """Only an axis member is known to have passed the shell-safe alphabet."""
    grid = loaded(write)
    for dimension, variant in (("pizz", "vue"), ("button", "altissimo"), ("; rm -rf /", "vue")):
        with pytest.raises(ValueError, match="outside grid `frameworks`'s product"):
            resolve_verify(grid, dimension, variant)


# --- Cells -----------------------------------------------------------------


def test_cells_are_the_product_of_the_axes(write):
    assert list(cells(loaded(write))) == [
        ("button", "react"),
        ("button", "vue"),
        ("container", "react"),
        ("container", "vue"),
    ]


def test_every_cell_resolves_to_a_command(write):
    grid = loaded(write, FRAMEWORKS)
    commands = {resolve_verify(grid, *cell) for cell in cells(grid)}
    assert len(commands) == len(grid.dimensions) * len(grid.variants) == 16
