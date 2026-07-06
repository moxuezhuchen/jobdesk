"""JobDesk GUI — 3-page layout: Files / Runs+Results / Settings+Servers."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow, QMessageBox

from ..app_logging import configure_file_logging
from ..services.gui_settings import GuiSettingsStore
from .i18n import tr
from .layouts.shell import AppShell
from .pages.file_transfer_page import FileTransferPage
from .pages.runs_results_page import RunsResultsPage
from .pages.settings_servers_page import SettingsServersPage
from .pages.workflow_builder_page import WorkflowBuilderPage
from .state import AppState
from .theme import build_app_stylesheet

_NAV_ITEMS = [
    ("workflow", "Workflow"),
    ("folder", "Files"),
    ("rocket", "Runs"),
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
        # Persisted across GUI sessions: last-viewed agent server.
        if settings.last_agent_server:
            self.state.last_agent_server = settings.last_agent_server
        self.language = settings.language
        self._file_logger = configure_file_logging()
        self.setStyleSheet(build_app_stylesheet())

        nav_items = [(icon, tr(label, self.language)) for icon, label in _NAV_ITEMS]
        self.shell = AppShell(nav_items)
        self.setCentralWidget(self.shell)

        # 4 pages
        self.workflow_page = WorkflowBuilderPage(self.state, self._log, self._update_status,
                                                  self.show_error)
        self.files_page = FileTransferPage(self.state, self._log, self._update_status,
                                           self.show_error)
        self.runs_page = RunsResultsPage(self.state, self._log, self._update_status,
                                          error_cb=self.show_error)
        self.settings_page = SettingsServersPage(self.state, self._log, self._update_status)
        self.settings_page.language_changed.connect(self._on_language_changed)
        self.files_page.runs_submitted.connect(
            lambda run_ids: QTimer.singleShot(0, lambda: _show_submitted_runs(self, run_ids))
        )
        self.files_page.agent_jobs_submitted.connect(
            lambda job_ids, srv: (
                QTimer.singleShot(0, lambda j=job_ids, s=srv: self._show_agent_jobs(j, s))
            )
        )
        self.runs_page.startup_recovery_failed.connect(self._on_startup_recovery_failed)
        self.runs_page.startup_recovery_finished.connect(self._finish_startup_recovery)
        # Persist last-viewed agent server across GUI sessions.
        self.runs_page.agent_server_changed.connect(self._persist_last_agent_server)
        self.workflow_page.workflow_built.connect(self._on_workflow_built)

        self.shell.add_page(self.workflow_page)
        self.shell.add_page(self.files_page)
        self.shell.add_page(self.runs_page)
        self.shell.add_page(self.settings_page)

        self.shell.page_changed.connect(self._on_nav)
        self._apply_language()
        self.shell.set_current(0)  # start on Workflow tab
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

    def _apply_language(self):
        self.language = self._settings_store.load().language
        for i, (_icon, key) in enumerate(_NAV_ITEMS):
            self.shell.set_nav_label(i, tr(key, self.language))
        for page in (self.workflow_page, self.files_page, self.runs_page, self.settings_page):
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

    def _show_agent_jobs(self, job_ids: list[str], server_id: str) -> None:
        """Navigate to the Runs page and signal agent jobs to refresh."""
        self.shell.sidebar.blockSignals(True)
        self.shell.sidebar.set_current(2)
        self.shell.sidebar.blockSignals(False)
        self.shell.pages.setCurrentIndex(2)
        self.shell.page_changed.emit(2)
        self.runs_page.show_agent_jobs(job_ids, server_id)

    def _on_workflow_built(self, yaml_text: str, _payload: dict) -> None:
        """Stage 4 wizard finished building a YAML — stash it for the Files page
        to pick up. The Files page is responsible for the actual SSH/SFTP
        upload + ``AgentBridge.submit_job()`` call so we keep one canonical
        submit path."""
        server_id = getattr(self.state, "last_agent_server", None) or ""
        self.state.current_manifest_path = None  # wizard has no manifest
        # Stash the YAML text for the Files page; it checks this attribute when
        # the user clicks Run ConfFlow.
        setattr(self.state, "_wizard_yaml", yaml_text)
        setattr(self.state, "_wizard_server", server_id)
        self._update_status(
            f"Workflow YAML ready ({len(yaml_text)} bytes). Switch to Files, "
            f"select an XYZ, and click Run ConfFlow to submit."
        )

    def _persist_last_agent_server(self, server_id: str) -> None:
        """Persist the agent-view server choice to GuiSettingsStore.

        Called via ``runs_page.agent_server_changed`` signal so the choice
        survives across GUI close/reopen.
        """
        try:
            self._settings_store.update(last_agent_server=server_id or "")
        except Exception as exc:
            self._log(f"Failed to persist last_agent_server: {exc}")

    def shutdown(self):
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        try:
            self._settings_store.update(
                window_size=[self.width(), self.height()],
                last_agent_server=self.state.last_agent_server or "",
            )
        except Exception:
            pass
        for page in (self.workflow_page, self.files_page, self.runs_page, self.settings_page):
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
