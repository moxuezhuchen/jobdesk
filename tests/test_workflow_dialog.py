"""Tests for ``WorkflowBuilderDialog`` (Phase 2.0)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QApplication  # noqa: E402

from jobdesk_app.core.workflow_spec import WorkflowSpec  # noqa: E402
from jobdesk_app.gui.dialogs.workflow_builder_dialog import WorkflowBuilderDialog  # noqa: E402
from jobdesk_app.services.method_presets import MethodPresetStore  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_dialog_embeds_editor(qapp):
    dlg = WorkflowBuilderDialog(language="en", preset_store=MethodPresetStore())
    assert dlg.editor is not None
    assert dlg.editor.is_empty()
    dlg.close()
    dlg.deleteLater()


def test_dialog_loads_initial_spec(qapp):
    spec = WorkflowSpec.from_form(
        work_dir_name="x", program="gaussian", method="B3LYP",
        basis="6-31G(d)", charge=0, multiplicity=1, nproc=4, memory_mb=4096,
    )
    dlg = WorkflowBuilderDialog(language="en",
                                preset_store=MethodPresetStore(),
                                initial_spec=spec)
    assert not dlg.editor.is_empty()
    dlg.close()
    dlg.deleteLater()


def test_dialog_accept_returns_spec(qapp):
    spec = WorkflowSpec.from_form(
        work_dir_name="x", program="gaussian", method="B3LYP",
        basis="6-31G(d)", charge=0, multiplicity=1, nproc=4, memory_mb=4096,
    )
    dlg = WorkflowBuilderDialog(language="en",
                                preset_store=MethodPresetStore(),
                                initial_spec=spec)
    dlg._on_accept()
    assert dlg.result_spec() is spec
    dlg.close()
    dlg.deleteLater()
