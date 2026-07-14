"""Regression tests for the step-library / saved-workflow submit boundary."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QApplication

from jobdesk_app.core.submit_payload import InputSource
from jobdesk_app.gui.dialogs.submit_dialog import SubmitDialog
from jobdesk_app.gui.pages.workflow_page import WorkflowPage
from jobdesk_app.services.method_presets import MethodPresetStore
from jobdesk_app.services.submit_use_case import SubmitUseCase


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


class _StubState:
    current_project_root = None
    repo = None


def _src(path: Path) -> InputSource:
    return InputSource(path=path, side="local", kind="xyz")


def _save_rich_workflow(store: MethodPresetStore, name: str = "my_workflow") -> str:
    text = """\
global:
  cores_per_task: 12
  total_memory: 24GB
  charge: -1
  multiplicity: 2
steps:
  - name: make_conformers
    type: confgen
    params:
      chains: [1-2-3-4]
      angle_step: 90
    inputs: []
  - name: final_orca
    type: calc
    params:
      iprog: orca
      itask: opt_freq
      keyword: PBE0 D4 def2-TZVP
    inputs: [make_conformers]
"""
    store.save_user_yaml(name, text)
    return text


def test_workflow_page_lists_only_saved_workflows(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("jobdesk_app.services.method_presets.get_app_data_dir", lambda: tmp_path)
    store = MethodPresetStore()
    _save_rich_workflow(store)
    page = WorkflowPage(state=_StubState(), language="en", preset_store=store)
    try:
        items = [page.preset_combo.itemData(i) for i in range(page.preset_combo.count())]
        assert items == [("my_workflow", "user")]
    finally:
        page.close()
        page.deleteLater()


def test_submit_dialog_lists_only_saved_workflows_and_shows_exact_yaml(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("jobdesk_app.services.method_presets.get_app_data_dir", lambda: tmp_path)
    store = MethodPresetStore()
    expected = _save_rich_workflow(store)
    dlg = SubmitDialog("en", files=[_src(tmp_path / "a.xyz")], server_id="prod", preset_store=store, preset_name="my_workflow")
    try:
        assert dlg.preset_combo.currentData() == "my_workflow"
        assert dlg._yaml_view.toPlainText() == expected
        assert dlg.charge_spin.isEnabled() is False
        assert dlg.mult_spin.isEnabled() is False
    finally:
        dlg.close()
        dlg.deleteLater()


def test_builtin_steps_cannot_be_submitted_as_workflows(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("jobdesk_app.services.method_presets.get_app_data_dir", lambda: tmp_path)
    dlg = SubmitDialog("en", files=[_src(tmp_path / "a.xyz")], server_id="prod", preset_store=MethodPresetStore())
    try:
        assert dlg.preset_combo.count() == 0
        assert dlg._yaml_view.toPlainText() == ""
        with pytest.raises(ValueError, match="saved workflow"):
            dlg.build_payload()
    finally:
        dlg.close()
        dlg.deleteLater()


def test_preview_and_uploaded_yaml_are_exactly_the_saved_workflow(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("jobdesk_app.services.method_presets.get_app_data_dir", lambda: tmp_path)
    store = MethodPresetStore()
    expected = _save_rich_workflow(store)
    xyz = tmp_path / "molecule.xyz"
    xyz.write_text("1\nworkflow regression\nH 0 0 0\n", encoding="utf-8")
    dlg = SubmitDialog("en", files=[_src(xyz)], server_id="prod", preset_store=store, preset_name="my_workflow")
    try:
        payload = dlg.build_payload()
        assert payload.workflow is not None
        assert payload.workflow.yaml_text == expected
        batch = SubmitUseCase().execute(payload)
        assert batch.ok, batch.errors
        assert batch.yaml_local_path is not None
        assert batch.yaml_local_path.read_text(encoding="utf-8") == expected
    finally:
        dlg.close()
        dlg.deleteLater()
