"""JobDesk GUI — 4-page layout: Files / Submit / Runs+Results / Settings+Servers."""

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow, QMessageBox

from ..app_logging import configure_file_logging
from ..config.servers import load_servers
from ..core.submit_payload import SubmitPayload
from ..services.gui_settings import GuiSettingsStore
from ..services.run_coordinator import RunCoordinator
from ..services.run_service import RunService
from .i18n import tr
from .layouts.shell import AppShell
from .pages.file_transfer_page import FileTransferPage
from .pages.runs_results_page import RunsResultsPage
from .pages.settings_servers_page import SettingsServersPage
from .pages.submit_page import SubmitPage
from .session import create_sftp_client, create_ssh_client
from .state import AppState
from .theme import build_app_stylesheet
from .workers import BackgroundWorker

# Sidebar nav items: (icon_name, label).  Labels are translated at runtime
# via :func:`i18n.tr` so adding a new entry here only needs the i18n key.
_NAV_ITEMS = [
    ("folder", "Files"),
    ("rocket", "Submit"),
    ("bar-chart", "Runs"),
    ("settings", "Settings"),
]


def _show_submitted_runs(window: "MainWindow", run_ids: list[str]) -> None:
    if run_ids:
        window.state.current_batch_id = run_ids[-1]
    window.shell.sidebar.blockSignals(True)
    window.shell.sidebar.set_current(2)
    window.shell.sidebar.blockSignals(False)
    window.shell.pages.setCurrentIndex(2)
    window.shell.page_changed.emit(2)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JobDesk")
        self._settings_store = GuiSettingsStore()
        settings = self._settings_store.load()
        size = settings.window_size or [1320, 860]
        self.resize(size[0], size[1])
        self.state = AppState()
        self.language = settings.language
        self._file_logger = configure_file_logging()
        self.setStyleSheet(build_app_stylesheet())

        nav_items = [(icon, tr(label, self.language)) for icon, label in _NAV_ITEMS]
        self.shell = AppShell(nav_items)
        self.setCentralWidget(self.shell)

        # 4 pages
        self.files_page = FileTransferPage(self.state, self._log, self._update_status,
                                           self.show_error)
        self.submit_page = SubmitPage(
            self.state,
            language=self.language,
            on_status=self._update_status,
            on_error=self.show_error,
            settings_store=self._settings_store,
        )
        self.runs_page = RunsResultsPage(self.state, self._log, self._update_status)
        self.settings_page = SettingsServersPage(self.state, self._log, self._update_status)
        self.settings_page.language_changed.connect(self._on_language_changed)
        self.files_page.runs_submitted.connect(
            lambda run_ids: QTimer.singleShot(0, lambda: _show_submitted_runs(self, run_ids))
        )
        # Submit page → coordinator (background worker, like _run_selected_chunks)
        # Phase 2: only ``submit_requested`` remains; the legacy
        # ``create_only_requested`` path collapsed into the unified editor.
        self.submit_page.submit_requested.connect(self._on_submit_requested)
        # Phase 1.1: forward the nodegraph editor's tour_requested signal up
        # to a top-level dialog so the "Read 60-second tour" button on the
        # empty canvas actually does something.
        if hasattr(self.submit_page, "editor") and hasattr(self.submit_page.editor, "tour_requested"):
            self.submit_page.editor.tour_requested.connect(self._show_workflow_tour)
        # Cross-page push from Files page right-click menu.
        if hasattr(self.files_page, "use_as_input_received"):
            self.files_page.use_as_input_received.connect(self._on_use_as_input_received)
        # Phase 2.1 (review-round 2): empty-state cards raise navigation
        # signals; MainWindow owns the only public surface for switching
        # pages so we funnel every request through ``_switch_page`` and
        # keep the sidebar / page-stack in lockstep.
        if hasattr(self.files_page, "open_settings_requested"):
            self.files_page.open_settings_requested.connect(
                lambda: self._switch_page(3)
            )
        if hasattr(self.runs_page, "go_to_submit_requested"):
            self.runs_page.go_to_submit_requested.connect(
                lambda: self._switch_page(1)
            )
        # Review-fix: the Runs-page "Show example templates" button needs
        # the same destination as ``go_to_submit_requested`` PLUS a
        # request to pop the editor's Examples drawer, otherwise the
        # button only navigates and the user is still one click away
        # from a template -- the old behaviour was effectively a
        # duplicate "Go to Submit" button.
        if hasattr(self.runs_page, "go_to_submit_with_examples_requested"):
            self.runs_page.go_to_submit_with_examples_requested.connect(
                self._on_go_to_submit_with_examples
            )
        self.runs_page.startup_recovery_failed.connect(self._on_startup_recovery_failed)
        self.runs_page.startup_recovery_finished.connect(self._finish_startup_recovery)

        self.shell.add_page(self.files_page)   # 0
        self.shell.add_page(self.submit_page)  # 1
        self.shell.add_page(self.runs_page)    # 2
        self.shell.add_page(self.settings_page)  # 3

        self.shell.page_changed.connect(self._on_nav)
        self._apply_language()
        self.shell.set_current(0)
        self.files_page.setEnabled(False)
        self.runs_page.setEnabled(False)
        QTimer.singleShot(0, self.runs_page.start_startup_recovery)

    def _finish_startup_recovery(self) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        self.files_page.setEnabled(True)
        self.runs_page.setEnabled(True)

    def _on_startup_recovery_failed(self, error: str) -> None:
        self._finish_startup_recovery()
        self.show_error(tr("Operation recovery failed", self.language), error)

    def _on_nav(self, index: int):
        self._apply_language()
        page = self.shell.pages.widget(index)
        if hasattr(page, "on_activated"):
            page.on_activated()
        # Keep SubmitPage's server pill in sync with whatever Files page
        # is currently connected to.
        if index == 1 and page is self.submit_page:
            page.set_server_status(
                connected=self.files_page._service is not None,
                server_label=self.files_page._connected_server_id or "",
            )
            page.set_max_parallel(self.files_page.max_parallel_spin.value()
                                  if hasattr(self.files_page, "max_parallel_spin") else 1)
            # Review-fix: push the Files page's current remote directory
            # into Submit so the payload points at the same folder the
            # user just browsed. Without this line Submit silently
            # hardcoded ``"/"`` which broke for users without root
            # write permission. Defensive getattr for the rare case the
            # helper is renamed.
            page.set_remote_dir(
                (self.files_page.remote_path.text().strip() or "/")
                if hasattr(self.files_page, "remote_path")
                else "/"
            )

    def _switch_page(self, index: int) -> None:
        """Centralised page switcher for cross-page signals.

        Reviews caught two empty-state buttons (``Files → Open Settings``
        and ``Runs → Go to Submit``) that emitted navigation requests
        into the void because nothing listened. Funnel both through this
        helper so the sidebar / page-stack / language reload / page
        activation all stay in lockstep with the manual-click path.
        Mirrors the existing ``_on_use_as_input_received`` flow.
        """
        try:
            target = self.shell.pages.widget(index)
        except Exception:
            return
        if target is None:
            return
        # Block the sidebar's user signal so the existing _on_nav path
        # does not fire twice; we drive setCurrentIndex + page_changed
        # manually to keep semantics identical to a click.
        self.shell.sidebar.blockSignals(True)
        self.shell.sidebar.set_current(index)
        self.shell.sidebar.blockSignals(False)
        self.shell.pages.setCurrentIndex(index)
        self.shell.page_changed.emit(index)

    def _on_go_to_submit_with_examples(self) -> None:
        """Land on Submit and pop the editor's Examples drawer.

        Triggered by the Runs-page empty-state "Show example templates"
        button. We use ``QTimer.singleShot(0, ...)`` because the drawer
        is a modal menu driven by ``QMenu.exec_``: popping it before the
        page actually finishes switching would steal the event loop from
        the sidebar click handler. Deferring it lets the ``page_changed``
        signal propagate first so the user sees the editor frame render
        before the menu opens.
        """
        self._switch_page(1)
        editor = getattr(self.submit_page, "editor", None)
        if editor is None:
            return
        QTimer.singleShot(
            0,
            lambda: getattr(editor, "open_examples_menu", lambda: None)(),
        )

    def _apply_language(self):
        self.language = self._settings_store.load().language
        for i, (_icon, key) in enumerate(_NAV_ITEMS):
            self.shell.set_nav_label(i, tr(key, self.language))
        for page in (self.files_page, self.submit_page, self.runs_page, self.settings_page):
            if hasattr(page, "apply_language"):
                page.apply_language(self.language)

    def _on_language_changed(self, language: str):
        self.language = language
        self._apply_language()

    def _log(self, msg: str):
        self._file_logger.info(msg)

    def _make_exception_hook(self):
        logger = self._file_logger
        def _hook(exc_type, exc, tb):
            logger.exception("Uncaught GUI exception: %s", exc)
        return _hook

    def _update_status(self, msg: str):
        self._file_logger.info("STATUS: %s", msg)

    def show_error(self, title: str, message: str):
        self._file_logger.error("%s: %s", title, message)
        QMessageBox.critical(self, title, message)

    # ── Submit-page wiring ────────────────────────────────────────────────

    def _on_submit_requested(self, payload: SubmitPayload, submit: bool = True) -> None:
        """Run :class:`SubmitUseCase` in a background worker and report back."""
        from ..services.file_transfer_service import (
            ensure_safe_remote_path,
        )
        from ..services.submit_use_case import SubmitUseCase

        if payload.server_id != (self.files_page._connected_server_id or ""):
            self.show_error(
                tr("Submit", self.language),
                tr("Connect to a server first.", self.language),
            )
            return
        service = self.files_page._service
        if service is None:
            self.show_error(
                tr("Submit", self.language),
                tr("Connect to a server first.", self.language),
            )
            return

        try:
            ensure_safe_remote_path(payload.remote_dir)
        except Exception as exc:
            self.show_error(tr("Submit", self.language), str(exc))
            return

        workspace = Path(self.state.current_project_root or Path.cwd())

        def _run(_ctx):
            use_case = SubmitUseCase()
            batch = use_case.execute(payload)
            if not batch.ok:
                return batch
            for local_path, remote_target in zip(batch.local_paths, batch.remote_targets):
                records = service.upload_path(local_path, remote_target)
                _raise(records, remote_target)
            if batch.yaml_local_path is not None and batch.yaml_local_path.exists():
                yaml_target = batch.remote_targets[0].rsplit("/", 1)[0] + "/workflow.yaml"
                records = service.upload_path(batch.yaml_local_path, yaml_target)
                _raise(records, yaml_target)
            coordinator = RunCoordinator(
                RunService(workspace),
                server_lookup=lambda sid: load_servers().servers[sid],
                ssh_factory=create_ssh_client,
                sftp_factory=create_sftp_client,
            )
            outcomes = []
            for spec in batch.specs:
                if submit:
                    outcomes.append(coordinator.create_and_submit(spec, local_dir=str(workspace)))
                else:
                    outcomes.append(coordinator.create_run(spec, local_dir=str(workspace)))
            # Bundle into a single RunOperationOutcome-shaped payload.
            from ..services.run_coordinator import RunOperationOutcome
            combined = RunOperationOutcome()
            for outcome in outcomes:
                combined.records.extend(outcome.records)
                combined.submit_results.extend(outcome.submit_results)
                combined.errors.extend(outcome.errors)
            return combined

        def _done(outcome):
            self.submit_page.on_submission_result(outcome)
            if outcome.errors:
                self.show_error(tr("Submit", self.language), "\n".join(outcome.errors))
                return
            run_ids = [r.run_id for r in outcome.records if not outcome.errors]
            _show_submitted_runs(self, run_ids)

        def _err(exc):
            self.show_error(tr("Submit", self.language), str(exc))

        worker = BackgroundWorker(_run)
        worker.result.connect(_done)
        worker.error.connect(_err)
        worker.start()

    def _show_workflow_tour(self) -> None:
        """Open the 6-slide workflow tour dialog (Phase 1.1)."""
        # Lazy import keeps the dialog module out of the import-time
        # graph; gui/dialogs/__init__.py is intentionally not created.
        from .dialogs.workflow_tour_dialog import WorkflowTourDialog

        dialog = WorkflowTourDialog(parent=self, language=self.language)
        dialog.exec()

    def _on_use_as_input_received(self, sources: list) -> None:
        """Cross-page wire: Files right-click → Submit page."""
        try:
            self.submit_page.push_sources(list(sources))
        except Exception:
            return
        # Review-fix: same remote_dir inheritance as in _on_nav — the
        # right-click navigation should land on Submit with the Files
        # page's current browsing path, otherwise the user immediately
        # sees a different (or invalid) target directory and gets
        # confused.
        try:
            if hasattr(self.files_page, "remote_path"):
                self.submit_page.set_remote_dir(
                    self.files_page.remote_path.text().strip() or "/"
                )
        except Exception:
            pass
        # Navigate to the Submit page (index 1).
        self.shell.sidebar.blockSignals(True)
        self.shell.sidebar.set_current(1)
        self.shell.sidebar.blockSignals(False)
        self.shell.pages.setCurrentIndex(1)
        self.shell.page_changed.emit(1)

    def shutdown(self):
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        try:
            self._settings_store.update(window_size=[self.width(), self.height()])
        except Exception:
            pass
        for page in (self.files_page, self.submit_page, self.runs_page, self.settings_page):
            if hasattr(page, "shutdown"):
                try:
                    page.shutdown()
                except Exception:
                    pass
        from .workers import BackgroundWorker
        BackgroundWorker.wait_all()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)


def _raise(records, target):
    """Best-effort upload-error check (mirrors FileTransferPage's helper)."""
    for record in records or []:
        if getattr(record, "status", None) and getattr(record.status, "name", "") != "completed":
            raise RuntimeError(f"Upload failed for {target}")
        if getattr(record, "error", None):
            raise RuntimeError(f"Upload failed for {target}: {record.error}")
