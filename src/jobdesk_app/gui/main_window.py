"""JobDesk GUI — 4-page layout: Files / Submit / Runs+Results / Settings+Servers."""

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow, QMessageBox

from ..app_logging import configure_file_logging
from ..config.servers import load_servers
from ..core.submit_payload import SubmitPayload
from ..services.gui_settings import GuiSettingsStore
from ..services.method_presets import MethodPresetStore
from ..services.run_coordinator import RunCoordinator
from ..services.run_service import RunService
from .dialogs.submit_dialog import SubmitDialog
from .i18n import tr
from .layouts.shell import AppShell
from .pages.file_transfer_page import FileTransferPage
from .pages.runs_results_page import RunsResultsPage
from .pages.settings_servers_page import SettingsServersPage
from .pages.workflow_page import WorkflowPage
from .session import create_sftp_client, create_ssh_client
from .state import AppState
from .theme import build_app_stylesheet
from .workers import BackgroundWorker

# Sidebar nav items: (icon_name, label).  Labels are translated at runtime
# via :func:`i18n.tr` so adding a new entry here only needs the i18n key.
_NAV_ITEMS = [
    ("folder", "Files"),
    ("workflow", "Workflow"),
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
        self.files_page = FileTransferPage(self.state, self._log, self._update_status, self.show_error)
        self._preset_store = MethodPresetStore()
        self.workflow_page = WorkflowPage(
            self.state,
            language=self.language,
            preset_store=self._preset_store,
            settings_store=self._settings_store,
            on_status=self._update_status,
            on_error=self.show_error,
        )
        self.runs_page = RunsResultsPage(self.state, self._log, self._update_status)
        self.settings_page = SettingsServersPage(self.state, self._log, self._update_status)
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
        if hasattr(page, "on_activated"):
            page.on_activated()
        # Keep WorkflowPage's server pill in sync with whatever Files page
        # is currently connected to.
        if index == 1 and page is self.workflow_page:
            if hasattr(page, "set_server_status"):
                page.set_server_status(
                    connected=self.files_page._service is not None,
                    server_label=self.files_page._connected_server_id or "",
                )
            if hasattr(page, "set_remote_dir") and hasattr(self.files_page, "remote_path"):
                page.set_remote_dir(self.files_page.remote_path.text().strip() or "/")
        if index == 0:
            # Refresh the Files page so a returning user sees fresh state.
            refresh = getattr(self.files_page, "refresh", None) or getattr(self.files_page, "_refresh_all", None)
            if refresh is not None:
                try:
                    refresh()
                except Exception:
                    pass
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
            for local_path, remote_target in zip(
                batch.local_paths,
                batch.upload_targets,
                strict=True,
            ):
                records = service.upload_path(local_path, remote_target)
                _raise(records, remote_target)
            if batch.yaml_local_path is not None and batch.yaml_local_path.exists():
                yaml_target = batch.yaml_remote_path
                if yaml_target is None:
                    raise RuntimeError("Prepared workflow batch has no remote YAML target")
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
        server_id = self.files_page._connected_server_id or ""
        remote_dir = "/"
        try:
            if hasattr(self.files_page, "remote_path"):
                remote_dir = self.files_page.remote_path.text().strip() or "/"
        except Exception:
            pass
        dialog = SubmitDialog(
            self.language,
            files=list(sources),
            server_id=server_id,
            remote_dir=remote_dir,
            max_parallel=1,
            workspace=Path(self.state.current_project_root or Path.cwd()),
            preset_store=self._preset_store,
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
        # dialog with whatever is on _files_page._connected_server_id.
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
        for page in (self.files_page, self.workflow_page, self.runs_page, self.settings_page):
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
