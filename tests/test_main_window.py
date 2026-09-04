"""Smoke tests for MainWindow wiring (Phase 1.1)."""

from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

# Ensure an offscreen Qt platform before any Qt import (Windows CI friendly).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QApplication  # noqa: E402

from jobdesk_app.application.gui_ports import (  # noqa: E402
    ConnectionSnapshot,
    FilesPagePort,
    FileTargetSnapshot,
    PageRefreshPort,
)
from jobdesk_app.gui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_show_error_reuses_owned_nonblocking_message_box(qapp):
    """Closing an error must not leave a temporary native dialog wrapper."""
    window = MainWindow()
    try:
        window.show_error("First error", "first message")
        box = window._error_message_box
        assert box is not None
        assert box.windowTitle() == "First error"
        assert box.text() == "first message"
        assert box.isVisible()

        box.close()
        qapp.processEvents()

        window.show_error("Second error", "second message")
        assert window._error_message_box is box
        assert box.windowTitle() == "Second error"
        assert box.text() == "second message"
    finally:
        box = getattr(window, "_error_message_box", None)
        if box is not None:
            box.close()
        try:
            window.shutdown()
        except Exception:
            pass
        window.close()
        window.deleteLater()


def test_files_page_port_copies_an_immutable_connection_and_target_snapshot():
    class RawSnapshot:
        server_id = "wsl"
        server = object()
        remote_dir = "/opt/confflow"
        connected = True
        ready = True

    class Page:
        def __init__(self):
            self.raw = RawSnapshot()
            self.refresh_calls = 0

        def connection_snapshot(self):
            return self.raw

        def refresh(self):
            self.refresh_calls += 1

    page = Page()
    snapshot = FilesPagePort(page).snapshot()

    assert isinstance(snapshot, ConnectionSnapshot)
    assert snapshot.target == FileTargetSnapshot("wsl", "/opt/confflow")
    assert snapshot.connected is True
    assert snapshot.ready is True
    assert not hasattr(snapshot, "service")
    page.raw.remote_dir = "/tmp/changed"
    assert snapshot.remote_dir == "/opt/confflow"
    try:
        snapshot.target.remote_dir = "/tmp/mutated"  # type: ignore[misc]
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("FileTargetSnapshot must be immutable")


def test_files_page_port_derives_legacy_status_without_retaining_service():
    service = object()

    class LegacySnapshot:
        server_id = "wsl"
        server = None
        remote_dir = "/"

    LegacySnapshot.service = service

    class LegacyPage:
        def connection_snapshot(self):
            return LegacySnapshot()

        def refresh(self):
            pass

        def upload_path(self, local_path, remote_path, *args, **kwargs):
            return (local_path, remote_path, args, kwargs)

    snapshot = FilesPagePort(LegacyPage()).snapshot()
    assert snapshot.connected is True
    assert snapshot.ready is True
    assert not hasattr(snapshot, "service")


def test_files_page_port_uploads_through_public_page_action():
    expected = object()

    class Page:
        def connection_snapshot(self):
            raise AssertionError("snapshot is not part of this action test")

        def refresh(self):
            pass

        def upload_path(self, local_path, remote_path, *args, **kwargs):
            assert local_path == "input.xyz"
            assert remote_path == "/remote/input.xyz"
            assert args == ("policy",)
            assert kwargs == {"dry_run": True}
            return expected

    assert FilesPagePort(Page()).upload_path("input.xyz", "/remote/input.xyz", "policy", dry_run=True) is expected


def test_page_refresh_port_never_falls_back_to_private_widget_actions():
    class PublicPage:
        def __init__(self):
            self.calls = 0

        def refresh_run_list(self):
            self.calls += 1

        def _refresh_all(self):
            raise AssertionError("private fallback must not be called")

    public_page = PublicPage()
    assert PageRefreshPort.for_page(public_page).refresh() is True
    assert public_page.calls == 1

    class PrivateOnlyPage:
        def _refresh_all(self):
            raise AssertionError("private fallback must not be called")

    assert PageRefreshPort.for_page(PrivateOnlyPage()).refresh() is False


