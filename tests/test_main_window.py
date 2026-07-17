"""Smoke tests for MainWindow wiring (Phase 1.1)."""

from __future__ import annotations

import os
from pathlib import Path

# Ensure an offscreen Qt platform before any Qt import (Windows CI friendly).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QApplication  # noqa: E402

from jobdesk_app.gui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _patch_dialog(monkeypatch, captured):
    """Replace WorkflowTourDialog with a fake that records the call args.

    The MainWindow imports ``WorkflowTourDialog`` lazily from
    ``jobdesk_app.gui.dialogs.workflow_tour_dialog`` inside
    ``_show_workflow_tour``, so we patch the symbol on that submodule.
    """
    from jobdesk_app.gui.dialogs import workflow_tour_dialog

    class FakeDialog:
        def __init__(self, parent=None, language="en"):
            captured["parent"] = parent
            captured["language"] = language
            captured["constructed"] = True

        def exec(self):
            captured["exec_called"] = True
            return None

    monkeypatch.setattr(workflow_tour_dialog, "WorkflowTourDialog", FakeDialog)


def test_show_workflow_tour_opens_dialog(qapp, monkeypatch):
    """The MainWindow must have a ``_show_workflow_tour`` method that
    instantiates ``WorkflowTourDialog`` with the window as parent and
    the current language, then calls ``exec()``.
    """
    captured: dict = {}
    _patch_dialog(monkeypatch, captured)

    window = MainWindow()
    try:
        # The method must exist (Phase 1.1 contract).
        assert hasattr(window, "_show_workflow_tour")
        assert callable(window._show_workflow_tour)

        window._show_workflow_tour()

        assert captured.get("constructed") is True
        assert captured["parent"] is window
        assert captured["language"] == window.language
        assert captured.get("exec_called") is True
    finally:
        try:
            window.shutdown()
        except Exception:
            pass
        window.close()
        window.deleteLater()


def test_submit_editor_tour_signal_connected_to_main_window(qapp, monkeypatch):
    """Phase 2.0: The SubmitPage no longer embeds the editor; the editor
    lives inside :class:`WorkflowBuilderDialog`. The tour dialog is opened
    directly from ``_show_workflow_tour`` and reachable from the editor.
    The MainWindow therefore does not need to wire any signal — instead,
    we verify that ``_show_workflow_tour`` constructs the dialog.
    """
    captured: dict = {}
    _patch_dialog(monkeypatch, captured)

    window = MainWindow()
    try:
        assert hasattr(window, "_show_workflow_tour")
        # Call directly; this is the same path the editor's
        # ``tour_requested`` signal drives.
        window._show_workflow_tour()
        assert captured.get("constructed") is True
        assert captured["parent"] is window
        assert captured.get("exec_called") is True
    finally:
        try:
            window.shutdown()
        except Exception:
            pass
        window.close()
        window.deleteLater()


# Phase 2.0 dual-entry follow-ups: the Workflow-page and Runs-page
# empty-state buttons used to be dead links. The fixes route them
# through the modal ``SubmitDialog`` so the buttons land somewhere
# the user can act on. These tests pin down the wiring.


class _RecordingDialog:
    """Replacement for ``SubmitDialog`` that records the constructor args.

    We avoid ``exec()`` returning ``Accepted`` so the test cannot
    accidentally trigger the SubmitUseCase worker. Instead we return
    ``Rejected`` so ``MainWindow._on_submit_requested`` is never
    called. Mirrors the ``DialogCode`` enum attribute that real
    ``QDialog`` subclasses expose so ``MainWindow._open_submit_dialog``
    can do ``SubmitDialog.DialogCode.Accepted`` without falling over.
    """

    last_instance: "_RecordingDialog | None" = None

    # Mirror QDialog.DialogCode so the ``== SubmitDialog.DialogCode.Accepted``
    # check in MainWindow still resolves. Inheriting from QDialog would
    # actually instantiate a real widget; we keep this as a plain Python
    # class so the test stays single-purpose.
    class DialogCode:
        Accepted = 1
        Rejected = 0

    def __init__(
        self,
        language,
        *,
        files,
        server_id="",
        remote_dir="/",
        max_parallel=1,
        preset_store=None,
        preset_name=None,
        parent=None,
    ):
        self.language = language
        self.files = list(files)
        self.server_id = server_id
        self.remote_dir = remote_dir
        self.preset_name = preset_name
        self.parent = parent
        self.exec_called = False
        _RecordingDialog.last_instance = self

    def exec(self):
        self.exec_called = True
        # Rejected == 0 so MainWindow does not call build_payload().
        return self.DialogCode.Rejected

    def set_selected_preset_name(self, name):
        self.preset_name = name

    def build_payload(self):
        raise AssertionError("RecordingDialog.build_payload should never be called")


