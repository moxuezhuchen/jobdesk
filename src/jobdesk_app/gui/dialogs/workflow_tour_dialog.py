"""Six-slide onboarding tour dialog (Phase 1.1).

Walk the user through the core JobDesk workflow: set up a server,
connect, pick inputs, build a graph, submit, and read results.

The dialog is intentionally text-only (ASCII art for visual emphasis).
Screenshots would be fragile across themes / OS scaling.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from jobdesk_app.gui.i18n import tr

# Keyed 1..6. (title_key, body_key).
_SLIDES: dict[int, tuple[str, str]] = {
    1: (
        "Set up a server",
        (
            "Open the Settings tab and add a Linux SSH server. You need:\n"
            "  - host, port, username\n"
            "  - auth method (key-based SSH)\n"
            "  - absolute path to your SSH private key\n"
            "JobDesk only supports key authentication today; passwords\n"
            "are not accepted, and the server dialog's auth combo offers\n"
            "key only. JobDesk stores these in servers.yaml under your\n"
            "user app data folder so they persist across launches."
        ),
    ),
    2: (
        "Connect & browse",
        (
            "On the Files tab, pick the server you just added and click\n"
            "Connect. Once connected you can browse the remote directory\n"
            "on the right. Look for .xyz, .gjf, and .inp files -- these\n"
            "are the inputs JobDesk can run."
        ),
    ),
    3: (
        "Pick your inputs",
        (
            "Right-click one or more remote files and choose\n"
            "  Use as input -> Submit\n"
            "JobDesk jumps to the Submit tab with those files pre-loaded.\n"
            "You can also drop files into the local panel."
        ),
    ),
    4: (
        "Build a workflow",
        (
            "Drag node types from the left library onto the canvas.\n"
            "Typical chain:\n"
            "  XYZ file -> Conformer generation (optional)\n"
            "            -> Geometry optimization\n"
            "            -> Frequency\n"
            "            -> Output\n"
            "Wire them by dragging from one node's output port to\n"
            "another's input port."
        ),
    ),
    5: (
        "Submit & monitor",
        (
            "Click Submit to Remote. JobDesk uploads your input files\n"
            "and the workflow spec to the server, then starts a remote\n"
            "run. You will be auto-jumped to the Runs tab where status\n"
            "updates flow in via SSH."
        ),
    ),
    6: (
        "Read results",
        (
            "Select a finished run and double-click any row in the\n"
            "result table to see parsed energy, ZPE, Gibbs free energy,\n"
            "vibrational frequencies and the final geometry.\n"
            "Compare runs by selecting multiple rows and choosing\n"
            "  Compare Selected."
        ),
    ),
}

_TOTAL_SLIDES = len(_SLIDES)


class WorkflowTourDialog(QDialog):
    """A 6-slide static-text tour shown when the user clicks the
    'Read 60-second tour' button on the empty canvas onboarding card.
    """

    def __init__(self, parent: QWidget | None = None, language: str = "en") -> None:
        super().__init__(parent)
        self._language = language
        self.setModal(True)
        # Fixed size per the plan; the body has ASCII art that wraps
        # differently at smaller widths, so we lock the layout.
        self.setFixedSize(520, 420)

        # -- Stacked body ------------------------------------------------
        self._stack = QStackedWidget(self)
        self._title_labels: list[QLabel] = []
        self._body_labels: list[QLabel] = []

        for index in range(1, _TOTAL_SLIDES + 1):
            page = QWidget(self._stack)
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(18, 16, 18, 12)
            page_layout.setSpacing(10)

            title = QLabel(page)
            title_font = title.font()
            title_font.setPointSize(max(title_font.pointSize() + 2, 12))
            title_font.setBold(True)
            title.setFont(title_font)
            title.setWordWrap(True)
            title.setObjectName(f"workflowTourTitle{index}")
            title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

            body = QLabel(page)
            body.setObjectName(f"workflowTourBody{index}")
            body.setWordWrap(True)
            body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body_font: QFont = body.font()
            body_font.setFamily("Consolas, Menlo, Courier New, monospace")
            body.setFont(body_font)

            page_layout.addWidget(title)
            page_layout.addWidget(body, 1)

            self._title_labels.append(title)
            self._body_labels.append(body)
            self._stack.addWidget(page)

        # -- Indicator strip ---------------------------------------------
        self._indicator = QLabel(self)
        self._indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._indicator.setObjectName("workflowTourIndicator")

        # -- Footer buttons ---------------------------------------------
        self._back_btn = QPushButton(self)
        self._back_btn.setObjectName("workflowTourBackButton")
        self._next_btn = QPushButton(self)
        self._next_btn.setObjectName("workflowTourNextButton")
        self._next_btn.setDefault(True)
        self._close_btn = QPushButton(self)
        self._close_btn.setObjectName("workflowTourCloseButton")
        self._close_btn.hide()  # only visible on the last slide

        self._back_btn.clicked.connect(self._on_back)
        self._next_btn.clicked.connect(self._on_next)
        self._close_btn.clicked.connect(self.accept)

        # Explicit Esc -> reject() so the test can observe a `done` signal
        # without depending on whether the close button is currently shown.
        self._esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._esc_shortcut.activated.connect(self.reject)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addWidget(self._back_btn)
        footer.addStretch(1)
        footer.addWidget(self._next_btn)
        footer.addWidget(self._close_btn)

        # -- Top-level layout -------------------------------------------
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)
        outer.addWidget(self._stack, 1)
        outer.addWidget(self._indicator)
        outer.addLayout(footer)

        self.apply_language(language)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def apply_language(self, language: str) -> None:
        self._language = language
        self.setWindowTitle(tr("Workflow tour", language))
        for index, (title_key, body_key) in _SLIDES.items():
            self._title_labels[index - 1].setText(tr(title_key, language))
            self._body_labels[index - 1].setText(tr(body_key, language))
        self._back_btn.setText(tr("Back", language))
        self._next_btn.setText(tr("Next", language))
        self._close_btn.setText(tr("Close", language))
        self._refresh_footer()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _on_next(self) -> None:
        current = self._stack.currentIndex()
        last = _TOTAL_SLIDES - 1
        if current >= last:
            self.accept()
            return
        self._stack.setCurrentIndex(current + 1)
        self._refresh_footer()

    def _on_back(self) -> None:
        current = self._stack.currentIndex()
        if current <= 0:
            return
        self._stack.setCurrentIndex(current - 1)
        self._refresh_footer()

    def _refresh_footer(self) -> None:
        current = self._stack.currentIndex()  # 0-based
        self._indicator.setText(
            tr("Slide {n} of {total}", self._language).format(
                n=current + 1, total=_TOTAL_SLIDES
            )
        )
        # Back disabled on slide 1.
        self._back_btn.setEnabled(current > 0)
        is_last = current >= _TOTAL_SLIDES - 1
        # On the last slide, the primary action morphs into Close.
        self._next_btn.setVisible(not is_last)
        self._close_btn.setVisible(is_last)
        if is_last:
            self._close_btn.setDefault(True)
            self._next_btn.setDefault(False)
        else:
            self._next_btn.setDefault(True)


__all__ = ["WorkflowTourDialog"]
