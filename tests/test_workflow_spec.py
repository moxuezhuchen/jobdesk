"""Tests for ``jobdesk_app.core.workflow_spec``.

The ConfFlow Pydantic models are an optional dependency. These tests verify
the wrapper behaves correctly regardless: graceful degradation when the
package is missing, round-trip serialization when it is available.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobdesk_app.core import workflow_spec
from jobdesk_app.core.workflow_spec import (
    ConfFlowUnavailableError,
    DryRunReport,
    WorkflowSpec,
    assemble_orca_keyword,
    require_confflow,
    write_workflow_yaml,
)


def test_require_confflow_raises_when_unavailable():
    """require_confflow raises ConfFlowUnavailableError if confflow missing."""
    if workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package is installed; behavior is covered by other tests")
    with pytest.raises(ConfFlowUnavailableError):
        require_confflow()


def test_from_yaml_round_trip_when_confflow_available(tmp_path: Path):
    """Round-trip serialization preserves shape when confflow is installed."""
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    yaml_text = (
        "work_dir: hexane_work\n"
        "calc:\n"
        "  program: gaussian\n"
        "  method: B3LYP\n"
        "  basis: 6-31G(d)\n"
        "  charge: 0\n"
        "  multiplicity: 1\n"
        "  nproc: 8\n"
        "  memory_mb: 4096\n"
        "  steps:\n"
        "    - confgen\n"
        "    - preopt\n"
        "    - opt\n"
        "    - refine\n"
        "    - sp\n"
    )
    spec = WorkflowSpec.from_yaml(yaml_text)
    serialized = spec.to_yaml()
    # Round-trip should produce identical payload on the second pass.
    again = WorkflowSpec.from_yaml(serialized)
    assert again.to_yaml() == serialized


def test_to_form_includes_known_fields_when_available():
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    spec = WorkflowSpec.from_form(
        work_dir_name="hexane_work",
        program="gaussian",
        method="B3LYP",
        basis="6-31G(d)",
        charge=0,
        multiplicity=1,
        nproc=8,
        memory_mb=4096,
        steps=("confgen", "opt"),
    )
    form = spec.to_form()
    assert form["program"] == "gaussian"
    assert form["method"] == "B3LYP"
    assert form["nproc"] == 8
    assert form["steps"] == ["confgen", "opt"]


def test_dry_run_ok_when_round_trip_works(tmp_path: Path):
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    spec = WorkflowSpec.from_form(
        work_dir_name="water_work",
        program="orca",
        method="B3LYP",
        basis="def2-TZVP",
        charge=0,
        multiplicity=1,
        nproc=4,
        memory_mb=2048,
    )
    report = spec.dry_run()
    assert isinstance(report, DryRunReport)
    assert report.ok is True
    assert report.error == ""


def test_write_workflow_yaml_is_atomic(tmp_path: Path):
    """write_workflow_yaml replaces the target atomically."""
    target = tmp_path / "nested" / "workflow.yaml"
    if workflow_spec._CONFFLOW_AVAILABLE:
        spec = WorkflowSpec.from_form(
            work_dir_name="x",
            program="gaussian",
            method="HF",
            basis="3-21G",
            charge=0,
            multiplicity=1,
            nproc=1,
            memory_mb=1024,
        )
        write_workflow_yaml(spec, target)
        # Final file must exist; .tmp sidecar must not leak.
        assert target.exists()
        assert not target.with_suffix(target.suffix + ".tmp").exists()
        # The payload is YAML, not JSON; assert it parses as YAML and contains
        # the work_dir we set on the form. Pre-fix, this assertion used
        # ``json.loads`` which never matched the YAML output.
        import yaml

        parsed = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        assert parsed != {}
        # v6 schema: ``work_dir`` lives under the canonical
        # ``global`` section (the confflow loader shape).
        assert parsed.get("global", {}).get("work_dir") == "x"
        # Each step has a ``name``; ``type`` is omitted when it is the
        # default value ``calc`` (the wizard's only wizard-visible
        # step type).
        steps = parsed.get("steps") or []
        assert isinstance(steps, list) and steps
        assert all("name" in s for s in steps)
        for s in steps:
            assert s.get("type") in (None, "calc", "confgen", "gen", "task")
    else:
        # Graceful path: missing confflow raises a typed error.
        with pytest.raises(ConfFlowUnavailableError):
            write_workflow_yaml(WorkflowSpec(global_config=None), target)


# ----------------------------------------------------------------------------
# Phase 7: ORCA keyword assembly.
# ----------------------------------------------------------------------------


def test_assemble_orca_keyword_basic():
    """method + basis join with a single space; no leading '!'."""
    assert assemble_orca_keyword("B3LYP", "def2-svp") == "B3LYP def2-svp"
    assert assemble_orca_keyword("b3lyp", "def2-svp") == "b3lyp def2-svp"


def test_assemble_orca_keyword_strips_bang():
    """User-pasted '! method basis' must drop the leading '!'."""
    assert assemble_orca_keyword("! B3LYP", "def2-svp") == "B3LYP def2-svp"
    assert assemble_orca_keyword("!! B3LYP", "def2-svp") == "B3LYP def2-svp"
    assert assemble_orca_keyword("B3LYP", "! def2-svp") == "B3LYP def2-svp"


def test_assemble_orca_keyword_extra_tokens():
    """Extra tokens (e.g. Opt, MiniPrint) are appended and '!'-stripped."""
    assert assemble_orca_keyword("b3lyp", "def2-svp", "Opt MiniPrint") == "b3lyp def2-svp Opt MiniPrint"
    assert assemble_orca_keyword("b3lyp", "def2-svp", "! Opt MiniPrint") == "b3lyp def2-svp Opt MiniPrint"


def test_assemble_orca_keyword_skips_empty_components():
    """An empty method or basis is dropped, never inserted as whitespace."""
    assert assemble_orca_keyword("", "def2-svp") == "def2-svp"
    assert assemble_orca_keyword("B3LYP", "") == "B3LYP"
    assert assemble_orca_keyword("", "") == ""


def test_from_form_passes_keyword_for_orca(tmp_path: Path):
    """When program is ORCA and no manual keyword is supplied, from_form must
    auto-assemble the keyword from method + basis."""
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    spec = WorkflowSpec.from_form(
        work_dir_name="methane_work",
        program="orca",
        method="B3LYP",
        basis="def2-svp",
        charge=0,
        multiplicity=1,
        nproc=1,
        memory_mb=512,
    )
    yaml_text = spec.to_yaml()
    assert "keyword:" in yaml_text
    assert "B3LYP def2-svp" in yaml_text
    # No '!!' from the ORCA template + user '!'.
    assert "!!" not in yaml_text


def test_from_form_orca_keyword_keeps_user_override():
    """When the user supplies their own keyword via extra_options, do not
    overwrite it."""
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    spec = WorkflowSpec.from_form(
        work_dir_name="x",
        program="orca",
        method="B3LYP",
        basis="def2-svp",
        charge=0,
        multiplicity=1,
        nproc=1,
        memory_mb=512,
        extra_options={"keyword": "B3LYP def2-TZVP Opt TightSCF"},
    )
    yaml_text = spec.to_yaml()
    assert "def2-TZVP" in yaml_text
    # ``def2-svp`` should appear once — only as the top-level ``basis:``
    # field. The user override wins for the ``keyword:`` block, so the
    # auto-assembled ``B3LYP def2-svp`` must not show up under
    # ``calc.keyword``.
    import re

    keyword_match = re.search(r"keyword:\s*([^\n]+)", yaml_text)
    assert keyword_match is not None, "missing keyword: line in workflow YAML"
    keyword_value = keyword_match.group(1)
    assert "def2-TZVP" in keyword_value
    assert "def2-svp" not in keyword_value, (
        f"user-supplied keyword overrode the auto-assembled one; got keyword={keyword_value!r}"
    )


def test_from_form_gaussian_does_not_force_keyword():
    """v6: ``from_form`` always emits ``keyword`` in the first calc
    step's params (the engine builds the Gaussian input file from
    it). What we *don't* do is invent a keyword when both method and
    basis are blank.

    Engine-facing YAML is an exact workflow snapshot, so the explicit
    program and task fields are retained as well.
    """
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    spec = WorkflowSpec.from_form(
        work_dir_name="x",
        program="gaussian",
        method="B3LYP",
        basis="6-31G(d)",
        charge=0,
        multiplicity=1,
        nproc=8,
        memory_mb=4096,
        steps=("opt_freq",),
    )
    yaml_text = spec.to_yaml()
    # keyword is now nested in the first calc step's ``params``.
    assert "keyword: B3LYP 6-31G(d)" in yaml_text
    # ``type: calc`` is required by confflow — always emitted.
    assert "type: calc" in yaml_text
    assert "itask: opt_freq" in yaml_text
    assert "iprog: gaussian" in yaml_text


def test_from_yaml_rejects_invalid_step_task():
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    with pytest.raises(ValueError, match="invalid itask"):
        WorkflowSpec.from_yaml(
            """\
