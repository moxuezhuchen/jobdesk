"""Portable workflow document/codec regression tests (no chem extra needed)."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from jobdesk_app.core import workflow_spec
from jobdesk_app.core.workflow_codec import decode_workflow_yaml, encode_workflow_yaml
from jobdesk_app.core.workflow_mapping import canonical_mapping
from jobdesk_app.core.workflow_schema_lint import lint_workflow_schema
from jobdesk_app.core.workflow_spec import WorkflowSpec
from jobdesk_app.gui.nodegraph.spec_bridge import WorkflowGraphPayload

FIXTURES = Path(__file__).parent / "fixtures" / "workflow_documents"


def test_fixture_manifest_matches_exact_bytes() -> None:
    entries = (FIXTURES / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    names: list[str] = []
    for entry in entries:
        expected, name = entry.split("  ", 1)
        candidate = (FIXTURES / name).resolve()
        assert candidate.parent == FIXTURES.resolve()
        assert name not in names
        names.append(name)
        assert hashlib.sha256(candidate.read_bytes()).hexdigest() == expected
    actual = sorted(path.name for path in FIXTURES.glob("*.yaml"))
    assert sorted(names) == actual


@pytest.mark.parametrize("fixture", sorted(FIXTURES.glob("*.yaml")))
def test_fixture_codec_round_trip_is_lossless(fixture: Path) -> None:
    source = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    rendered = encode_workflow_yaml(decode_workflow_yaml(fixture.read_text(encoding="utf-8")))
    assert yaml.safe_load(rendered) == source


@pytest.mark.parametrize("fixture", sorted(FIXTURES.glob("*.yaml")))
def test_canonical_mapping_keeps_unknown_extensions_and_graph_data(fixture: Path) -> None:
    source = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    mapped = canonical_mapping(decode_workflow_yaml(fixture.read_text(encoding="utf-8")))
    if "steps" in source:
        assert len(mapped["steps"]) == len(source["steps"])
    if fixture.name == "v06_extensions_dag.yaml":
        assert mapped["x-top"] == source["x-top"]
        assert mapped["steps"][3]["inputs"] == ["left", "right"]
        assert mapped["steps"][3]["x-step"] == "preserve-me"


def test_schema_lint_uses_only_the_caller_supplied_schema() -> None:
    schema = {"type": "object", "required": ["global", "steps"], "properties": {"steps": {"type": "array"}}}
    assert lint_workflow_schema({"global": {}, "steps": []}, schema) == []
    assert lint_workflow_schema({"global": {}}, schema) == ["$.steps: required property is missing"]


def test_base_install_probe_does_not_import_confflow() -> None:
    root = Path(__file__).resolve().parents[1]
    env = {**__import__("os").environ, "PYTHONPATH": str(root / "src")}
    code = (
        "import sys; "
        "from jobdesk_app.core.workflow_codec import decode_workflow_yaml; "
        "from jobdesk_app.core.workflow_mapping import canonical_mapping; "
        "from jobdesk_app.core.workflow_schema_lint import lint_workflow_schema; "
        "from jobdesk_app.core import workflow_spec; "
        "workflow_spec._load_confflow_models=lambda:None; "
        "from jobdesk_app.core.workflow_spec import WorkflowSpec; "
        "d=decode_workflow_yaml('global: {}\\nsteps: []\\n'); "
        "assert canonical_mapping(d)['steps']==[]; "
        "assert lint_workflow_schema(d.to_mapping(), {'type':'object'})==[]; "
        "s=WorkflowSpec.from_yaml('global: {}\\nsteps: []\\n'); "
        "assert 'steps:' in s.to_yaml(); "
        "assert s.schema_lint({'type':'object','required':['global','steps']})==[]; "
        "assert not any(k.startswith('confflow') for k in sys.modules)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_facade_can_read_write_and_map_without_chem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workflow_spec, "_load_confflow_models", lambda: None)
    spec = WorkflowSpec.from_yaml(
        "global: {x-global: retain}\nsteps:\n  - name: one\n    type: calc\n    params: {itask: sp, x-param: retain}\n"
    )
    assert spec.global_config is None
    assert yaml.safe_load(spec.to_yaml())["global"]["x-global"] == "retain"
    assert spec.to_form()["steps"] == ["sp"]
    assert spec.schema_lint({"type": "object", "required": ["global", "steps"]}) == []


@pytest.mark.parametrize(
    "text, message",
    [
        ("global: {}\nsteps: oops\n", "steps must be a list"),
        ("global: {}\nsteps:\n - name: x\n   params: oops\n", "params must be a mapping"),
    ],
)
def test_facade_rejects_invalid_shapes_without_silent_rewrite(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        WorkflowSpec.from_yaml(text)


def test_user_yaml_round_trip_retains_step_global_and_top_extensions() -> None:
    spec = WorkflowSpec.from_yaml(
        "global: {x-global: keep}\nx-top: {note: keep}\nsteps:\n"
        " - name: x\n   type: calc\n   disabled: true\n   x-step: keep\n   params: {itask: sp, x-param: keep}\n"
    )
    rebuilt = WorkflowSpec.from_yaml(spec.to_user_yaml())
    parsed = yaml.safe_load(rebuilt.to_yaml())
    assert parsed["global"]["x-global"] == "keep"
    assert parsed["x-top"] == {"note": "keep"}
    assert parsed["steps"][0]["disabled"] is True
    assert parsed["steps"][0]["x-step"] == "keep"


def test_user_yaml_round_trip_retains_wizard_metadata() -> None:
    spec = WorkflowSpec.from_form(
        work_dir_name="must-retain",
        program="gaussian",
        method="HF",
        basis="STO-3G",
        charge=0,
        multiplicity=1,
        nproc=1,
        memory_mb=1024,
        steps=("sp",),
    )
    assert WorkflowSpec.from_yaml(spec.to_user_yaml()).to_form()["work_dir_name"] == "must-retain"


def test_schema_lint_operates_on_the_canonical_migrated_view() -> None:
    spec = WorkflowSpec.from_yaml("version: '0.5'\nprogram: g16\nmethod: HF\nbasis: STO-3G\nsteps: [sp]\n")
    assert spec.schema_lint({"type": "object", "required": ["global", "steps"]}) == []
    parsed = yaml.safe_load(spec.to_yaml())
    assert parsed["steps"][0]["params"]["iprog"] == "g16"
    assert parsed["steps"][0]["params"]["keyword"] == "HF STO-3G"


def test_public_payload_bridge_has_an_explicit_lossless_route_for_dag_fixture() -> None:
    """NodeGraph correctly rejects type-invalid fan-in; the payload bridge
    still provides a lossless document route until UI exposes a matching port.
    """
    source = (FIXTURES / "v06_extensions_dag.yaml").read_text(encoding="utf-8")
    spec = WorkflowSpec.from_yaml(source)
    steps = yaml.safe_load(spec.to_yaml())["steps"]
    rendered = WorkflowGraphPayload(spec=spec, steps=steps).to_yaml()
    parsed = yaml.safe_load(rendered)
    assert parsed["steps"][3]["inputs"] == ["left", "right"]
    assert parsed["steps"][3]["x-step"] == "preserve-me"
