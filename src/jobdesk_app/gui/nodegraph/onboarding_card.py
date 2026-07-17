"""Empty-canvas onboarding card for the workflow graph editor."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from jobdesk_app.gui.i18n import tr

DEFAULT_EXAMPLE_TEMPLATE_ID = "linear_opt_freq"


class OnboardingCard(QFrame):
    """Small call-to-action card shown over an empty workflow canvas."""

    example_template_requested = Signal(str)
    tour_requested = Signal()
    hide_forever_requested = Signal()
    quick_start_requested = Signal()

    def __init__(self, language: str = "en", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._language = language
        self.setObjectName("nodegraphOnboardingCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._title = QLabel(self)
        self._subtitle = QLabel(self)
        self._hint = QLabel(self)
        for label in (self._title, self._subtitle, self._hint):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
        title_font = self._title.font()
        title_font.setPointSize(max(title_font.pointSize() + 2, 12))
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._example_btn = QPushButton(self)
        self._example_btn.setObjectName("nodegraphOnboardingExampleButton")
        self._tour_btn = QPushButton(self)
        self._tour_btn.setObjectName("nodegraphOnboardingTourButton")
        self._hide_btn = QPushButton(self)
        self._hide_btn.setObjectName("nodegraphOnboardingHideButton")
        self._example_btn.clicked.connect(
            lambda _checked=False: self.example_template_requested.emit(DEFAULT_EXAMPLE_TEMPLATE_ID)
        )
        self._tour_btn.clicked.connect(lambda _checked=False: self.tour_requested.emit())
        self._hide_btn.clicked.connect(lambda _checked=False: self.hide_forever_requested.emit())
        self._quick_start_btn = QPushButton(self)
        self._quick_start_btn.setObjectName("nodegraphOnboardingQuickStartButton")
        self._quick_start_btn.clicked.connect(lambda _checked=False: self.quick_start_requested.emit())

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 8, 0, 0)
        actions.setSpacing(8)
        actions.addWidget(self._example_btn)
        actions.addWidget(self._tour_btn)
        actions.addWidget(self._quick_start_btn)
        actions.addWidget(self._hide_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(6)
        layout.addWidget(self._title)
        layout.addWidget(self._subtitle)
        layout.addWidget(self._hint)
        layout.addLayout(actions)
        self.setStyleSheet(
            "#nodegraphOnboardingCard {"
            " background: #ffffff; border: 1px solid #d7dde5; border-radius: 14px;"
            "}"
            "#nodegraphOnboardingCard QLabel { color: #253041; }"
            "#nodegraphOnboardingCard QPushButton { padding: 6px 10px; }"
        )
        self.apply_language(language)

    def apply_language(self, language: str) -> None:
        self._language = language
        self._title.setText(tr("Start your workflow graph", language))
        self._subtitle.setText(tr("Use a ready-made template, or drag nodes from the library.", language))
        self._hint.setText(tr("Connect steps left to right, then preview the generated workflow YAML.", language))
        self._example_btn.setText(tr("Use an example template", language))
        self._tour_btn.setText(tr("Read 60-second tour", language))
        # Review-fix: the button used to read "Quick start: load a single
        # OPT template" but it actually wires ``linear_opt_freq`` which
        # is a three-step Geometry optimization → Frequency chain, not a
        # bare OPT. The label was misleading new users into thinking
        # the chain contained exactly one step. The new wording mirrors
        # the model label that NodeLibraryPanel already exposes, so the
        # button does what the label says.
        self._quick_start_btn.setText(tr("Quick start: load Linear OPT + FREQ", language))
        self._hide_btn.setText(tr("Hide forever", language))


__all__ = ["DEFAULT_EXAMPLE_TEMPLATE_ID", "OnboardingCard"]
