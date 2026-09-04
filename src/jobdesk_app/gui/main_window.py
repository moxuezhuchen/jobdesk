"""JobDesk GUI — 4-page layout: Files / Submit / Runs+Results / Settings+Servers."""

import inspect
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QMainWindow, QMessageBox

from ..app_logging import configure_file_logging
from ..application.container import ApplicationContainer
from ..application.gui_ports import FilesPagePort, PageRefreshPort
from ..bootstrap import (
    GuiSettingsStore,
    RunMonitor,
    RunServiceTaskLookup,
    SessionPool,
    build_terminal_launch,
    create_sftp_client,
    create_ssh_client,
    get_default_servers_path,
    launch_terminal,
    load_servers,
)
from ..core.submit_payload import SubmitPayload
from .dependencies import configure_gui_dependencies
from .dialogs.submit_dialog import SubmitDialog
from .i18n import tr
from .layouts.shell import AppShell
from .pages.file_transfer_page import FileTransferPage
from .pages.runs_results_page import RunsResultsPage
from .pages.settings_servers_page import SettingsServersPage
from .pages.workflow_page import WorkflowPage
from .state import AppState
from .task_supervisor import GuiTaskSupervisor, TaskCallbacks
from .theme import build_app_stylesheet

# Sidebar nav items: (icon_name, label).  Labels are translated at runtime
# via :func:`i18n.tr` so adding a new entry here only needs the i18n key.
_NAV_ITEMS = [
    ("folder", "Files"),
    ("workflow", "Workflow"),
    ("bar-chart", "Runs"),
    ("settings", "Settings"),
]


def _construct_page_with_session_pool(page_factory, *args, session_pool, **kwargs):
    """Keep lightweight test/plugin page factories compatible with injection."""
    injected = {"session_pool": session_pool, **kwargs}
    signature = inspect.signature(page_factory)
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if not accepts_kwargs:
        injected = {key: value for key, value in injected.items() if key in signature.parameters}
    return page_factory(*args, **injected)


def _show_submitted_runs(window: "MainWindow", run_ids: list[str]) -> None:
    if run_ids:
        window.state.current_batch_id = run_ids[-1]
    window.shell.sidebar.blockSignals(True)
    window.shell.sidebar.set_current(2)
    window.shell.sidebar.blockSignals(False)
    window.shell.pages.setCurrentIndex(2)
    window.shell.page_changed.emit(2)


