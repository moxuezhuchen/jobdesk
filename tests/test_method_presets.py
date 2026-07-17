"""Tests for saved workflows and reusable step presets."""

from __future__ import annotations

import pytest

from jobdesk_app.core.workflow_spec import WorkflowSpec
from jobdesk_app.services.method_presets import MethodPresetStore, StepPresetStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("jobdesk_app.services.method_presets.get_app_data_dir", lambda: tmp_path)
    return MethodPresetStore()


def _workflow_yaml() -> str:
    return """\
global:
  cores_per_task: 4
  total_memory: 4GB
steps:
  - name: gaussian_opt_freq
    type: calc
    params:
      iprog: gaussian
      itask: opt_freq
      keyword: B3LYP 6-31G(d)
    inputs: []
"""


def test_workflow_list_starts_empty_and_excludes_builtin_names(store):
    assert store.list_presets() == []
    with pytest.raises(KeyError):
        store.load("b3lyp_631gd_opt_freq", source="builtin")


def test_save_user_yaml_preserves_composed_workflow_exactly(store):
    yaml_text = _workflow_yaml()
    path = store.save_user_yaml("exact_workflow", yaml_text)

    assert path.read_text(encoding="utf-8") == yaml_text
    presets = store.list_presets()
    assert [(preset.name, preset.source) for preset in presets] == [("exact_workflow", "user")]
    assert store.load_yaml("exact_workflow", source="user") == yaml_text


def test_save_user_then_list_includes_user_workflow(store):
    spec = WorkflowSpec.from_form(
        work_dir_name="x",
        program="gaussian",
        method="B3LYP",
        basis="6-31G(d)",
        charge=0,
        multiplicity=1,
        nproc=4,
        memory_mb=4096,
    )
    store.save_user("user_x", spec)
    assert [preset.name for preset in store.list_presets()] == ["user_x"]


def test_delete_and_rename_user_workflow(store):
    store.save_user_yaml("old_name", _workflow_yaml())
    new_path = store.rename_user("old_name", "new_name")
    assert new_path.exists()
    assert not (store.user_dir / "old_name.yaml").exists()
    store.delete_user("new_name")
    assert store.list_presets() == []


def test_step_presets_are_the_only_bundled_presets(tmp_path, monkeypatch):
    monkeypatch.setattr("jobdesk_app.services.method_presets.get_app_data_dir", lambda: tmp_path)
    step_store = StepPresetStore()
    names = {preset.name for preset in step_store.list_presets()}
    assert {"confgen", "b3lyp_631gd_opt_freq", "b3lyp_def2tzvp_opt_freq"} <= names
    step = step_store.load("b3lyp_def2tzvp_opt_freq", source="builtin")
    assert step["type"] == "calc"
    assert step["params"] == {"iprog": "orca", "itask": "opt_freq", "keyword": "B3LYP D3BJ def2-TZVP"}
    assert "inputs" not in step


def test_step_preset_save_rejects_workflow_owned_keys(tmp_path, monkeypatch):
    monkeypatch.setattr("jobdesk_app.services.method_presets.get_app_data_dir", lambda: tmp_path)
    with pytest.raises(ValueError, match="inputs"):
        StepPresetStore().save_user("invalid", {"type": "calc", "params": {}, "inputs": []})


def test_step_preset_save_and_reload(tmp_path, monkeypatch):
    monkeypatch.setattr("jobdesk_app.services.method_presets.get_app_data_dir", lambda: tmp_path)
    step_store = StepPresetStore()
    step_store.save_user("custom_sp", {"type": "calc", "params": {"iprog": "orca", "itask": "sp"}})
    assert step_store.load("custom_sp", source="user")["params"]["itask"] == "sp"
