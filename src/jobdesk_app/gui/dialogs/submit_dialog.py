"""Modal submit dialog with auto-detected Single / Workflow mode.

The dialog inspects the selected ``InputSource`` list and switches its
default Mode:

* Only ``.gjf`` and ``.inp`` -> Single (Gaussian / ORCA direct run).
* Any ``.xyz`` (or unknown suffix) -> Workflow forced; Single radio is
  disabled and greyed out.

The dialog emits a fully formed :class:`SubmitPayload` on accept so the
caller (``MainWindow._on_submit_requested``) is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
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

        self.setWindowTitle(tr("Submit for calculation", language))
        self.setMinimumWidth(540)

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
        layout.addWidget(self._build_globals_box())

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setText(tr("Submit \u25b6", language))
        self._ok_button.setEnabled(self._has_files)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_mode()
        self._refresh_preset_combo()

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
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        layout.addRow(tr("Workflow:", self._language), self.preset_combo)
        return box

    def _build_globals_box(self) -> QWidget:
        box = QWidget()
        layout = QFormLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        self.charge_spin = QSpinBox()
        self.charge_spin.setRange(-99, 99)
        self.charge_spin.setValue(0)
        self.mult_spin = QSpinBox()
        self.mult_spin.setRange(1, 10)
        self.mult_spin.setValue(1)
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
            self._mode_hint.setText(
                tr("Workflow required while no input files are selected", self._language)
            )
            self.preset_combo.setEnabled(True)
            if hasattr(self, "_ok_button"):
                self._ok_button.setEnabled(False)
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
            self._mode_hint.setText(
                tr("Workflow required for non-Gaussian/ORCA inputs", self._language)
            )
        else:
            self.single_radio.setEnabled(True)
            if not self.workflow_radio.isChecked() and not self.single_radio.isChecked():
                self.single_radio.setChecked(True)
            self._mode_hint.setText("")
        self.preset_combo.setEnabled(self.mode() == "workflow")

    def _refresh_preset_combo(self) -> None:
        self.preset_combo.clear()
        for preset in self._preset_store.list_presets():
            label = f"{preset.name}  ({tr(preset.source.capitalize(), self._language)})"
            self.preset_combo.addItem(label, preset.name)
        if self._preset_name:
            idx = self.preset_combo.findData(self._preset_name)
            if idx >= 0:
                self.preset_combo.setCurrentIndex(idx)

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

    # -- Payload assembly --

    def build_payload(self) -> SubmitPayload:
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
            preset_name = ""
            for p in self._preset_store.list_presets():
                preset_name = p.name
                break
        preset_spec = self._preset_store.load(preset_name, source="user")
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
            ),
            output_dir=output_dir,
            server_id=server_id,
            remote_dir=remote_dir,
            max_parallel=max_parallel,
            dag=None,
        )


__all__ = ["SubmitDialog"]