def _patch_submit_dialog(monkeypatch):
    """Patch ``SubmitDialog`` at the import site MainWindow uses.

    MainWindow imports ``SubmitDialog`` at module import time, so
    we patch the symbol at ``jobdesk_app.gui.main_window``.
    """
    monkeypatch.setattr("jobdesk_app.gui.main_window.SubmitDialog", _RecordingDialog)


def test_workflow_chosen_opens_submit_dialog_with_preset(qapp, monkeypatch):
    """Workflow-page button opens the SubmitDialog with preset pre-selected.

    Pre-fix regression guard: ``_on_workflow_chosen`` previously only
    navigated to Files. Now it also opens the modal with the preset
    pre-selected and an empty sources list (the expected Phase 2.0
    flow is "pick preset first, then files").
    """
    _patch_submit_dialog(monkeypatch)
    window = MainWindow()
    try:
        assert hasattr(window, "_on_workflow_chosen")
        _RecordingDialog.last_instance = None

        window._on_workflow_chosen("b3lyp_631gd_opt_freq", "builtin")

        # The dialog must have been constructed exactly once with the
        # preset name carried through.
        dlg = _RecordingDialog.last_instance
        assert dlg is not None
        assert dlg.exec_called is True
        assert dlg.files == []
        assert dlg.preset_name == "b3lyp_631gd_opt_freq"
        # The dialog should be parented to the window so modality works.
        assert dlg.parent is window
    finally:
        try:
            window.shutdown()
        except Exception:
            pass
        window.close()
        window.deleteLater()


def test_workflow_chosen_without_preset_name_still_opens_dialog(qapp, monkeypatch):
    """A blank name falls back to ``preset_name=None`` in the dialog."""
    _patch_submit_dialog(monkeypatch)
    window = MainWindow()
    try:
        _RecordingDialog.last_instance = None
        window._on_workflow_chosen("", "builtin")
        dlg = _RecordingDialog.last_instance
        assert dlg is not None
        assert dlg.preset_name is None
        assert dlg.exec_called is True
    finally:
        try:
            window.shutdown()
        except Exception:
            pass
        window.close()
        window.deleteLater()


def test_runs_go_to_submit_opens_empty_dialog(qapp, monkeypatch):
    """Runs-page ``go_to_submit_requested`` now opens the dialog.

    Pre-fix regression guard: previously this signal only called
    ``_switch_page(1)`` (which is the Workflow page, not a Submit
    trigger). Now it opens the modal with an empty sources list.
    """
    _patch_submit_dialog(monkeypatch)
    window = MainWindow()
    try:
        assert hasattr(window, "_on_runs_go_to_submit")
        _RecordingDialog.last_instance = None

        # Drive via the actual signal connection path so we also
        # verify the wiring is intact (the lambda that lived here
        # pre-fix routed navigation to index 1; the new code
        # instantiates ``_on_runs_go_to_submit``).
        window.runs_page.go_to_submit_requested.emit()

        dlg = _RecordingDialog.last_instance
        assert dlg is not None
        assert dlg.exec_called is True
        assert dlg.files == []
        assert dlg.preset_name is None
    finally:
        try:
            window.shutdown()
        except Exception:
            pass
        window.close()
        window.deleteLater()


def test_use_as_input_with_files_opens_dialog_with_sources(qapp, monkeypatch):
    """``_on_use_as_input_received`` still routes file sources through.

    Sanity guard: the new empty-dialog path must not have broken
    the pre-existing file-source path used by the Files-page right-
    click menu. We feed in two ``InputSource`` instances and verify
    the recording dialog receives both.
    """
    from jobdesk_app.core.submit_payload import InputSource

    _patch_submit_dialog(monkeypatch)
    window = MainWindow()
    try:
        _RecordingDialog.last_instance = None
        sources = [
            InputSource(path=Path("/tmp/a.gjf"), side="local", kind="gjf"),
            InputSource(path=Path("/tmp/b.gjf"), side="local", kind="gjf"),
        ]
        window._on_use_as_input_received(sources)
        dlg = _RecordingDialog.last_instance
        assert dlg is not None
        assert len(dlg.files) == 2
    finally:
        try:
            window.shutdown()
        except Exception:
            pass
        window.close()
        window.deleteLater()