class MainWindow(QMainWindow):
    def __init__(
        self,
        session_pool: SessionPool | None = None,
        *,
        application: ApplicationContainer | None = None,
        task_supervisor: GuiTaskSupervisor | None = None,
    ) -> None:
        super().__init__()
        configure_gui_dependencies(
            settings_store_factory=GuiSettingsStore,
            ssh_factory=create_ssh_client,
            sftp_factory=create_sftp_client,
            monitor_factory=RunMonitor,
        )
        self.setWindowTitle("JobDesk")
        self._settings_store = GuiSettingsStore()
        settings = self._settings_store.load()
        size = settings.window_size or [1320, 860]
        self.resize(size[0], size[1])
        self.state = AppState()
        self._session_pool = session_pool or SessionPool(create_ssh_client, create_sftp_client)
        if application is None:
            from ..bootstrap import create_application

            application = create_application(Path.cwd(), session_pool=self._session_pool)
        self._application = application
        self._task_supervisor = task_supervisor or GuiTaskSupervisor(self)
        self._initial_nav_completed = False
        self.language = settings.language
        self._file_logger = configure_file_logging("jobdesk_app")
        # Keep one error dialog owned by the main window.  The static
        # QMessageBox.critical() helper creates a temporary Python wrapper
        # around a native modal dialog; on Windows/Python 3.13 that wrapper
        # could be collected after the dialog closed and crash shiboken on
        # the next Qt input event.  A persistent child avoids that lifetime
        # gap and also keeps repeated errors from creating overlapping boxes.
        self._error_message_box: QMessageBox | None = None
        self.setStyleSheet(build_app_stylesheet())

        nav_items = [(icon, tr(label, self.language)) for icon, label in _NAV_ITEMS]
        self.shell = AppShell(nav_items)
        self.setCentralWidget(self.shell)

        # 4 pages
        self.files_page = _construct_page_with_session_pool(
            FileTransferPage,
            self.state,
            self._log,
            self._update_status,
            self.show_error,
            session_pool=self._session_pool,
            files_application=self._application.files,
            settings_store=self._settings_store,
            server_loader=load_servers,
            run_task_lookup=RunServiceTaskLookup(),
            terminal_builder=build_terminal_launch,
            terminal_launcher=launch_terminal,
            servers_path_provider=get_default_servers_path,
        )
        self._files_port = FilesPagePort(self.files_page)
        self.workflow_page = WorkflowPage(
            self.state,
            language=self.language,
            workflows=self._application.workflows,
            settings_store=self._settings_store,
            on_status=self._update_status,
            on_error=self.show_error,
        )
        self.runs_page = _construct_page_with_session_pool(
            RunsResultsPage,
            self.state,
            self._log,
            self._update_status,
            session_pool=self._session_pool,
            run_application=self._application.runs,
        )
        self.settings_page = _construct_page_with_session_pool(
            SettingsServersPage,
            self.state,
            self._log,
            self._update_status,
            session_pool=self._session_pool,
            settings_application=self._application.settings,
        )
        self.settings_page.language_changed.connect(self._on_language_changed)
        self.files_page.runs_submitted.connect(
            lambda run_ids: QTimer.singleShot(0, lambda: _show_submitted_runs(self, run_ids))
        )
        # Files page → Submit dialog (Phase 2.0 dual-entry refactor).
        if hasattr(self.files_page, "submit_requested_with_files"):
            self.files_page.submit_requested_with_files.connect(self._open_submit_dialog)
        # Workflow page → switch to Files with the preset highlighted.
        if hasattr(self.workflow_page, "preset_chosen_for_submit"):
            self.workflow_page.preset_chosen_for_submit.connect(self._on_workflow_chosen)
        # Review-round 3: the Workflow-page ``[New workflow]`` button
        # now opens the modal ``WorkflowBuilderDialog``. ``MainWindow``
        # doesn't need to do anything special here -- the dialog
        # itself owns the Save flow -- but we still subscribe to the
        # ``workflow_authored`` signal so a saved-and-then-submit chain
        # (``Save in the modal → route through SubmitDialog``) keeps
        # the sidebar in sync with the freshly-saved preset. The
        # actual save is performed in ``WorkflowPage._offer_save_*``;
        # this listener just refreshes the status line.
        if hasattr(self.workflow_page, "workflow_authored"):
            self.workflow_page.workflow_authored.connect(self._on_workflow_authored)
        # Cross-page push from Files page right-click menu.
        if hasattr(self.files_page, "use_as_input_received"):
            self.files_page.use_as_input_received.connect(self._on_use_as_input_received)
        # Phase 2.1 (review-round 2): empty-state cards raise navigation
        # signals; MainWindow owns the only public surface for switching
        # pages so we funnel every request through ``_switch_page`` and
        # keep the sidebar / page-stack in lockstep.
        if hasattr(self.files_page, "open_settings_requested"):
            self.files_page.open_settings_requested.connect(lambda: self._switch_page(3))
        if hasattr(self.runs_page, "go_to_submit_requested"):
            self.runs_page.go_to_submit_requested.connect(self._on_runs_go_to_submit)
        # Review-fix: the Runs-page "Show example templates" button needs
        # the same destination as ``go_to_submit_requested`` PLUS a
        # request to pop the editor's Examples drawer, otherwise the
        # button only navigates and the user is still one click away
        # from a template -- the old behaviour was effectively a
        # duplicate "Go to Submit" button.
        if hasattr(self.runs_page, "go_to_submit_with_examples_requested"):
            self.runs_page.go_to_submit_with_examples_requested.connect(self._on_go_to_submit_with_examples)
        self.runs_page.startup_recovery_failed.connect(self._on_startup_recovery_failed)
        self.runs_page.startup_recovery_finished.connect(self._finish_startup_recovery)

        self.shell.add_page(self.files_page)  # 0
        self.shell.add_page(self.workflow_page)  # 1
        self.shell.add_page(self.runs_page)  # 2
        self.shell.add_page(self.settings_page)  # 3

        self.shell.page_changed.connect(self._on_nav)
        self._install_shortcuts()
        # Applying translations must not synchronously open the runs
        # database while the window is still being constructed.  The Runs
        # page is disabled until startup recovery completes and refreshes
        # lazily when it is activated, so only update its labels here.
        self._apply_language(refresh_runs=False)
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
        # Navigation can be emitted while the shell is still being
        # initialised (``set_current(0)`` below).  Keep this path limited to
        # translation work; RunsResultsPage.on_activated schedules its
        # database refresh asynchronously once the target page is visible.
        self._apply_language(refresh_runs=False)
        page = self.shell.pages.widget(index)
        on_activated = getattr(page, "on_activated", None)
        if callable(on_activated):
            on_activated()
        # Keep WorkflowPage's server pill in sync with whatever Files page
        # is currently connected to.
        if index == 1 and page is self.workflow_page:
            connection = self._files_port.snapshot()
            if hasattr(page, "set_server_status"):
                page.set_server_status(
                    connected=connection.connected,
                    server_label=connection.server_id or "",
                )
            if hasattr(page, "set_remote_dir"):
                page.set_remote_dir(connection.remote_dir)
        if index == 0:
            # Refresh the Files page so a returning user sees fresh state.
            # The first activation already performs its initial connection and
            # local refresh.  Skipping this duplicate call prevents two
            # concurrent WSL bootstrap/list workers on application startup.
            if self._initial_nav_completed:
                try:
                    self._files_port.refresh()
                except Exception:
                    pass
            self._initial_nav_completed = True
        # Apply language whenever the user changes pages (cheap; cached).
        # Keep the Runs page in label-only mode here as well; its activation
        # callback owns the deferred run-list refresh.
        for page in (self.files_page, self.workflow_page, self.runs_page, self.settings_page):
            if hasattr(page, "apply_language"):
                if page is self.runs_page:
                    try:
                        page.apply_language(self.language, refresh=False)
                    except TypeError:
                        page.apply_language(self.language)
                else:
                    page.apply_language(self.language)

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

    def _install_shortcuts(self) -> None:
        """Install discoverable window-level navigation and page actions."""
        self._shortcuts: list[QShortcut] = []

        def bind(sequence: str, callback) -> None:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

        for index in range(4):
            bind(f"Alt+{index + 1}", lambda target=index: self._switch_page(target))
        bind("F5", self._refresh_current_page)
        bind("Ctrl+F", self._focus_current_search)
        bind("Ctrl+S", self._save_current_page)

    def _current_page(self):
        return self.shell.pages.currentWidget()

    def _refresh_current_page(self) -> None:
        page = self._current_page()
        # Prefer a page's full refresh action so F5 is equivalent to the
        # visible Refresh button (the Runs page also refreshes remote status).
        PageRefreshPort.for_page(page).refresh()

    def _focus_current_search(self) -> None:
        focus_search = getattr(self._current_page(), "focus_search", None)
        if callable(focus_search):
            focus_search()

    def _save_current_page(self) -> None:
        save_current = getattr(self._current_page(), "save_current", None)
        if callable(save_current):
            save_current()

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
        # The Submit page no longer embeds the WorkflowGraphEditor. The
        # editor lives inside the modal WorkflowBuilderDialog that the
        # user opens from the Workflow page when they want to author or
        # edit a preset. Examples drawer is reachable from inside that
        # dialog, so there's nothing to do here. We keep the navigation
        # switch so the empty-state button still works.
        return

    def _on_runs_go_to_submit(self) -> None:
        """Wire the Runs-page ``go_to_submit_requested`` signal.

        Phase 2.0 dual entry: when the Runs page has no runs it shows an
        empty-state hint. Clicking **Go to Submit** used to navigate
        to the Workflow page (index 1) and stop — the old behaviour was
        a dead link because the Submit-dialog trigger lives on the
        Files page. Now we open the modal ``SubmitDialog`` directly
        with an empty sources list. The dialog renders an empty-state
        hint and stays in Workflow mode so the user can still pick a
        preset to submit (with no files selected yet).
        """
        self._open_submit_dialog([])

    def _apply_language(self, *, refresh_runs: bool = True):
        self.language = self._settings_store.load().language
        for i, (_icon, key) in enumerate(_NAV_ITEMS):
            self.shell.set_nav_label(i, tr(key, self.language))
        for page in (self.files_page, self.workflow_page, self.runs_page, self.settings_page):
            if hasattr(page, "apply_language"):
                if page is self.runs_page:
                    # RunsResultsPage.apply_language accepts ``refresh`` so
                    # startup can translate widgets without doing database
                    # I/O.  Keep the fallback for lightweight test doubles
                    # that implement the historical one-argument method.
                    try:
                        page.apply_language(self.language, refresh=refresh_runs)
                    except TypeError:
                        page.apply_language(self.language)
                else:
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
        box = self._error_message_box
        if box is None:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Critical)
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.setModal(True)
            self._error_message_box = box
        box.setWindowTitle(title)
        box.setText(message)
        # ``open`` keeps the dialog application-modal without entering a
        # nested event loop.  The main window owns ``box`` for its lifetime,
        # so closing it cannot leave a stale Shiboken wrapper behind.
        box.open()

    # ── Submit-page wiring ────────────────────────────────────────────────

    def _on_submit_requested(self, payload: SubmitPayload, submit: bool = True) -> None:
        """Delegate the complete submission transaction to the application."""

        connection = self._files_port.snapshot()
        if payload.server_id != (connection.server_id or ""):
            self.show_error(
                tr("Submit", self.language),
                tr("Connect to a server first.", self.language),
            )
            return
        if not connection.ready:
            self.show_error(
                tr("Submit", self.language),
                tr("Connect to a server first.", self.language),
            )
            return

        busy_lease = self._task_supervisor.acquire_busy("main-window-submit", "submit")
        if busy_lease is None:
            self._update_status(tr("Remote operation already in progress", self.language))
            return

        def _run(_ctx):
            return self._application.runs.submit(payload, dispatch=submit)

        def _done(outcome):
            value = outcome.value
            self.runs_page.set_submit_warnings(list(value.warnings) if value else [])
            if outcome.failures:
                self.show_error(
                    tr("Submit", self.language),
                    "\n".join(failure.display_text for failure in outcome.failures),
                )
                return
            run_ids = [run.summary.run_id for run in value.runs] if value else []
            _show_submitted_runs(self, run_ids)

        def _err(exc):
            self.show_error(tr("Submit", self.language), str(exc))

        try:
            self._task_supervisor.start(
                "main-window",
                "submit",
                _run,
                TaskCallbacks(on_result=_done, on_error=_err),
                busy_lease=busy_lease,
            )
        except Exception as exc:
            # ``start`` releases the lease when native worker construction or
            # startup fails.  Keep the synchronous failure on the same UI
            # error path as an asynchronous worker error.
            self.show_error(tr("Submit", self.language), str(exc))

    def _show_workflow_tour(self) -> None:
        """Open the 6-slide workflow tour dialog (Phase 1.1)."""
        # Lazy import keeps the dialog module out of the import-time
        # graph; gui/dialogs/__init__.py is intentionally not created.
        from .dialogs.workflow_tour_dialog import WorkflowTourDialog

        dialog = WorkflowTourDialog(parent=self, language=self.language)
        dialog.exec()

    def _on_use_as_input_received(self, sources: list) -> None:
        """Cross-page wire: Files right-click → open the Submit dialog.

        The legacy behaviour pushed sources onto the Submit page and
        navigated to it; in Phase 2.0 we open the modal dialog directly
        so the user sees the auto-detected mode immediately. We keep the
        signal name so the Files page does not need to change.
        """
        self._open_submit_dialog(list(sources))

    def _open_submit_dialog(
        self,
        sources: list,
        *,
        preset_name: str | None = None,
        seed_preset_from_files: bool = True,
    ) -> None:
        """Open :class:`SubmitDialog` and forward the resulting payload.

        Parameters
        ----------
        sources:
            The list of :class:`InputSource` to seed the dialog with. May
            be empty — the dialog renders a "no files selected" empty
            state in that case and the workflow mode is forced so the
            user can still pick a preset to submit later.
        preset_name:
            Pre-select a method preset in the dialog's preset combo.
            Used by the Workflow-page "Use this preset for submit"
            button (Phase 2.0 dual entry).
        seed_preset_from_files:
            Defaults to ``True``. When ``True`` and no explicit
            ``preset_name`` is provided AND the user has any saved
            presets, prefer the first user-built preset so a "fresh"
            Workflow-mode dialog is not left with a bare combo box.
            Pass ``False`` to keep the dialog on the dialog-side
            default (first builtin).
        """
        connection = self._files_port.snapshot()
        server_id = connection.server_id or ""
        remote_dir = connection.remote_dir
        dialog = SubmitDialog(
            self.language,
            files=list(sources),
            server_id=server_id,
            remote_dir=remote_dir,
            max_parallel=1,
            workspace=Path(self.state.current_project_root or Path.cwd()),
            workflows=self._application.workflows,
            preset_name=preset_name,
            parent=self,
        )
        # Wire the dialog's status callback so ``[Save workflow.yaml]``
        # reports its outcome in the same status line as the rest of
        # the app instead of swallowing it. Review-round 3.
        if hasattr(dialog, "set_status_callback"):
            dialog.set_status_callback(self._update_status)
        # If the caller didn't pin a preset and no files are selected,
        # pre-select the first user preset if any (best UX). We do this
        # AFTER construction because the constructor can't read
        # ``_preset_store.list_presets()`` order with priority logic
        # without duplicating it here.
        if preset_name is None and not sources and seed_preset_from_files:
            try:
                presets = [p for p in self._preset_store.list_presets() if getattr(p, "source", "") == "user"]
                if presets:
                    dialog.set_selected_preset_name(presets[0].name)
            except Exception:
                pass
        if dialog.exec() == SubmitDialog.DialogCode.Accepted:
            payload = dialog.build_payload()
            self._on_submit_requested(payload)

    def _on_workflow_chosen(self, name: str, source: str) -> None:
        """WorkflowPage → SubmitDialog with the picked preset pre-selected.

        Phase 2.0 dual entry: clicking **Use this preset for submit** on
        the Workflow page used to only flip the sidebar to Files. Now we
        open the modal ``SubmitDialog`` directly with the preset
        pre-selected so the user lands one click from ``Submit ▶``.
        The Files page is also brought to the foreground so the dialog
        inherits the current ``server_id`` and ``remote_dir`` from
        Files' toolbar (inherited by ``_open_submit_dialog``).
        """
        preset_name = name if name else None
        # Switch to Files first so the dialog reads Files-page toolbar
        # state (server_id + remote_dir). If the switch fails (page not
        # registered yet, test harness, etc.) we still try to open the
        # dialog with the Files page's public connection snapshot.
        try:
            self._switch_page(0)
        except Exception:
            pass
        # No files selected at the moment of clicking the Workflow-page
        # button — that's the expected Phase-2.0 flow (user picks a
        # preset first, then drags files in). The dialog renders an
        # empty-state and stays open in Workflow mode.
        self._open_submit_dialog([], preset_name=preset_name)

    def _on_workflow_authored(self, _spec, name: str) -> None:
        """Sidebar status feedback after the user authors a workflow.

        Review-round 3: the modal ``WorkflowBuilderDialog`` (Save in
        ``[New workflow]`` / ``[Edit in builder]``) emits
        ``workflow_authored`` after persisting the new preset. We
        surface a status line so the user sees the outcome in the
        same place as ``Save as user preset``.
        """
        if name:
            self._update_status(tr("Workflow preset loaded: {name}", self.language, name=name))

    def shutdown(self):
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        try:
            self._settings_store.update(window_size=[self.width(), self.height()])
        except Exception:
            pass
        # Invalidate the main-window generation before pages or the shared
        # session pool are torn down.  A queued submit result can therefore
        # never touch Qt widgets after shutdown begins.
        self._task_supervisor.shutdown()
        for page in (self.files_page, self.workflow_page, self.runs_page, self.settings_page):
            if hasattr(page, "shutdown"):
                try:
                    page.shutdown()
                except Exception:
                    pass
        try:
            self._application.close()
        except Exception:
            self._file_logger.exception("Application container shutdown failed")
        logger = getattr(self, "_file_logger", None)
        if logger is not None:
            for handler in list(getattr(logger, "handlers", ())):
                try:
                    logger.removeHandler(handler)
                    handler.close()
                except Exception:
                    pass
            try:
                logger.propagate = True
            except Exception:
                pass

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
