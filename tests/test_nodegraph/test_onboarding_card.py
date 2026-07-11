"""Empty-canvas onboarding card behavior for the node-graph editor."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from jobdesk_app.gui.nodegraph.editor import WorkflowGraphEditor
from jobdesk_app.gui.nodegraph.model import NodeKind
from jobdesk_app.gui.nodegraph.onboarding_card import DEFAULT_EXAMPLE_TEMPLATE_ID
from jobdesk_app.services.gui_settings import GuiSettings, GuiSettingsStore


def _make_editor(qtbot, tmp_path, *, show_onboarding: bool = True) -> tuple[WorkflowGraphEditor, GuiSettingsStore]:
    store = GuiSettingsStore(tmp_path / "gui_settings.yaml")
    store.save(GuiSettings(show_onboarding=show_onboarding))
    editor = WorkflowGraphEditor(language="en", settings_store=store)
    editor.resize(900, 560)
    qtbot.addWidget(editor)
    editor.show()
    qtbot.waitUntil(lambda: editor.isVisible(), timeout=500)
    return editor, store


def _card(editor: WorkflowGraphEditor):
    card = editor.onboarding_card()
    assert card is not None
    return card


def test_default_card_visible_when_graph_empty_and_flag_on(qtbot, tmp_path):
    editor, _store = _make_editor(qtbot, tmp_path, show_onboarding=True)

    assert editor.graph().nodes == {}
    assert _card(editor).isVisible()


def test_hide_forever_persists_false_and_hides_card(qtbot, tmp_path):
    editor, store = _make_editor(qtbot, tmp_path, show_onboarding=True)
    card = _card(editor)
    hide_btn = card.findChild(QPushButton, "nodegraphOnboardingHideButton")
    assert hide_btn is not None

    qtbot.mouseClick(hide_btn, Qt.MouseButton.LeftButton)

    assert store.load().show_onboarding is False
    assert not card.isVisible()


def test_adding_first_node_hides_card(qtbot, tmp_path):
    editor, _store = _make_editor(qtbot, tmp_path, show_onboarding=True)
    card = _card(editor)

    editor.scene().add_node(NodeKind.XYZ_FILE, (0.0, 0.0))

    qtbot.waitUntil(lambda: not card.isVisible(), timeout=500)


def test_onboarding_actions_emit_expected_signals(qtbot, tmp_path):
    editor, _store = _make_editor(qtbot, tmp_path, show_onboarding=True)
    card = _card(editor)
    example_btn = card.findChild(QPushButton, "nodegraphOnboardingExampleButton")
    tour_btn = card.findChild(QPushButton, "nodegraphOnboardingTourButton")
    assert example_btn is not None
    assert tour_btn is not None

    with qtbot.waitSignal(editor.example_template_requested, timeout=500) as example_signal:
        qtbot.mouseClick(example_btn, Qt.MouseButton.LeftButton)
    assert example_signal.args == [DEFAULT_EXAMPLE_TEMPLATE_ID]

    with qtbot.waitSignal(editor.tour_requested, timeout=500) as tour_signal:
        qtbot.mouseClick(tour_btn, Qt.MouseButton.LeftButton)
    assert tour_signal.args == []
