"""Tests for :mod:`jobdesk_app.services.method_presets`."""
from __future__ import annotations

import pytest

from jobdesk_app.core.workflow_spec import WorkflowSpec
from jobdesk_app.services.method_presets import MethodPresetStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    # Re-route the user-preset directory to tmp_path.
    monkeypatch.setattr(
        "jobdesk_app.services.method_presets.get_app_data_dir",
        lambda: tmp_path,
    )
    return MethodPresetStore()


def test_list_presets_includes_builtins(store):
    names = {p.name for p in store.list_presets()}
    assert "b3lyp_631gd_opt_freq" in names  # one of the bundled gaussians
    assert "r2scan3c_opt_freq" in names     # one of the bundled orcas


def test_builtin_presets_carry_source_builtin(store):
    presets = store.list_presets()
    for p in presets:
        if p.name in {"b3lyp_631gd_opt_freq", "r2scan3c_opt_freq"}:
            assert p.source == "builtin"


def test_load_returns_workflow_spec(store):
    spec = store.load("b3lyp_631gd_opt_freq", source="builtin")
    assert spec is not None
    assert hasattr(spec, "global_config")


def test_load_unknown_raises(store):
    with pytest.raises(KeyError):
        store.load("does_not_exist", source="builtin")


def test_builtin_yaml_round_trip_through_form(store):
    """Regression: built-in YAML files must use the WorkflowSpec flat schema.

    Phase 2.0 shipped the presets as ``{global: {...}, steps: [...]}`` (confflow
    runtime schema), but ``WorkflowSpec.from_yaml`` parses the
    ``WorkflowSpec.from_form`` shape (``{work_dir, calc: {program, method,
    basis, ..., steps}}``). Without this assertion, ``to_form()`` silently
    returns empty steps / default program because Pydantic drops the
    unrecognised nested keys.
    """
    spec = store.load("b3lyp_631gd_opt_freq", source="builtin")
    form = spec.to_form()
    assert form["program"] == "gaussian"
    assert form["method"] == "B3LYP"
    assert form["basis"] == "6-31G(d)"
    assert form["steps"] == ["opt_freq"]
    assert form["work_dir_name"] == "b3lyp_631gd_opt_freq"

    spec_orca = store.load("r2scan3c_opt_freq", source="builtin")
    form_orca = spec_orca.to_form()
    assert form_orca["program"] == "orca"
    assert form_orca["steps"] == ["opt_freq"]

    spec_multi = store.load("conformer_ensemble_sp", source="builtin")
    form_multi = spec_multi.to_form()
    assert form_multi["steps"], "multi-step preset must round-trip its steps"
    assert "confgen" in form_multi["steps"]


def test_save_user_writes_yaml_to_user_dir(store, tmp_path):
    spec = WorkflowSpec.from_form(
        work_dir_name="user_demo",
        program="orca",
        method="B3LYP",
        basis="def2-SVP",
        charge=0,
        multiplicity=1,
        nproc=8,
        memory_mb=8192,
        steps=("confgen", "opt"),
    )
    path = store.save_user("user_demo", spec)
    assert path.exists()
    assert path.parent == store.user_dir
    assert path.suffix == ".yaml"
    reloaded = WorkflowSpec.from_yaml(path.read_text(encoding="utf-8"))
    assert reloaded.to_form()["work_dir_name"] == "user_demo"


def test_save_user_then_list_includes_it_as_user(store):
    spec = WorkflowSpec.from_form(
        work_dir_name="x", program="gaussian", method="B3LYP",
        basis="6-31G(d)", charge=0, multiplicity=1,
        nproc=4, memory_mb=4096,
    )
    store.save_user("user_x", spec)
    user_names = {p.name for p in store.list_presets() if p.source == "user"}
    assert "user_x" in user_names


def test_user_preset_with_same_name_overrides_builtin(store):
    spec = WorkflowSpec.from_form(
        work_dir_name="override", program="orca", method="r2SCAN-3c",
        basis="", charge=0, multiplicity=1, nproc=8, memory_mb=4096,
    )
    store.save_user("b3lyp_631gd_opt_freq", spec)  # collide with builtin
    match = next(p for p in store.list_presets() if p.name == "b3lyp_631gd_opt_freq")
    assert match.source == "user"


def test_delete_user_removes_file(store):
    spec = WorkflowSpec.from_form(
        work_dir_name="x", program="gaussian", method="B3LYP",
        basis="6-31G(d)", charge=0, multiplicity=1,
        nproc=4, memory_mb=4096,
    )
    store.save_user("temp", spec)
    store.delete_user("temp")
    assert not (store.user_dir / "temp.yaml").exists()
    user_names = [p for p in store.list_presets() if p.source == "user" and p.name == "temp"]
    assert user_names == []


def test_rename_user_creates_new_removes_old(store):
    spec = WorkflowSpec.from_form(
        work_dir_name="x", program="gaussian", method="B3LYP",
        basis="6-31G(d)", charge=0, multiplicity=1, nproc=4, memory_mb=4096,
    )
    store.save_user("old_name", spec)
    new_path = store.rename_user("old_name", "new_name")
    assert new_path.exists()
    assert not (store.user_dir / "old_name.yaml").exists()
