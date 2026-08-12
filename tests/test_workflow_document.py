"""Focused contracts for the Phase 4B workflow authoring boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from jobdesk_app.core import workflow_spec
from jobdesk_app.core.workflow_codec import WorkflowCodec
from jobdesk_app.core.workflow_document import WorkflowDocument
from jobdesk_app.core.workflow_editor import (
    MigrationPolicy,
    lint_workflow,
    require_migration_policy,
)
from jobdesk_app.core.workflow_spec import WorkflowSpec
from jobdesk_app.core.workflow_validation import ConfFlowCompatibilityValidator


def _rich_canonical_yaml() -> str:
    return """\
global:
  cores_per_task: 4
  total_memory: 4GB
  advanced_options:
    producer_future_flag: true
  aliases:
    opt: optimization
steps:
  - name: confgen
    type: confgen
    disabled: false
    aliases: [ensemble]
    params:
      chains: [1-2-3-4]
      angle_step: 120
    inputs: []
    fan_out: [opt, freq]
    future_step_field:
      keep: me
  - name: opt
    type: calc
    disabled: true
    params:
      itask: opt
      iprog: gaussian
      keyword: HF 3-21G
      advanced_options:
        producer_future_flag: 7
    inputs: [confgen]
    fan_in: [confgen]
  - name: freq
    type: calc
    params:
      itask: freq
      iprog: gaussian
      keyword: HF 3-21G
    inputs: [confgen]
"""


def test_document_round_trip_keeps_unknown_fields_and_dag_shape() -> None:
    document = WorkflowCodec.loads(_rich_canonical_yaml())
    assert isinstance(document, WorkflowDocument)
    restored = yaml.safe_load(WorkflowCodec.dumps(document))
    original = yaml.safe_load(_rich_canonical_yaml())

    assert restored == original
    assert restored["global"]["advanced_options"]["producer_future_flag"] is True
    assert restored["steps"][1]["disabled"] is True
    assert restored["steps"][1]["inputs"] == ["confgen"]
    assert restored["steps"][0]["fan_out"] == ["opt", "freq"]
    assert restored["steps"][1]["fan_in"] == ["confgen"]
    assert restored["steps"][0]["future_step_field"] == {"keep": "me"}


def test_codec_atomic_write_is_available_without_facade_or_producer(tmp_path: Path) -> None:
    document = WorkflowCodec.loads(_rich_canonical_yaml())
    target = tmp_path / "nested" / "workflow.yaml"

    WorkflowCodec.write_atomic(target, WorkflowCodec.dumps(document))

    assert yaml.safe_load(target.read_text(encoding="utf-8")) == yaml.safe_load(_rich_canonical_yaml())
    assert not target.with_suffix(target.suffix + ".tmp").exists()


def test_editor_lint_is_bounded_and_reports_migration_without_semantic_rule_list() -> None:
    document = WorkflowCodec.loads("calc:\n  aliases: [legacy]\n  steps: [opt]\n")
    diagnostics = lint_workflow(document)

    assert document.migration.requires_migration is True
    assert document.migration.backup_required is True
    assert any(item.code == "migration.backup_required" for item in diagnostics)
    assert not any("itask" in item.code or "iprog" in item.code for item in diagnostics)


def test_editor_producer_diagnostics_are_warning_only() -> None:
    document = WorkflowCodec.loads(
        """\
global: {}
steps:
  - name: authoring
    type: calc
    params: {}
"""
    )
    validator = ConfFlowCompatibilityValidator(
        producer_validator=lambda payload: ["producer semantic mismatch"]
    )

    diagnostics = document.lint(validator)

    assert [(item.code, item.severity) for item in diagnostics] == [("producer.semantic", "warning")]


def test_legacy_migration_requires_explicit_backup_policy() -> None:
    document = WorkflowCodec.loads("calc:\n  steps: [opt]\n")

    with pytest.raises(ValueError, match="allow_format_change"):
        require_migration_policy(document, None)
    with pytest.raises(ValueError, match="backup_created"):
        require_migration_policy(document, MigrationPolicy(allow_format_change=True))

    require_migration_policy(
        document,
        MigrationPolicy(allow_format_change=True, backup_created=True),
    )
    canonical = document.canonical_mapping()
    assert canonical["steps"][0]["name"] == "opt"
    assert canonical["steps"][0]["params"]["itask"] == "opt"


def test_workflow_spec_facade_retains_rich_document_fields() -> None:
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")

    spec = WorkflowSpec.from_yaml(_rich_canonical_yaml())
    serialized = yaml.safe_load(spec.to_yaml())
    assert spec.workflow_document.raw["steps"][1]["disabled"] is True
    assert serialized["global"]["advanced_options"]["producer_future_flag"] is True
    assert serialized["steps"][1]["inputs"] == ["confgen"]
    assert serialized["steps"][1]["params"]["advanced_options"]["producer_future_flag"] == 7
    assert spec.migration_decision.requires_migration is False


def test_workflow_document_layers_do_not_import_qt_or_producer_models() -> None:
    root = Path(__file__).parents[1] / "src" / "jobdesk_app" / "core"
    for name in ("workflow_document.py", "workflow_codec.py", "workflow_editor.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert not any(name.startswith("PySide6") for name in imports)
        assert not any(name == "confflow.core.models" for name in imports)


def test_producer_model_and_legacy_validator_are_confined_to_facade() -> None:
    root = Path(__file__).parents[1] / "src" / "jobdesk_app" / "core"
    workflow_spec_path = root / "workflow_spec.py"
    workflow_spec_source = workflow_spec_path.read_text(encoding="utf-8")
    facade = ast.parse(workflow_spec_source)
    model_imports = [
        node
        for node in ast.walk(facade)
        if isinstance(node, ast.ImportFrom) and node.module == "confflow.core.models"
    ]
    assert len(model_imports) == 1
    loader = next(node for node in ast.walk(facade) if isinstance(node, ast.FunctionDef) and node.name == "_load_confflow_models")
    assert model_imports[0] in ast.walk(loader)
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "core._confflow_validation"
        for node in ast.walk(facade)
    )

    spec_class = next(node for node in ast.walk(facade) if isinstance(node, ast.ClassDef) and node.name == "WorkflowSpec")
    methods = {node.name: node for node in spec_class.body if isinstance(node, ast.FunctionDef)}
    for method_name in ("from_yaml", "to_yaml", "to_form"):
        method_source = ast.get_source_segment(workflow_spec_source, methods[method_name]) or ""
        assert "require_confflow" not in method_source
        assert "GlobalConfigModel" not in method_source
        assert "_validate_confflow_semantics" not in method_source

    for name in ("workflow_document.py", "workflow_codec.py", "workflow_editor.py", "workflow_validation.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        assert not any(
            isinstance(node, ast.ImportFrom) and node.module == "core._confflow_validation"
            for node in ast.walk(tree)
        )

    compatibility = ast.parse((root / "_confflow_validation.py").read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "COMPATIBILITY_ONLY" for target in node.targets)
        and isinstance(node.value, ast.Constant)
        and node.value.value is True
        for node in compatibility.body
    )