global: {}
steps:
  - name: invalid
    type: calc
    params:
      iprog: orca
      itask: not_a_task
      keyword: B3LYP def2-SVP
"""
        )


def test_from_yaml_rejects_canonical_confgen_without_chains():
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    with pytest.raises(ValueError, match="requires 'chains'"):
        WorkflowSpec.from_yaml(
            """\
global: {}
steps:
  - name: invalid_confgen
    type: confgen
    params: {}
"""
        )


def test_from_form_orca_step_emits_iprog_override():
    """ORCA overrides the global ``iprog`` default, so each calc
    step should surface its ``iprog: orca`` field.
    """
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    spec = WorkflowSpec.from_form(
        work_dir_name="x",
        program="orca",
        method="B3LYP",
        basis="def2-SVP",
        charge=0,
        multiplicity=1,
        nproc=8,
        memory_mb=4096,
        steps=("opt_freq",),
    )
    yaml_text = spec.to_yaml()
    assert "iprog: orca" in yaml_text
    assert "keyword: B3LYP def2-SVP" in yaml_text


# ── Phase 6: wizard-facing YAML split ────────────────────────────────────


def test_to_user_yaml_omits_global_block_and_default_type():
    """v6 phase-6 wizard YAML omits the ``global:`` block entirely and
    hides ``type: calc``. Resources live in the Global settings
    card; ``type: calc`` is the engine default the wizard hides.
    """
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    spec = WorkflowSpec.from_form(
        work_dir_name="x",
        program="gaussian",
        method="B3LYP",
        basis="6-31G(d)",
        charge=0,
        multiplicity=1,
        nproc=8,
        memory_mb=4096,
        steps=("opt_freq",),
    )
    text = spec.to_user_yaml()
    assert "global:" not in text
    assert "type: calc" not in text
    assert "type:" not in text  # no step has any type now
    # Step + keyword still shown.
    assert "name: opt_freq" in text
    assert "keyword: B3LYP 6-31G(d)" in text


def test_to_user_yaml_keeps_confgen_type_visible():
    """``type: confgen`` is non-default and must be preserved in the
    wizard's editor view.
    """
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    spec = WorkflowSpec.from_form(
        work_dir_name="x",
        program="gaussian",
        method="B3LYP",
        basis="6-31G(d)",
        charge=0,
        multiplicity=1,
        nproc=8,
        memory_mb=4096,
        steps=("confgen", "preopt", "opt"),
    )
    text = spec.to_user_yaml()
    assert "type: confgen" in text  # non-default — still surfaced
    assert "type: calc" not in text  # default — still hidden


def test_to_user_yaml_keeps_orca_iprog_override():
    """When ``iprog`` differs from the global ``gaussian`` default, it
    is still useful information and remains visible in the wizard view.
    """
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    spec = WorkflowSpec.from_form(
        work_dir_name="x",
        program="orca",
        method="B3LYP",
        basis="def2-SVP",
        charge=0,
        multiplicity=1,
        nproc=8,
        memory_mb=4096,
        steps=("opt_freq",),
    )
    text = spec.to_user_yaml()
    assert "iprog: orca" in text


def test_user_yaml_round_trip_via_jobdesk_validation():
    """``to_user_yaml`` → ``from_yaml`` (after merging hidden fields
    back) must still pass JobDesk's validation and produce valid YAML.

    This is the contract the workflow page ``Apply`` button relies on.
    We use JobDesk's own validation module instead of an external ConfFlow
    loader to keep this workflow-editor contract self-contained.
    """
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    spec = WorkflowSpec.from_form(
        work_dir_name="round_trip",
        program="orca",
        method="B3LYP",
        basis="def2-TZVP",
        charge=0,
        multiplicity=1,
        nproc=8,
        memory_mb=16384,
        steps=("preopt", "sp"),
    )
    user_yaml = spec.to_user_yaml()
    # Simulate the wizard Apply path: inject the hidden fields
    # back, then build a fresh spec.
    import yaml as yamllib

    base = spec._raw
    user_data = yamllib.safe_load(user_yaml) or {}
    # Re-attach global from base + lift top-level keys via the
    # normaliser (the wizard's merge routine does the same).
    from jobdesk_app.core.workflow_spec import _normalise_yaml_to_schema

    normalised = _normalise_yaml_to_schema(user_data)
    merged = {
        "global": {**(base.get("global") or {}), **(normalised.get("global") or {})},
        "steps": normalised.get("steps") or [],
    }
    merged_text = yamllib.safe_dump(
        merged,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    rebuilt = WorkflowSpec.from_yaml(merged_text)

    # Validate using JobDesk's own workflow-editor validator.
    from jobdesk_app.core._confflow_validation import validate_yaml_config

    rebuilt_yaml = rebuilt.to_yaml()
    rebuilt_data = yamllib.safe_load(rebuilt_yaml)
    errors = validate_yaml_config(rebuilt_data)
    assert errors == [], f"Round-tripped YAML failed validation: {errors}"
    assert isinstance(rebuilt_data, dict)
    assert "steps" in rebuilt_data
    assert len(rebuilt_data["steps"]) >= 1
