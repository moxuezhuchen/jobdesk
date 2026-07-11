"""SubmitPage — first-class unified submit UI powered by the node-graph editor.

Phase 2 replaces the legacy "Build input file | Build workflow" tabs (and the
three buttons below them) with a single :class:`WorkflowGraphEditor`. The page
now drives everything off the editor's :class:`NodeGraph`: live preview is the
graph serialised through :func:`to_workflow_spec`, and the only remaining
actions are **Generate YAML** (instant) and **Submit to Remote** (primary).

Layout (top to bottom):

    ┌─────────────────────────────────────────────────┐
    │ InputSourcePanel  (local / remote tabs)         │
    ├─────────────────────────────────────────────────┤
    │ WorkflowGraphEditor (toolbar / library / canvas │
    │   / properties / status pill)                   │
    ├─────────────────────────────────────────────────┤
    │ server pill | max parallel spin                 │
    │ [Generate YAML]  [Submit to Remote]              │
    │ (live YAML preview reflects graph state)        │
    ├─────────────────────────────────────────────────┤
    │ Activity log                                    │
    └─────────────────────────────────────────────────┘

Public signals:

* :pyattr:`submit_requested(SubmitPayload)` — fires on "Submit to Remote".
* :pyattr:`use_as_input_received(list)` — fires when cross-page push from
  the Files page right-click menu flows in.

The Phase 14B :pyattr:`create_only_requested` signal was removed along with
its button in Phase 2: the wizard's "Create tasks only" path collapsed into
the unified editor, and downstream consumers should go through the
``submit_requested`` payload with ``kind="confflow"``. The Phase 10.6
cleanup removed the legacy :class:`InputBuilderWidget` /
:class:`CalculationWidget` / :class:`WorkflowWidget` modules; this page
is now driven entirely by the node-graph editor.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.submit_payload import (
    DagWorkflowFields,
    InputSource,
    SubmitKind,
    SubmitPayload,
    WorkflowFields,
)
from ...services.submit_use_case import PreparedBatch, SubmitUseCase
from ..button_feedback import ButtonRole, apply_button_role
from ..i18n import tr
from ..nodegraph import (
    WorkflowGraphPayload,
    WorkflowSpecError,
    to_workflow_spec,
)
from ..nodegraph.editor import WorkflowGraphEditor
from ..widgets import InlineBanner
from ..widgets.input_source_panel import InputSourcePanel

_ACTIVITY_LIMIT = 50
_PREVIEW_DEBOUNCE_MS = 150


class SubmitPage(QWidget):
    """Embedded unified-submit widget driven by the node-graph editor."""

    submit_requested = Signal(object)  # SubmitPayload
    use_as_input_received = Signal(list)  # list[InputSource]

    def __init__(
        self,
        state: Any,
        parent: QWidget | None = None,
        *,
        language: str = "en",
        on_status: Callable[[str], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
        remote_available: bool = False,
        server_label: str = "",
        activity_repo: Any = None,
        settings_store: Any = None,
    ):
        super().__init__(parent)
        self.state = state
        self._language = language
        self._settings_store = settings_store
        self._on_status = on_status or (lambda msg: None)
        self._on_error = on_error or (lambda title, msg: None)
        self._remote_available = remote_available
        self._server_label = server_label
        # Phase 14D→review-fix: track the Files page's current remote
        # directory so the Submit payload points at the same place the
        # user is browsing. Defaults to "/" only if nothing has been
        # pushed yet; set_remote_dir() (called from MainWindow when the
        # user navigates here) overrides it. Users normally cannot
        # upload to "/" anyway, so inheriting the browsing context
        # avoids the common "submit fails because root is not writable"
        # mistake called out in review.
        self._remote_dir = "/"
        self._use_case = SubmitUseCase()
        self._last_batch: PreparedBatch | None = None
        self._activity_repo = activity_repo or getattr(state, "repo", None)
        self.load_recent_activity()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        # ── 1. Input source panel ──────────────────────────────────────────
        self.input_panel = InputSourcePanel(
            language=language, remote_available=remote_available
        )
        self.input_panel.sources_changed.connect(self._on_sources_changed)
        self.input_panel.add_files_requested.connect(self._on_add_files_requested)
        layout.addWidget(self.input_panel)

        # ── 1b. Inline banner (Phase 3.1) — surfaces non-modal warnings/errors
        # such as "preview failed" or "validation errors" right below the
        # input panel so the user notices without having to look at the
        # activity log. The banner is hidden until something calls
        # ``show_warning`` / ``show_error`` on it.
        self._banner = InlineBanner(language=language, parent=self)
        layout.addWidget(self._banner)

        # ── 2. Node-graph editor ───────────────────────────────────────
        # ``WorkflowGraphEditor`` is a plain QWidget designed to be
        # embedded in a layout. We add it with stretch=1 so it absorbs
        # the vertical space between the input panel and the action
        # row.
        self.editor = WorkflowGraphEditor(language=language, parent=self, settings_store=self._settings_store)
        self.editor.graph_changed.connect(self._on_graph_changed)
        layout.addWidget(self.editor, 1)

        # ── 3. Server + max-parallel controls (live with the buttons) ─────
        # The Phase 14B 3-button shape is gone, but the server pill and
        # the max-parallel spin still need a home; we keep them just
        # above the action buttons so the user can change concurrency
        # before submitting.
        self.server_pill = QLabel(self._server_pill_text())
        self.server_pill.setStyleSheet("padding: 4px 10px; border-radius: 10px;")
        # Phase 1.2: small in-page hint shown when the user has inputs but no
        # server is currently selected. Not a gate — the submit button stays
        # clickable so the existing MainWindow path ("Connect to a server
        # first.") still surfaces a real error if the user presses submit.
        # This is a heads-up so the user sees the disconnect *before* clicking.
        self.server_hint = QLabel(self)
        self.server_hint.setObjectName("SubmitServerHint")
        self.server_hint.setStyleSheet("color: #b54708; font-style: italic;")
        self.server_hint.setVisible(False)
        self.max_parallel_label = QLabel(tr("Max parallel:", language))
        self.max_parallel_spin = QSpinBox()
        self.max_parallel_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.max_parallel_spin.setRange(1, 9999)
        self.max_parallel_spin.setValue(1)
        # Review-fix: surface the currently-inherited remote target so
        # the user can see exactly where the workflow will land before
        # clicking Submit. The label starts blank until
        # ``set_remote_dir`` is called from MainWindow.
        self.remote_target_label = QLabel("", self)
        self.remote_target_label.setObjectName("SubmitRemoteTargetLabel")
        self.remote_target_label.setStyleSheet("color: #4b5563; font-size: 9pt;")
        self.remote_target_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        server_row = QHBoxLayout()
        server_row.setSpacing(8)
        server_row.addWidget(self.server_pill)
        server_row.addWidget(self.server_hint, 1)
        server_row.addStretch()
        server_row.addWidget(self.max_parallel_label)
        server_row.addWidget(self.max_parallel_spin)
        server_row.addWidget(self.remote_target_label, 0)
        layout.addLayout(server_row)

        # ── 4. Two-button row (replaces Submit/Create-only/Refresh) ────────
        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addStretch()

        self.generate_btn = apply_button_role(
            QPushButton(tr("Generate YAML", language)),
            ButtonRole.INSTANT_ACTION,
        )
        self.generate_btn.clicked.connect(self._on_generate_clicked)
        button_row.addWidget(self.generate_btn)

        self.submit_btn = QPushButton(tr("Submit to Remote", language))
        self.submit_btn.setObjectName("PrimaryBtn")
        apply_button_role(self.submit_btn, ButtonRole.PRIMARY_ACTION)
        self.submit_btn.clicked.connect(self._on_submit_clicked)
        button_row.addWidget(self.submit_btn)
        layout.addLayout(button_row)

        # ── 5. Live preview pane (workflow YAML) ───────────────────────────
        self._preview_box = QGroupBox(tr("Live preview", language))
        pv_layout = QVBoxLayout(self._preview_box)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        font = QFont("Courier New")
        font.setStyleHint(QFont.Monospace)
        self.preview.setFont(font)
        self.preview.setMinimumHeight(160)
        pv_layout.addWidget(self.preview)
        layout.addWidget(self._preview_box)

        # ── 6. Activity log ────────────────────────────────────────────────
        self._log_box = QGroupBox(tr("Activity log", language))
        log_layout = QVBoxLayout(self._log_box)
        self.activity_list = QListWidget()
        self.activity_list.setMaximumHeight(120)
        log_layout.addWidget(self.activity_list)
        layout.addWidget(self._log_box)

        # ── 7. Debounced live-preview refresh ─────────────────────────────
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(_PREVIEW_DEBOUNCE_MS)
        self._preview_timer.timeout.connect(self._refresh_preview)

        # ── 8. Initial validation / preview ──────────────────────────────
        self._refresh_remote_target_label()
        self._refresh_validation()
        self._refresh_preview()

    # ── Public API ────────────────────────────────────────────────────────

    def apply_language(self, language: str) -> None:
        """Re-translate every static label."""
        self._language = language
        self.input_panel.apply_language(language)
        self.editor.apply_language(language)
        self.generate_btn.setText(tr("Generate YAML", language))
        self.submit_btn.setText(tr("Submit to Remote", language))
        self.max_parallel_label.setText(tr("Max parallel:", language))
        # Phase 11.1 — F5 fix. Group titles and the server pill are
        # also static text; without these lines a runtime language
        # switch left half the page in the previous language.
        self.server_pill.setText(self._server_pill_text())
        if not self._server_label:
            self.server_hint.setText(tr("Connect to a server first.", self._language))
        else:
            self.server_hint.setText("")
        self._preview_box.setTitle(tr("Live preview", language))
        self._log_box.setTitle(tr("Activity log", language))
        self._refresh_remote_target_label()
        self._banner.apply_language(language)

    def set_server_status(self, connected: bool, server_label: str = "") -> None:
        """Update the server pill text and active state.

        Adds the Remote tab on connect, removes it on disconnect. The
        tab itself mirrors the Local tab's structure (same buttons +
        drag/drop), so the user can pick remote paths and have them
        flow through the same :class:`InputSource` list.
        """
        self._server_label = server_label
        self._remote_available = connected
        self.server_pill.setText(self._server_pill_text())
        self._refresh_server_hint()
        if connected and self.input_panel.remote_tab is None:
            self.input_panel.remote_tab = self.input_panel._build_tab("remote")
            self.input_panel.remote_tab.btn_add.clicked.connect(self.input_panel._on_add_files_remote)
            self.input_panel.remote_tab.btn_remove.clicked.connect(self.input_panel._on_remove)
            self.input_panel.remote_tab.btn_clear.clicked.connect(self.input_panel._on_clear)
            self.input_panel.remote_tab.recursive_cb.toggled.connect(self.input_panel._on_recursive_toggled)
            self.input_panel.tabs.addTab(
                self.input_panel.remote_tab, tr("Remote", self._language)
            )
            self.input_panel.remote_tab.recursive_cb.setChecked(
                self.input_panel.local_tab.recursive_cb.isChecked()
            )
        elif not connected and self.input_panel.remote_tab is not None:
            idx = self.input_panel.tabs.indexOf(self.input_panel.remote_tab)
            if idx >= 0:
                self.input_panel.tabs.removeTab(idx)
            self.input_panel.remote_tab.deleteLater()
            self.input_panel.remote_tab = None

    def on_submission_result(self, payload: object) -> None:
        """Called by the main window after the worker completes."""
        batch_id = ""
        errors: list[str] = []
        try:
            records = list(getattr(payload, "records", []) or [])
            if records:
                batch_id = str(getattr(records[0], "run_id", ""))
            errors = list(getattr(payload, "errors", []) or [])
        except Exception:
            pass
        if errors:
            self._log(tr("Submit failed: {e}", self._language, e="; ".join(errors)))
            return
        if batch_id:
            self._log(tr("Submitted: {batch_id}", self._language, batch_id=batch_id))
        else:
            self._log(tr("Submitted.", self._language))

    def push_sources(self, sources: list[InputSource]) -> None:
        """Wire endpoint for the cross-page right-click menu."""
        self.input_panel.set_sources(list(sources))
        self.use_as_input_received.emit(list(sources))
        self._log(tr("Pushed {n} source(s) from Files page.", self._language, n=len(sources)))

    def set_max_parallel(self, value: int) -> None:
        self.max_parallel_spin.setValue(int(value))

    def set_server_id(self, server_id: str) -> None:
        self._server_label = server_id
        self.server_pill.setText(self._server_pill_text())

    def set_remote_dir(self, remote_dir: str) -> None:
        """Inherit the Files page's current remote directory.

        Review-fix: Submit used to hardcode ``remote_dir="/"`` which
        broke for users without root write permission. The MainWindow
        pushes the Files page's current ``remote_path`` here whenever
        the user navigates to the Submit tab so the submitted run
        lands in the same folder the user just browsed.
        """
        if remote_dir:
            self._remote_dir = remote_dir
        self._refresh_remote_target_label()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _server_pill_text(self) -> str:
        if not self._server_label:
            return tr("No server", self._language)
        return f"{tr('Server', self._language)}: {self._server_label}"

    def _refresh_remote_target_label(self) -> None:
        """Show the inherited remote target so users can see where the
        workflow will be uploaded.

        Review-fix: keeps the user from being surprised when the Files
        page was on ``/home/me/scratch`` and the Submit payload still
        inherited ``"/"``. The label is plain text and intentionally
        short to stay in one line with the server pill.
        """
        target = self._remote_dir or "/"
        self.remote_target_label.setText(
            tr("\u2192 {target}", self._language, target=target)
        )

    def _refresh_server_hint(self) -> None:
        """Phase 1.2: show a hint next to the server pill when inputs are
        added but no server is currently selected.

        Visibility rule: hint is visible iff the user has at least one
        input source AND ``_server_label`` is empty. The submit button
        stays enabled — the existing MainWindow path produces a real
        error dialog if the user presses Submit anyway.
        """
        has_sources = bool(self.input_panel.sources())
        has_server = bool(self._server_label)
        self.server_hint.setVisible(has_sources and not has_server)

    def load_recent_activity(self, limit: int = _ACTIVITY_LIMIT) -> None:
        """Repopulate the activity list from the repository on startup."""
        repo = self._activity_repo
        if repo is None:
            return
        try:
            for entry in repo.list_recent_activity(limit=limit):
                self.activity_list.addItem(QListWidgetItem(entry["message"]))
        except Exception:
            pass

    def _log(self, message: str) -> None:
        if self._activity_repo is not None:
            try:
                self._activity_repo.append_activity(level="info", message=message)
            except Exception:
                pass
        self.activity_list.addItem(QListWidgetItem(message))
        self._on_status(message)

    def _on_sources_changed(self, _sources: list[InputSource]) -> None:
        # Inputs may not be required for assembling the graph first; we
        # do not gate the buttons on it here. The use case surfaces the
        # missing-input error on submit.
        self._refresh_server_hint()
        return None

    def _on_add_files_requested(self, side: str, _default_dir: str) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            tr("Select input files", self._language),
            "",
            "Input files (*.xyz *.gjf *.inp);;XYZ files (*.xyz);;GJF files (*.gjf);;INP files (*.inp);;All files (*)",
        )
        if side == "remote":
            self.input_panel.add_remote_paths(files)
        else:
            self.input_panel.add_local_paths(files)

    def _on_graph_changed(self) -> None:
        # Keep button enablement in lockstep with the status pill, and
        # debounce the YAML render so dragging a node doesn't thrash.
        self._refresh_validation()
        self._preview_timer.start()

    def _on_generate_clicked(self) -> None:
        self._preview_timer.stop()
        # Review-fix: empty canvas surfaces a friendly hint in the
        # activity log instead of a yellow "Graph incomplete" banner
        # that contradicts the green pill. Same friendly short-circuit
        # for Submit.
        if self.editor.is_empty():
            self._log(
                tr(
                    "Add a node from the library to start your workflow.",
                    self._language,
                )
            )
            return
        self.editor.validate()
        self._refresh_preview()

    def _on_submit_clicked(self) -> None:
        # Review-fix: handle the empty-canvas case explicitly so the
        # user gets a clear "Add a node first" message instead of the
        # confusing "No inputs selected" path.
        if self.editor.is_empty():
            self._log(
                tr(
                    "Add a node from the library to start your workflow.",
                    self._language,
                )
            )
            self._banner.show_warning(
                tr(
                    "Add a node from the library to start your workflow.",
                    self._language,
                )
            )
            return
        issues = self.editor.validate()
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            for issue in errors:
                self._log(
                    tr("Validation [{code}]: {message}", self._language,
                       code=issue.code or "graph", message=issue.message)
                )
            # Phase 3.1: surface the first validation error in the inline
            # banner so the user sees it without scrolling the activity log.
            first = errors[0]
            self._banner.show_error(
                tr("Validation [{code}]: {message}", self._language,
                   code=first.code or "graph", message=first.message)
            )
            return
        self._banner.dismiss()
        payload = self._build_payload("confflow")
        if payload is None:
            return
        self.submit_requested.emit(payload)

    def _refresh_preview(self) -> None:
        """Render the current graph to YAML into the preview pane.

        Review-fix: an empty canvas used to raise ``WorkflowSpecError
        ("graph is empty; ...")`` which got surfaced as a yellow
        "Graph incomplete" banner. The status pill simultaneously
        flashed green "Workflow OK" because the topology validation
        passed on zero nodes. The result was a contradictory
        neutral/green/yellow state at startup. We now check
        ``editor.is_empty()`` first and render a single, neutral
        "Add a node to start your workflow" message that pairs with
        the editor's "Empty canvas" pill.
        """
        try:
            if self.editor.is_empty():
                self.preview.setPlainText(
                    tr(
                        "Add a node from the library to start your workflow.",
                        self._language,
                    )
                )
                self._banner.dismiss()
                return
            graph = self.editor.graph()
            payload: WorkflowGraphPayload = to_workflow_spec(graph)
        except WorkflowSpecError as exc:
            self.preview.setPlainText(tr("Graph incomplete: {exc}", self._language, exc=exc))
            self._banner.show_warning(
                tr("Graph incomplete: {exc}", self._language, exc=exc)
            )
            return
        except Exception as exc:
            self.preview.setPlainText(tr("Preview failed: {exc}", self._language, exc=exc))
            self._banner.show_error(
                tr("Preview failed: {exc}", self._language, exc=exc)
            )
            return
        try:
            yaml_text = payload.to_yaml()
        except Exception as exc:
            self.preview.setPlainText(tr("Render failed: {exc}", self._language, exc=exc))
            self._banner.show_error(
                tr("Render failed: {exc}", self._language, exc=exc)
            )
            return
        self.preview.setPlainText(yaml_text)
        # Successful preview — dismiss any previous warning/error banner.
        self._banner.dismiss()

    def _refresh_validation(self) -> None:
        """Toggle the buttons based on whether the graph has any errors.

        Review-fix: an empty canvas is a neutral state — neither OK
        nor a validation error. We keep both buttons enabled so the
        user can still press Submit / Generate from the empty canvas
        and receive a friendly "Add a node first" hint in the
        activity log; previously the buttons were enabled but the
        message said "No inputs selected", which contradicted both
        the green OK pill and the Graph incomplete banner.
        """
        if self.editor.is_empty():
            # Leave both buttons enabled; we let _on_submit_clicked /
            # _on_generate_clicked surface the empty-canvas hint so
            # clicking feels responsive rather than mysteriously
            # silent.
            self.generate_btn.setEnabled(True)
            self.submit_btn.setEnabled(True)
            return
        issues = self.editor.validate()
        has_errors = any(i.severity == "error" for i in issues)
        self.generate_btn.setEnabled(not has_errors)
        self.submit_btn.setEnabled(not has_errors)

    def _build_payload(self, kind: SubmitKind) -> SubmitPayload | None:
        """Assemble a :class:`SubmitPayload` from the current graph state.

        Returns ``None`` and logs an entry if there are no inputs — the
        use case would otherwise reject the payload later.
        """
        sources = self.input_panel.sources()
        if not sources:
            self._log(tr("No inputs selected.", self._language))
            return None

        first = sources[0].path
        output_dir = first.parent if first.is_absolute() else Path(".")
        work_dir_name = _work_dir_name(first)

        try:
            graph = self.editor.graph()
            payload = to_workflow_spec(graph)
        except WorkflowSpecError as exc:
            self._log(tr("Validation [graph]: {exc}", self._language, exc=exc))
            return None
        except Exception as exc:
            self._log(tr("Validation [graph]: {exc}", self._language, exc=exc))
            return None

        calc_cfg = payload.spec.global_config.calc
        program = str(getattr(calc_cfg, "program", "orca"))
        calc = _calc_fields_from_cfg(calc_cfg, program)

        # Phase 10.5: auto-detect DAG vs linear. Any step with a non-empty
        # ``inputs`` list (Phase 10.1-10.4 wiring) means the graph declares
        # a multi-input edge, which the legacy linear ``confflow`` path
        # can't model. ``kind`` is overridden locally; the caller's ``kind``
        # argument is used as a fallback so tests can force a path.
        detected_kind: SubmitKind = _detect_payload_kind(payload.steps) or kind

        if detected_kind == "dag":
            dag = _DagWorkflowFieldsShim(
                work_dir_name=work_dir_name,
                steps=list(payload.steps),
                advanced_options={},
            )
            return SubmitPayload(
                kind="dag",
                inputs=sources,
                program=program,
                calc=calc,
                workflow=None,
                dag=dag,
                output_dir=output_dir,
                output_paths=[],
                server_id=self._server_label or "",
                # Review-fix: Submit used to hardcode remote_dir="/" which
                # broke submissions to non-writable roots. The Files page
                # pushes its current remote directory into us via
                # ``set_remote_dir``; fall back to "/" only when nothing
                # has been pushed yet (legacy / unconfigured path).
                remote_dir=self._remote_dir or "/",
                max_parallel=self.max_parallel_spin.value(),
            )

        workflow = WorkflowFields(
            work_dir_name=work_dir_name,
            steps=[_step_type_token(s) for s in payload.steps],
            advanced_options={},
        )
        return SubmitPayload(
            kind=kind,
            inputs=sources,
            program=program,
            calc=calc,
            workflow=workflow,
            output_dir=output_dir,
            output_paths=[],
            server_id=self._server_label or "",
            remote_dir=self._remote_dir or "/",
            max_parallel=self.max_parallel_spin.value(),
        )


def _work_dir_name(first_path: Path) -> str:
    """Derive the workflow's ``work_dir`` from the first selected input.

    Mirrors the legacy wizard convention: ``<basename>_confflow_work``
    when the first input has a non-empty stem, falling back to
    ``dir_confflow_work`` for unnamed paths and ``"."`` for relative
    inputs. Centralised here so the editor and submit code stay in
    sync.
    """
    stem = first_path.stem.strip()
    if stem:
        return f"{stem}_confflow_work"
    parent_name = first_path.parent.name.strip() if first_path.is_absolute() else ""
    if parent_name and parent_name not in {".", "/"}:
        return f"{parent_name}_confflow_work"
    return "confflow_work"


def _step_type_token(step: dict[str, Any]) -> str:
    """Return the wizard's step-type token for a bridge-emitted step.

    Mirrors :func:`jobdesk_app.gui.nodegraph.spec_bridge._step_type_token`
    but kept local so the rest of Phase 2 doesn't grow a new public import.
    """
    step_type = step.get("type")
    if step_type == "confgen":
        return "confgen"
    itask = step.get("params", {}).get("itask")
    return f"calc:{itask}" if itask else "calc"


def _calc_fields_from_cfg(calc_cfg: Any, program: str) -> "_CalculationFieldsShim":
    """Build a :class:`CalculationFields`-shaped object from the graph-derived config.

    :class:`SubmitUseCase` reads ``method_basis``, ``charge``,
    ``multiplicity``, ``nproc`` and ``mem`` off this object, so the shim
    satisfies its duck-typed access without dragging a calc UI into the
    submit page's runtime path.
    """
    try:
        method_basis = " ".join(
            part for part in (getattr(calc_cfg, "method", "") or "",
                              getattr(calc_cfg, "basis", "") or "")
            if part
        )
    except Exception:
        method_basis = ""
    try:
        nproc = int(getattr(calc_cfg, "nproc", 1) or 1)
    except Exception:
        nproc = 1
    try:
        mem_mb = int(getattr(calc_cfg, "memory_mb", 1024) or 1024)
    except Exception:
        mem_mb = 1024
    return _CalculationFieldsShim(
        program=program,  # type: ignore[arg-type]
        preset_name=None,
        method_basis=method_basis,
        job_keywords=[],
        charge=int(getattr(calc_cfg, "charge", 0) or 0),
        multiplicity=int(getattr(calc_cfg, "multiplicity", 1) or 1),
        nproc=nproc,
        mem=f"{mem_mb}MB",
    )


@dataclass
class _CalculationFieldsShim:
    """Plain value object that satisfies :class:`SubmitUseCase`'s duck-typed access.

    Mirrors the attribute surface :class:`SubmitUseCase` reads
    (``method_basis``, ``charge``, ``multiplicity``, ``nproc``, ``mem``,
    plus ``program`` / ``preset_name`` / ``job_keywords`` for completeness).
    Keeps the submit page free of any Qt-widget dep in its data path.
    """

    program: str
    preset_name: str | None
    method_basis: str
    job_keywords: list[str]
    charge: int
    multiplicity: int
    nproc: int
    mem: str


@dataclass
class _DagWorkflowFieldsShim(DagWorkflowFields):
    """Thin wrapper around :class:`DagWorkflowFields` for the Phase 10.5 page.

    Kept separate from ``_CalculationFieldsShim`` so the page's two
    build-paths (linear / DAG) read top-to-bottom without conditionals
    scattered around. Inherits from :class:`DagWorkflowFields` so the
    use case's duck-typed access (``work_dir_name`` / ``steps`` /
    ``advanced_options``) keeps working unchanged.
    """

    pass


def _detect_payload_kind(steps: list[dict[str, Any]]) -> SubmitKind | None:
    """Return ``"dag"`` when any step declares non-empty ``inputs``.

    Phase 10.5 rule: a graph whose per-step ``inputs`` arrays are all
    empty is a linear workflow (Phase 1.6 / 14B style) and is submitted
    as ``kind="confflow"`` for backward compatibility.  Any step that
    names an upstream predecessor is a DAG fan-in and forces
    ``kind="dag"`` so the submit path writes ``StepConfig.inputs`` to
    the YAML.
    """
    for step in steps:
        inputs = step.get("inputs") or []
        if inputs:
            return "dag"
    return None


__all__ = ["SubmitPage"]
