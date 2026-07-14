"""Tests for the new ``SubmitDialog`` (Phase 2.0).

Verifies auto-detected Mode (Single / Workflow) and payload assembly
for both branches.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from jobdesk_app.core.submit_payload import InputSource, SubmitPayload  # noqa: E402
from jobdesk_app.core.workflow_spec import WorkflowSpec  # noqa: E402
from jobdesk_app.gui.dialogs.submit_dialog import SubmitDialog  # noqa: E402
from jobdesk_app.services.method_presets import MethodPresetStore  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def qapp_instance(qapp):
    return qapp


def _src(name: str, kind: str) -> InputSource:
    p = Path("/tmp") / name
    return InputSource(path=p, side="local", kind=kind)  # type: ignore[arg-type]


def test_mode_defaults_to_single_for_gjf_only(qapp_instance):
    sources = [_src("a.gjf", "gjf"), _src("b.gjf", "gjf")]
    dlg = SubmitDialog("en", files=sources)
    assert dlg.mode() == "single"


def test_mode_defaults_to_single_for_inp_only(qapp_instance):
    sources = [_src("a.inp", "inp")]
    dlg = SubmitDialog("en", files=sources)
    assert dlg.mode() == "single"


def test_mode_defaults_to_single_for_mixed_gjf_inp(qapp_instance):
    sources = [_src("a.gjf", "gjf"), _src("b.inp", "inp")]
    dlg = SubmitDialog("en", files=sources)
    assert dlg.mode() == "single"


def test_mode_forces_workflow_when_xyz_present(qapp_instance):
    sources = [_src("a.gjf", "gjf"), _src("b.xyz", "xyz")]
    dlg = SubmitDialog("en", files=sources)
    assert dlg.mode() == "workflow"


def test_single_radio_disabled_when_xyz_present(qapp_instance):
    sources = [_src("a.xyz", "xyz")]
    dlg = SubmitDialog("en", files=sources)
    assert dlg.single_radio.isEnabled() is False


def test_charge_and_server_flow_into_single_payload(qapp_instance):
    sources = [_src("a.gjf", "gjf")]
    dlg = SubmitDialog("en", files=sources, server_id="prod-01")
    dlg.charge_spin.setValue(2)
    payload = dlg.build_payload()
    assert isinstance(payload, SubmitPayload)
    assert payload.kind == "single"
    assert payload.calc.charge == 2
    assert payload.server_id == "prod-01"
    assert payload.program == "gaussian"


def test_inp_only_payload_program_is_orca(qapp_instance):
    sources = [_src("a.inp", "inp")]
    dlg = SubmitDialog("en", files=sources, server_id="prod-01")
    payload = dlg.build_payload()
    assert payload.program == "orca"


def test_workflow_payload_uses_selected_preset(qapp_instance, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jobdesk_app.services.method_presets.get_app_data_dir",
        lambda: tmp_path,
    )
    store = MethodPresetStore()
    spec = WorkflowSpec.from_form(
        work_dir_name="x", program="gaussian", method="B3LYP",
        basis="6-31G(d)", charge=0, multiplicity=1, nproc=4, memory_mb=4096,
    )
    store.save_user("my_preset", spec)

    sources = [_src("a.xyz", "xyz")]
    dlg = SubmitDialog("en", files=sources, server_id="prod-01",
                       preset_store=store)
    dlg.set_selected_preset_name("my_preset")
    payload = dlg.build_payload()
    assert payload.kind in {"confflow", "dag"}
    assert payload.program == "gaussian"
    assert payload.calc.method_basis == "B3LYP 6-31G(d)"


def test_stale_workflow_selection_cannot_accept(qapp_instance, tmp_path, monkeypatch):
    monkeypatch.setattr("jobdesk_app.services.method_presets.get_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    dlg = SubmitDialog(
        "en", files=[_src("a.xyz", "xyz")], preset_store=MethodPresetStore(),
        preset_name="b3lyp_631gd_opt_freq",
    )
    try:
        assert dlg._preset_name is None
        dlg._on_ok_clicked()
        assert dlg.result() == 0
    finally:
        dlg.close()
        dlg.deleteLater()


# Phase 2.0 dual-entry follow-ups: the dialog must tolerate an empty
# source list so the Workflow-page "Use this preset for submit" button
# and the Runs-page empty-state "Go to Submit" button can both open
# the dialog without first requiring file selection. Without this
# branch, ``files=[]`` would silently render a blank dialog and crash
# ``build_payload()`` on any IndexedError ``files[0]`` access.


def test_empty_files_renders_empty_state_banner(qapp_instance):
    """When no files are passed, the empty-state banner is visible.

    Pre-fix regression guard. The banner is the first child widget
    in the dialog's vertical layout and carries an italic amber
    hint. The OK button must be disabled so the user cannot submit
    a payload with zero sources.
    """
    dlg = SubmitDialog("en", files=[])
    try:
        assert dlg._has_files is False
        # The empty-state widget stays hidden when there ARE files
        # and visible when there AREN'T. ``isVisible()`` is gated on
        # the parent being shown (the dialog itself isn't on screen
        # in offscreen tests), so we use ``isHidden()`` instead --
        # ``setVisible(False)`` flips it without needing show().
        assert dlg._empty_state.isHidden() is False
        # Mode radios locked into Workflow-only.
        assert dlg.workflow_radio.isChecked() is True
        assert dlg.workflow_radio.isEnabled() is False
        assert dlg.single_radio.isEnabled() is False
        # OK button disabled so the user can't accept an empty payload.
        assert dlg._ok_button.isEnabled() is False
    finally:
        dlg.close()
        dlg.deleteLater()


def test_empty_files_build_payload_raises(qapp_instance):
    """``build_payload()`` with no files raises ``ValueError``.

    Belt-and-braces: the OK button being disabled keeps users from
    reaching here in production, but if a future code path forgets
    to gate the call, we want a loud failure instead of an
    IndexedError or a half-built payload.
    """
    dlg = SubmitDialog("en", files=[])
    try:
        with pytest.raises(ValueError, match="no input files"):
            dlg.build_payload()
    finally:
        dlg.close()
        dlg.deleteLater()


def test_set_files_re_enables_ok_and_hides_banner(qapp_instance):
    """``set_files()`` swaps empty-state off and the OK button on.

    Covers the runtime path (e.g. drag-drop extension) that feeds
    sources into an already-open dialog.
    """
    dlg = SubmitDialog("en", files=[])
    try:
        assert dlg._ok_button.isEnabled() is False
        dlg.set_files([_src("a.gjf", "gjf")])
        assert dlg._has_files is True
        assert dlg._empty_state.isHidden() is True
        assert dlg._ok_button.isEnabled() is True
        # After files are added, Single mode is enabled again.
        assert dlg.single_radio.isEnabled() is True
        assert dlg.workflow_radio.isEnabled() is True
    finally:
        dlg.close()
        dlg.deleteLater()


def test_set_files_with_empty_list_re_locks_into_workflow(qapp_instance):
    """Re-clearing files relocks the dialog into the empty-state.

    Belt-and-braces around ``set_files()`` — if a test or runtime
    path swaps from a non-empty list back to ``[]``, the empty-state
    banner returns and the OK button is disabled again.
    """
    dlg = SubmitDialog("en", files=[_src("a.gjf", "gjf")])
    try:
        dlg.set_files([])
        assert dlg._empty_state.isHidden() is False
        assert dlg._ok_button.isEnabled() is False
        assert dlg.mode() == "workflow"
    finally:
        dlg.close()
        dlg.deleteLater()