@pytest.fixture(autouse=True)
def _isolated_gui_appdata(monkeypatch, tmp_path):
    """Keep MainWindow smoke tests away from the developer's real profile."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    # AppConfig supports this override as well; clearing it prevents a
    # machine-level value from bypassing the APPDATA isolation above.
    monkeypatch.delenv("JOBDESK_APPDATA", raising=False)


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
        workspace=None,
        workflows=None,
        preset_name=None,
        parent=None,
    ):
        self.language = language
        self.files = list(files)
        self.server_id = server_id
        self.remote_dir = remote_dir
        self.workspace = workspace
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


def test_submit_dialog_receives_current_project_workspace(qapp, monkeypatch, tmp_path):
    """Remote-only workflow YAML has an explicit writable project anchor."""
    _patch_submit_dialog(monkeypatch)
    window = MainWindow()
    try:
        window.state.current_project_root = tmp_path
        _RecordingDialog.last_instance = None

        window._open_submit_dialog([])

        dlg = _RecordingDialog.last_instance
        assert dlg is not None
        assert dlg.workspace == tmp_path
    finally:
        try:
            window.shutdown()
        except Exception:
            pass
        window.close()
        window.deleteLater()


def test_main_window_does_not_query_runs_db_during_construction(qapp, monkeypatch):
    """A broken/unavailable runs database must not block window creation."""
    from jobdesk_app.gui.pages.runs_results_page import RunsResultsPage

    with monkeypatch.context() as isolated:
        isolated.setattr(
            RunsResultsPage,
            "refresh_run_list",
            lambda _page: (_ for _ in ()).throw(AssertionError("runs DB queried during startup")),
        )
        window = MainWindow()
        try:
            assert window.runs_page is not None
        finally:
            try:
                window.shutdown()
            except Exception:
                pass
            window.close()
            window.deleteLater()


def test_main_window_submit_is_owned_by_injected_task_supervisor(qapp, tmp_path):
    from jobdesk_app.application.facades import GuiPreferencesSnapshot

    supervisor = MagicMock()
    lease = object()
    supervisor.acquire_busy.return_value = lease
    runs = MagicMock()
    application = MagicMock()
    application.runs = runs
    application.settings.preferences.return_value = GuiPreferencesSnapshot()
    window = MainWindow(task_supervisor=supervisor, application=application)
    try:
        window.state.current_project_root = tmp_path
        window._files_port = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(server_id="wsl", ready=True),
        )
        payload = SimpleNamespace(server_id="wsl", remote_dir="/tmp/jobdesk-submit")

        window._on_submit_requested(payload)

        supervisor.acquire_busy.assert_called_once_with("main-window-submit", "submit")
        supervisor.start.assert_called_once()
        owner_key, operation_key, target, callbacks = supervisor.start.call_args.args
        assert owner_key == "main-window"
        assert operation_key == "submit"
        assert callable(target)
        assert callbacks.on_result is not None
        assert callbacks.on_error is not None
        assert supervisor.start.call_args.kwargs == {"busy_lease": lease}
        target(None)
        runs.submit.assert_called_once_with(payload, dispatch=True)
    finally:
        window.shutdown()
        window.close()
        window.deleteLater()


def test_main_window_rejects_duplicate_submit_before_building_worker(qapp):
    statuses: list[str] = []
    supervisor = MagicMock()
    supervisor.acquire_busy.return_value = None
    window = MainWindow(task_supervisor=supervisor)
    try:
        window._update_status = statuses.append
        window._files_port = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(server_id="wsl", ready=True),
        )
        payload = SimpleNamespace(server_id="wsl", remote_dir="/tmp/jobdesk-submit")

        window._on_submit_requested(payload)

        supervisor.start.assert_not_called()
        assert statuses == ["Remote operation already in progress"]
    finally:
        window.shutdown()
        window.close()
        window.deleteLater()


def test_main_window_shutdown_closes_supervisor_once_before_shared_pool(qapp):
    calls: list[str] = []
    supervisor = MagicMock()
    supervisor.shutdown.side_effect = lambda: calls.append("supervisor")
    pool = MagicMock()
    pool.close.side_effect = lambda: calls.append("pool")
    window = MainWindow(session_pool=pool, task_supervisor=supervisor)

    window.shutdown()
    window.shutdown()

    supervisor.shutdown.assert_called_once_with()
    pool.close.assert_called_once_with()
    assert calls.index("supervisor") < calls.index("pool")
    window.close()
    window.deleteLater()


def test_main_window_installs_navigation_and_page_shortcuts(qapp):
    window = MainWindow()
    try:
        sequences = {shortcut.key().toString() for shortcut in window._shortcuts}
        assert {"Alt+1", "Alt+2", "Alt+3", "Alt+4", "F5", "Ctrl+F", "Ctrl+S"} <= sequences
    finally:
        window.shutdown()
        window.close()
        window.deleteLater()


def test_runs_page_language_refresh_remains_explicit(qapp, monkeypatch):
    """Language updates still refresh the list when explicitly requested."""
    from jobdesk_app.gui.pages.runs_results_page import RunsResultsPage

    refresh_calls: list[int] = []
    with monkeypatch.context() as isolated:
        isolated.setattr(RunsResultsPage, "refresh_run_list", lambda _page: refresh_calls.append(1))
        window = MainWindow()
        try:
            # Constructor path translates labels only.
            assert refresh_calls == []
            window.runs_page.apply_language("zh")
            assert refresh_calls == [1]
        finally:
            try:
                window.shutdown()
            except Exception:
                pass
            window.close()
            window.deleteLater()
