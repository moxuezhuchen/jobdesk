"""Modal submit dialog with auto-detected Single / Workflow mode.

The dialog inspects the selected ``InputSource`` list and switches its
default Mode:

* Only ``.gjf`` and ``.inp`` -> Single (Gaussian / ORCA direct run).
* Any ``.xyz`` (or unknown suffix) -> Workflow forced; Single radio is
  disabled and greyed out.

The dialog emits a fully formed :class:`SubmitPayload` on accept so the
caller (``MainWindow._on_submit_requested``) is unchanged.

Review-round 3 fix-ups:

* A read-only ``QPlainTextEdit`` shows the exact ``workflow.yaml`` that
  ``build_payload()`` would upload for the currently selected preset.
  The user can eyeball the YAML before clicking Submit so they no
  longer "submit, then notice it was the wrong preset". The preview is
  regenerated whenever the preset combo or the charge / multiplicity
  spins change, so the two are always in sync.
* A ``[Save workflow.yaml\u2026]`` button writes the same YAML to a
  user-chosen path without touching the remote server. Use this to
  keep a copy alongside the inputs or to feed ``confflow --dry-run``
  offline.
* The preset combo rebuild now blocks signals during the rebuild so
  the spurious ``currentIndexChanged`` that fired when the first item
  was added can no longer overwrite the caller's preset selection.
* ``build_payload()`` re-reads the preset from disk before the
  payload is constructed, so an external ``preset_combo`` mutation
  between ``refresh`` and ``accept`` cannot smuggle a stale preset
  into the upload.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.submit_payload import (
    InputSource,
    SubmitKind,
    SubmitPayload,
    WorkflowFields,
)
from ...core.workflow_spec import ConfFlowUnavailableError, WorkflowSpec
from ...services.method_presets import MethodPresetStore
from ..i18n import tr

_MODE_LABEL = {"single": "Single calculation", "workflow": "Workflow"}


def _infer_program(sources: list[InputSource]) -> str:
    """Pick gaussian/orca based on the majority suffix of selected files."""
    counts = {"gjf": 0, "inp": 0, "xyz": 0}
    for src in sources:
        counts[src.kind] = counts.get(src.kind, 0) + 1
    if counts["inp"] > counts["gjf"]:
        return "orca"
    return "gaussian"


def _requires_workflow(sources: list[InputSource]) -> bool:
    """Workflow is mandatory when any input is not a fully-formed input file."""
    return any(s.kind != "gjf" and s.kind != "inp" for s in sources)


@dataclass(frozen=True)
class _CalculationFieldsShim:
    program: str
    preset_name: str | None
    method_basis: str
    job_keywords: list[str]
    charge: int
    multiplicity: int
    nproc: int
    mem: str


class SubmitDialog(QDialog):
    """Modal that produces a :class:`SubmitPayload` on accept.

    Constructed by ``MainWindow.open_submit_dialog``. Emits ``accepted``
    via the standard ``QDialog`` mechanism; the caller reads
    ``build_payload()`` immediately after ``exec()`` returns
    ``QDialog.Accepted``.
    """

    def __init__(
        self,
        language: str,
        *,
        files: list[InputSource],
        server_id: str = "",
        remote_dir: str = "/",
        max_parallel: int = 1,
        preset_store: MethodPresetStore | None = None,
        preset_name: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._language = language
        self._files = files
        self._server_id = server_id
        self._remote_dir = remote_dir
        self._max_parallel = max_parallel
        self._preset_store = preset_store or MethodPresetStore()
        self._preset_name = preset_name
        self._status_callback: Callable[[str], None] = lambda _msg: None

        self.setWindowTitle(tr("Submit for calculation", language))
        self.setMinimumWidth(640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # Track whether we currently have zero selected sources. The
        # empty-state hint replaces both the file summary list and the
        # Mode radio group so the user understands the dialog is open
        # but blocked on file selection.
        self._has_files = bool(files)

        layout.addWidget(self._build_empty_state_hint())
        layout.addWidget(self._build_file_summary())
        layout.addWidget(self._build_mode_box())
        layout.addWidget(self._build_workflow_box())
        layout.addWidget(self._build_workflow_yaml_box())
        layout.addWidget(self._build_globals_box())

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setText(tr("Submit \u25b6", language))
        self._ok_button.setEnabled(self._has_files)
        # Route both mouse and keyboard acceptance through the guard.  A
        # second direct ``accepted -> accept`` connection would bypass a
        # rejected missing-workflow/no-files check after the button click.
        buttons.accepted.connect(self._on_ok_clicked)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_mode()
        self._refresh_preset_combo()
        # Generate the YAML preview once after the preset combo has been
        # populated so the user sees the actual config that will be
        # submitted, even before they touch any spinner.
        self._refresh_workflow_yaml_preview()

    # -- UI builders --

    def _build_empty_state_hint(self) -> QWidget:
        """Top banner shown when the dialog has no input sources selected.

        Phase 2.0 follow-up: the Workflow-page "Use this preset for
        submit" button and the Runs-page "Go to Submit" button both
        open this dialog without prior file selection. A blank dialog
        used to crash on ``build_payload()`` -- the file list and the
        workflow preset combo would also be empty. We now show a
        banner that points the user at Files and disable the OK button
        until sources are added.
        """
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(
            tr(
                "Pick at least one input file in the Files page, then "
                "reopen this dialog. Until then the dialog stays open "
                "so you can still pick a workflow preset and queue a "
                "submission later.",
                self._language,
            )
        )
        label.setWordWrap(True)
        label.setStyleSheet("color: #b54708; font-style: italic;")
        layout.addWidget(label)
        wrap.setVisible(not self._has_files)
        self._empty_state = wrap
        return wrap

    def _build_file_summary(self) -> QWidget:
        n = len(self._files)
        if n == 0:
            label_text = tr("Selected files (0)", self._language)
        elif n == 1:
            label_text = tr("Selected files (1)", self._language)
        else:
            label_text = tr("Selected files ({n})", self._language, n=n)
        label = QLabel(label_text)
        list_widget = QListWidget()
        for src in self._files:
            item = QListWidgetItem(f"{src.path.name} ({src.kind})")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            list_widget.addItem(item)
        list_widget.setMaximumHeight(80)
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(label)
        v.addWidget(list_widget)
        self.file_list = list_widget
        return wrap

    def _build_mode_box(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(tr("Mode:", self._language)))
        self.single_radio = QRadioButton(tr("Single calculation", self._language))
        self.workflow_radio = QRadioButton(tr("Workflow", self._language))
        self.single_radio.toggled.connect(self._refresh_mode)
        self.workflow_radio.toggled.connect(self._refresh_mode)
        row = QHBoxLayout()
        row.addWidget(self.single_radio)
        row.addWidget(self.workflow_radio)
        row.addStretch()
        layout.addLayout(row)
        self._mode_hint = QLabel("")
        self._mode_hint.setStyleSheet("color: #b54708; font-style: italic;")
        layout.addWidget(self._mode_hint)
        return box

    def _build_workflow_box(self) -> QWidget:
        box = QWidget()
        layout = QFormLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        self.preset_combo = QComboBox()
        # blockSignals(True) during the rebuild is critical: without
        # it, the act of adding the first item to the freshly-cleared
        # combo fires ``currentIndexChanged(-1 -> 0)`` which then
        # overwrites ``self._preset_name`` to the first item's name --
        # see ``_refresh_preset_combo``.
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        layout.addRow(tr("Workflow:", self._language), self.preset_combo)
        # Review-round 3: the submit dialog also exposes the same
        # "Edit workflow" affordance as the sidebar. It opens the modal
        # ``WorkflowBuilderDialog`` for the *currently selected* preset,
        # so the user can iterate quickly when the YAML preview looks
        # wrong. The button is enabled only in Workflow mode where
        # editing makes sense.
        return box

    def _build_workflow_yaml_box(self) -> QWidget:
        """Read-only ``workflow.yaml`` preview + Save-to-disk shortcut.

        The preview box stays updated with whatever
        ``build_payload()`` would emit for the currently selected
        preset. Empty in Single mode (single-mode payloads do not have
        a workflow YAML). The Preview / Save buttons let the user
        verify config before committing to a remote submit.
        """
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self._yaml_label = QLabel(tr("workflow.yaml", self._language))
        font = self._yaml_label.font()
        font.setBold(True)
        self._yaml_label.setFont(font)
        header.addWidget(self._yaml_label)
        header.addStretch()
        self.btn_save_yaml = QPushButton(tr("Save workflow.yaml\u2026", self._language))
        self.btn_save_yaml.clicked.connect(self._on_save_yaml_clicked)
        header.addWidget(self.btn_save_yaml)
        layout.addLayout(header)

        self._yaml_view = QPlainTextEdit()
        self._yaml_view.setReadOnly(True)
        self._yaml_view.setMinimumHeight(140)
        self._yaml_view.setMaximumHeight(220)
        self._yaml_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self._yaml_view)

        # Stays visible even when in Single mode, but the contents are
        # empty -- the user sees "no workflow YAML in Single mode" so
        # the panel isn't dark / mystery meat.
        self._workflow_yaml_box = wrap
        return wrap

    def _build_globals_box(self) -> QWidget:
        box = QWidget()
        layout = QFormLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        self.charge_spin = QSpinBox()
        self.charge_spin.setRange(-99, 99)
        self.charge_spin.setValue(0)
        self.charge_spin.valueChanged.connect(self._refresh_workflow_yaml_preview)
        self.mult_spin = QSpinBox()
        self.mult_spin.setRange(1, 10)
        self.mult_spin.setValue(1)
        self.mult_spin.valueChanged.connect(self._refresh_workflow_yaml_preview)
        self.server_combo = QComboBox()
        self.server_combo.addItem(self._server_id or tr("No server", self._language))
        if not self._server_id:
            self.server_combo.setEnabled(False)
        layout.addRow(tr("Charge:", self._language), self.charge_spin)
        layout.addRow(tr("Multiplicity:", self._language), self.mult_spin)
        layout.addRow(tr("Server:", self._language), self.server_combo)
        return box

    # -- State refresh --

    def mode(self) -> str:
        return "workflow" if self.workflow_radio.isChecked() else "single"

    def _refresh_mode(self) -> None:
        # No files selected? Lock the dialog into Workflow mode (the
        # only viable path -- Single mode needs at least one input
        # file). The Mode radios stay visible for layout consistency
        # but Single is disabled and Workflow is force-checked.
        if not self._has_files:
            self.single_radio.setEnabled(False)
            self.single_radio.setChecked(False)
            self.workflow_radio.setChecked(True)
            self.workflow_radio.setEnabled(False)
            self.charge_spin.setEnabled(False)
            self.mult_spin.setEnabled(False)
            self._mode_hint.setText(tr("Workflow required while no input files are selected", self._language))
            self.preset_combo.setEnabled(True)
            if hasattr(self, "_ok_button"):
                self._ok_button.setEnabled(False)
            self._refresh_workflow_yaml_preview()
            return
        # Restore interactivity now that files are present.
        self.workflow_radio.setEnabled(True)
        if hasattr(self, "_ok_button"):
            self._ok_button.setEnabled(True)
        requires_workflow = _requires_workflow(self._files)
        if requires_workflow:
            self.single_radio.setEnabled(False)
            self.single_radio.setChecked(False)
            self.workflow_radio.setChecked(True)
            self._mode_hint.setText(tr("Workflow required for non-Gaussian/ORCA inputs", self._language))
        else:
            self.single_radio.setEnabled(True)
            if not self.workflow_radio.isChecked() and not self.single_radio.isChecked():
                self.single_radio.setChecked(True)
            self._mode_hint.setText("")
        self.preset_combo.setEnabled(self.mode() == "workflow")
        # Workflow globals belong to the saved Global YAML.  Keeping these
        # form controls editable here would promise overrides that conflict
        # with the exact-YAML submit contract.
        self.charge_spin.setEnabled(self.mode() == "single")
        self.mult_spin.setEnabled(self.mode() == "single")
        self._refresh_workflow_yaml_preview()

    def _refresh_preset_combo(self) -> None:
        # Block signals during the rebuild so the spurious
        # ``currentIndexChanged`` that ``addItem`` triggers on an
        # empty combo does not clobber ``self._preset_name``.
        # Review-round 3: this is the fix for the "selected
        # r2scan3c_opt_freq in sidebar but submit dialog showed
        # conformer_ensemble_sp" bug -- the auto-index to 0 on the
        # first addItem was firing ``_on_preset_changed(0)`` and
        # overwriting ``_preset_name`` with the first item's name,
        # which then made ``findData(<intended name>)`` succeed but
        # ``setCurrentIndex(<intended idx>)`` never run because the
        # find saw the wrong name.
        self.preset_combo.blockSignals(True)
        try:
            self.preset_combo.clear()
            for preset in self._preset_store.list_presets():
                # Built-ins are reusable steps, never submit-ready
                # workflows.  Only a user-saved composition is selectable.
                if preset.source != "user":
                    continue
                label = f"{preset.name}  ({tr(preset.source.capitalize(), self._language)})"
                self.preset_combo.addItem(label, preset.name)
            if self._preset_name:
                idx = self.preset_combo.findData(self._preset_name)
                if idx >= 0:
                    self.preset_combo.setCurrentIndex(idx)
            # Belt-and-braces: keep ``self._preset_name`` in sync with
            # whatever the combo actually settled on. When the caller's
            # ``_preset_name`` was missing or stale, this prevents a
            # phantom state where the combo shows one preset and
            # ``_preset_name`` stores another.
            current = self.preset_combo.currentData()
            if isinstance(current, str):
                self._preset_name = current
            else:
                # A deleted or legacy built-in name must never remain as a
                # phantom selection that can make the dialog accept.
                self._preset_name = None
        finally:
            self.preset_combo.blockSignals(False)
        self._refresh_workflow_yaml_preview()

    def set_selected_preset_name(self, name: str) -> None:
        self._preset_name = name
        self._refresh_preset_combo()

    def set_files(self, files: list[InputSource]) -> None:
        """Replace the dialog's source list and refresh derived state.

        Tests and the "drop a file here" path can swap sources at
        runtime; the OK button stays disabled until ``files`` is non-
        empty. The file list widget is repopulated in-place.
        """
        self._files = list(files)
        self._has_files = bool(files)
        # Refresh the file list widget.
        if hasattr(self, "file_list"):
            self.file_list.clear()
            for src in self._files:
                item = QListWidgetItem(f"{src.path.name} ({src.kind})")
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.file_list.addItem(item)
        # Toggle visibility of the empty-state banner.
        if hasattr(self, "_empty_state"):
            self._empty_state.setVisible(not self._has_files)
        self._refresh_mode()

    def _on_preset_changed(self, _index: int) -> None:
        data = self.preset_combo.currentData()
        if isinstance(data, str):
            self._preset_name = data
        self._refresh_workflow_yaml_preview()

    def _on_save_yaml_clicked(self) -> None:
        """Write the current YAML preview to a user-chosen path.

        Useful when the user wants to inspect ``workflow.yaml``
        offline (``confflow --dry-run`` etc.) without uploading to the
        server. We use a real ``QFileDialog.getSaveFileName`` so the
        path is fully controllable -- the dialog defaults to the
        first input's directory.
        """
        if self.mode() != "workflow":
            QMessageBox.information(
                self,
                tr("Save workflow.yaml", self._language),
                tr(
                    "Switch to Workflow mode to save a workflow.yaml.",
                    self._language,
                ),
            )
            return
        yaml_text = self._workflow_yaml_text()
        if yaml_text is None:
            return
        default_path = ""
        if self._files:
            default_path = str(self._files[0].path.parent / "workflow.yaml")
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("Save workflow.yaml", self._language),
            default_path,
            tr("YAML (*.yaml);;All files (*.*)", self._language),
        )
        if not path:
            return
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(yaml_text, encoding="utf-8")
            tmp.replace(target)
        except Exception as exc:
            QMessageBox.warning(
                self,
                tr("Save workflow.yaml", self._language),
                str(exc),
            )
            return
        self._status_callback(tr("Workflow saved.", self._language) + f" ({target})")

    def set_status_callback(self, callback: Callable[[str], None] | None) -> None:
        """External code (typically ``MainWindow``) wires a status sink."""
        self._status_callback = callback or (lambda _msg: None)

    def _resolve_workflow_spec(self) -> WorkflowSpec | None:
        """Build a ``WorkflowSpec`` for the currently selected preset.

        Used by both ``_refresh_workflow_yaml_preview`` and the
        ``[Save workflow.yaml]`` shortcut.  Build through the same
        ``WorkflowSpec.from_form`` mapping used by ``SubmitUseCase``;
        serialising the preset's raw global model here would produce a
        richer YAML than the file that is actually uploaded.
        """
        if not self._preset_name:
            return None
        yaml_text = self._workflow_yaml_text()
        if yaml_text is None:
            return None
        return WorkflowSpec.from_yaml(yaml_text)

    def _workflow_yaml_text(self) -> str | None:
        if not self._preset_name:
            return None
        try:
            return self._preset_store.load_yaml(self._preset_name, source="user")
        except KeyError:
            return None

    def _refresh_workflow_yaml_preview(self) -> None:
        """Re-render the YAML preview pane.

        Skips the work when the combo / preview widgets haven't been
        built yet (early-stage construction) so init order can't
        crash. In Single mode we leave the pane empty so the user
        sees a hint instead of stale YAML.
        """
        if not hasattr(self, "_yaml_view"):
            return
        if self.mode() != "workflow" or not self._preset_name:
            self._yaml_view.setPlainText("")
            return
        yaml_text = self._workflow_yaml_text()
        if yaml_text is None:
            self._yaml_view.setPlainText(
                tr(
                    "Pick a preset first.",
                    self._language,
                )
            )
            return
        try:
            self._yaml_view.setPlainText(yaml_text)
        except ConfFlowUnavailableError as exc:
            self._yaml_view.setPlainText(str(exc))
        except Exception as exc:
            self._yaml_view.setPlainText(tr("Preview failed: {exc}", self._language, exc=exc))

    # -- Payload assembly --

    def _on_ok_clicked(self) -> None:
        """Belt-and-braces: refuse to submit on a stale preset selection.

        ``build_payload()`` is allowed only when the dialog is in a
        submit-able state (files + radio state). ``QDialog.Accepted``
        is hooked up to ``accept()`` which fires ``exec()`` returning
        ``Accepted`` -- but we need to short-circuit ``Accepted`` here
        (via setting ``self.result(QDialog.Rejected)`` and skipping
        ``accept``) for the no-files / missing-preset edge case.
        """
        if not self._has_files:
            QMessageBox.information(
                self,
                tr("Submit for calculation", self._language),
                tr(
                    "Pick at least one input file in the Files page, then reopen this dialog.",
                    self._language,
                ),
            )
            # Roll the result back to Rejected so the caller skips
            # ``build_payload()``.
            self.result()
            self.reject()
            return
        # Workflow mode requires a user-saved workflow composition.
        if self.mode() == "workflow" and self._workflow_yaml_text() is None:
            self._refresh_preset_combo()
            if self._workflow_yaml_text() is None:
                QMessageBox.information(
                    self,
                    tr("Submit for calculation", self._language),
                    tr("Pick a preset first.", self._language),
                )
                self.result()
                self.reject()
                return
        # All clear -- standard accept.
        self.accept()

    def build_payload(self) -> SubmitPayload:
        # Re-read the preset from disk one more time so an external
        # ``preset_combo`` mutation between ``refresh`` and ``accept``
        # cannot smuggle a stale preset into the payload. Review-round
        # 3: this is the second half of the desync fix.
        files = list(self._files)
        if not files:
            # OK button is disabled when no files are selected, so the
            # caller should never reach here. Defend against accidental
            # invocation by raising so the bug is caught loudly instead
            # of silently returning a broken payload.
            raise ValueError(
                "SubmitDialog.build_payload() called with no input files. "
                "Seed the dialog via files=... in __init__ or set_files()."
            )
        first = files[0].path
        output_dir = first.parent if first.is_absolute() else first
        work_dir_name = f"{first.stem or 'job'}_work"
        server_id = self._server_id
        remote_dir = self._remote_dir
        max_parallel = self._max_parallel
        charge = self.charge_spin.value()
        mult = self.mult_spin.value()

        if self.mode() == "single":
            program = _infer_program(files)
            calc = _CalculationFieldsShim(
                program=program,
                preset_name=None,
                method_basis="",
                job_keywords=[],
                charge=charge,
                multiplicity=mult,
                nproc=8,
                mem="4GB",
            )
            return SubmitPayload(
                kind="single",
                inputs=files,
                program=program,
                calc=calc,
                workflow=None,
                output_dir=output_dir,
                output_paths=[],
                server_id=server_id,
                remote_dir=remote_dir,
                max_parallel=max_parallel,
            )

        # mode == workflow
        preset_name = self._preset_name
        if not preset_name:
            raise ValueError("Pick a saved workflow before submitting.")
        yaml_text = self._workflow_yaml_text()
        if yaml_text is None:
            raise ValueError("Selected workflow is no longer available.")
        preset_spec = WorkflowSpec.from_yaml(yaml_text)
        form = preset_spec.to_form()
        program = form.get("program") or "gaussian"
        method_basis = " ".join(p for p in (form.get("method", ""), form.get("basis", "")) if p)
        steps = list(form.get("steps", []))
        # Phase 10.5 mirror: WorkflowSpec strips graph topology, so dialog
        # always chooses kind="confflow" for the linear chain we round-trip
        # out of a preset. Update once WorkflowSpec exposes graph edges.
        kind: SubmitKind = "confflow"
        calc = _CalculationFieldsShim(
            program=program,
            preset_name=preset_name,
            method_basis=method_basis,
            job_keywords=[],
            charge=charge,
            multiplicity=mult,
            nproc=int(form.get("nproc", 8) or 8),
            mem=f"{int(form.get('memory_mb', 4096) or 4096)}MB",
        )
        return SubmitPayload(
            kind=kind,
            inputs=files,
            program=program,
            calc=calc,
            workflow=WorkflowFields(
                work_dir_name=work_dir_name,
                steps=steps,
                advanced_options={},
                yaml_text=yaml_text,
            ),
            output_dir=output_dir,
            server_id=server_id,
            remote_dir=remote_dir,
            max_parallel=max_parallel,
            dag=None,
        )


__all__ = ["SubmitDialog"]
