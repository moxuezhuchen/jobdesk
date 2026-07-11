"""Regression tests for the GUI review findings (Phase 11.1).

Each test maps to one of the findings from the GUI design review:

* F1 — ``WorkflowGraphEditor`` is a plain :class:`QWidget` and is
  actually visible when embedded in the Submit page's VBoxLayout.
* F2 — Selecting a node with incoming edges triggers
  ``show_node_with_inputs`` so the properties panel renders a fan-in
  summary.
* F3 — ``_SidebarItem`` accepts keyboard focus, exposes an accessible
  name, and activates on Space/Enter.
* F4 — ``InputSourcePanel._dropEvent`` emits ``sources_changed`` after a
  successful drop.
* F5 — Submit page re-translates its group titles / server pill on
  language switch; the Runs-results detail pane uses the active
  language, not a hard-coded ``"en"``.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from jobdesk_app.core.submit_payload import InputSource
from jobdesk_app.gui.design.components import Sidebar, _SidebarItem
from jobdesk_app.gui.i18n import tr
from jobdesk_app.gui.nodegraph.editor import WorkflowGraphEditor
from jobdesk_app.gui.nodegraph.model import NodeKind, default_node
from jobdesk_app.gui.nodegraph.properties import PropertiesPanel
from jobdesk_app.gui.pages.submit_page import SubmitPage
from jobdesk_app.gui.widgets.input_source_panel import InputSourcePanel

# ── F1: editor is a real QWidget and lays out with positive geometry ──────────


def test_editor_is_plain_qwidget_not_qmainwindow(qtbot) -> None:
    """WorkflowGraphEditor must inherit QWidget so Qt embeds it."""
    editor = WorkflowGraphEditor(language="en")
    qtbot.addWidget(editor)
    assert isinstance(editor, QWidget)
    # QMainWindow is a QWidget too — but the regression is "it's a
    # top-level window", so check via the explicit type string instead.
    assert type(editor).__name__ == "WorkflowGraphEditor"
    # If anyone reintroduces QMainWindow, fail loudly.
    from PySide6.QtWidgets import QMainWindow
    assert not isinstance(editor, QMainWindow), (
        "WorkflowGraphEditor must NOT inherit QMainWindow — Qt refuses "
        "to embed a top-level window as a child layout item."
    )


def test_editor_has_positive_geometry_when_embedded(qtbot) -> None:
    """When embedded in a parent with a real layout, geometry > 0."""
    host = QWidget()
    qtbot.addWidget(host)
    from PySide6.QtWidgets import QVBoxLayout
    layout = QVBoxLayout(host)
    editor = WorkflowGraphEditor(language="en", parent=host)
    layout.addWidget(editor, 1)
    host.resize(800, 600)
    host.show()
    qtbot.waitExposed(host)
    QApplication.processEvents()
    geom = editor.geometry()
    assert geom.isValid()
    assert geom.width() > 0
    assert geom.height() > 0
    assert editor.isVisibleTo(host)


def test_submit_page_embeds_editor_without_separate_window(qtbot) -> None:
    """SubmitPage exposes editor as a direct child widget, no Qt.Window flag."""
    page = SubmitPage(state=None, language="en")
    qtbot.addWidget(page)
    page.resize(900, 700)
    page.show()
    qtbot.waitExposed(page)
    QApplication.processEvents()
    assert page.editor is not None
    assert page.editor.parent() is page
    # The editor must not have any Qt.Window* window flags — those
    # would make Qt treat it as an independent top-level window and
    # hide it from the host layout.
    assert not (page.editor.windowFlags() & Qt.WindowType.Window)


# ── F2: editor selection wires show_node_with_inputs ──────────────────────────


def test_editor_selection_triggers_fan_in_summary(qtbot) -> None:
    """Selecting a node with an incoming edge calls show_node_with_inputs."""
    editor = WorkflowGraphEditor(language="en")
    qtbot.addWidget(editor)
    editor.resize(900, 600)
    editor.show()
    qtbot.waitExposed(editor)
    QApplication.processEvents()

    scene = editor.scene()
    src = scene.add_node(NodeKind.XYZ_FILE, (-100.0, 0.0))
    dst = scene.add_node(NodeKind.PRE_OPT, (100.0, 0.0))
    scene.add_edge_at(src.node_id, "out", dst.node_id, "in")

    # Select the destination node so the editor's selection handler fires.
    dst_item = scene._node_items.get(dst.node_id)
    assert dst_item is not None, "test setup: node item missing"
    scene.clearSelection()
    dst_item.setSelected(True)
    QApplication.processEvents()

    panel = editor.properties_panel()
    # The inputs header label must be visible after selection.
    assert panel._inputs_label.isVisible()
    text = panel._inputs_label.text()
    assert "incoming" in text.lower()
    # And the upstream node title must appear in the rendered list.
    src_model = scene.graph().nodes[src.node_id]
    assert src_model.title in text


def test_editor_selection_no_edges_hides_summary(qtbot) -> None:
    """Selecting an isolated node renders a 0-edges placeholder summary."""
    editor = WorkflowGraphEditor(language="en")
    qtbot.addWidget(editor)
    editor.resize(900, 600)
    editor.show()
    qtbot.waitExposed(editor)
    QApplication.processEvents()

    scene = editor.scene()
    node = scene.add_node(NodeKind.OPT, (0.0, 0.0))
    item = scene._node_items.get(node.node_id)
    scene.clearSelection()
    item.setSelected(True)
    QApplication.processEvents()

    panel = editor.properties_panel()
    # No incoming edges → the inputs header becomes a "0 incoming edges"
    # placeholder (i18n key 'Inputs: 0 incoming edges'). The header is
    # visible so the user can see this node is a graph source.
    assert panel._inputs_label.isVisible()
    assert "0" in panel._inputs_label.text()


# ── F3: SidebarItem keyboard activation + accessibility metadata ─────────────


def test_sidebar_item_has_focus_policy_and_accessible_name(qtbot) -> None:
    """Each sidebar entry must be keyboard-reachable and announce itself."""
    sidebar = Sidebar(
        items=[("files", "Files"), ("rocket", "Submit"), ("chart", "Runs")],
    )
    qtbot.addWidget(sidebar)
    sidebar.resize(80, 400)
    sidebar.show()
    QApplication.processEvents()

    items: list[_SidebarItem] = list(sidebar._items)
    assert len(items) == 3
    for item, expected_label in zip(items, ["Files", "Submit", "Runs"]):
        # Keyboard accessibility — focusable + announcement.
        assert item.focusPolicy() == Qt.FocusPolicy.StrongFocus
        assert item.accessibleName() == expected_label
        assert item.toolTip() == expected_label


def test_sidebar_item_activates_on_space_and_enter(qtbot) -> None:
    """Space and Enter must trigger the clicked signal."""
    sidebar = Sidebar(items=[("files", "Files"), ("rocket", "Submit")])
    qtbot.addWidget(sidebar)
    sidebar.show()
    QApplication.processEvents()

    first: _SidebarItem = sidebar._items[0]

    activations: list[int] = []

    def _on_click() -> None:
        activations.append(sidebar._current)

    first.clicked.connect(_on_click)

    # Space
    space = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
    first.keyPressEvent(space)
    QApplication.processEvents()
    # Enter (the keypad-style Key_Enter)
    enter = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Enter, Qt.KeyboardModifier.NoModifier)
    first.keyPressEvent(enter)
    QApplication.processEvents()
    # Return (the main keyboard Enter)
    ret = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    first.keyPressEvent(ret)
    QApplication.processEvents()

    assert len(activations) == 3


# ── F4: drag-drop emits sources_changed ───────────────────────────────────────


def test_drop_event_emits_sources_changed(qtbot, tmp_path: Path) -> None:
    """Dropping a valid file onto the panel must emit sources_changed."""
    xyz_file = tmp_path / "water.xyz"
    xyz_file.write_text("3\n\no 0 0 0\nh 0 0 1\nh 0 1 0\n")

    panel = InputSourcePanel(parent=None, language="en")
    qtbot.addWidget(panel)
    panel.show()
    QApplication.processEvents()

    emissions: list[list[InputSource]] = []
    panel.sources_changed.connect(lambda srcs: emissions.append(list(srcs)))

    # Build a synthetic drop with a local-file URL and dispatch it on
    # the local _TabBody (that's where the drag/drop event handlers
    # live — the InputSourcePanel just hosts the tabs).
    from PySide6.QtCore import QMimeData, QPoint, QUrl
    from PySide6.QtGui import QDropEvent

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(xyz_file))])

    drop = QDropEvent(
        QPoint(10, 10),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    panel.local_tab._dropEvent(drop)
    QApplication.processEvents()

    assert len(emissions) == 1, (
        f"expected exactly one sources_changed emission, got {len(emissions)}"
    )
    assert len(emissions[0]) == 1
    assert emissions[0][0].path == xyz_file


def test_drop_event_ignored_files_do_not_emit(qtbot, tmp_path: Path) -> None:
    """Dropping a non-allowed file extension must not emit sources_changed."""
    txt = tmp_path / "notes.txt"
    txt.write_text("hello")

    panel = InputSourcePanel(parent=None, language="en")
    qtbot.addWidget(panel)
    panel.show()
    QApplication.processEvents()

    emissions: list[list[InputSource]] = []
    panel.sources_changed.connect(lambda srcs: emissions.append(list(srcs)))

    from PySide6.QtCore import QMimeData, QPoint, QUrl
    from PySide6.QtGui import QDropEvent

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(txt))])

    drop = QDropEvent(
        QPoint(10, 10),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    panel.local_tab._dropEvent(drop)
    QApplication.processEvents()

    assert emissions == []


# ── F5: dynamic i18n — submit page group titles, runs-results detail pane ────


def test_submit_page_apply_language_retranslates_groups(qtbot) -> None:
    """After apply_language, preview/log group titles follow the new language."""
    page = SubmitPage(state=None, language="en")
    qtbot.addWidget(page)
    page.show()
    QApplication.processEvents()

    assert page._preview_box.title() == tr("Live preview", "en")
    assert page._log_box.title() == tr("Activity log", "en")

    page.apply_language("zh")
    QApplication.processEvents()
    assert page._preview_box.title() == tr("Live preview", "zh")
    assert page._log_box.title() == tr("Activity log", "zh")
    # zh translations must not be the raw English string.
    assert page._preview_box.title() != tr("Live preview", "en")


def test_runs_results_detail_pane_uses_active_language(qtbot) -> None:
    """Detail pane clear() must use the active language, not a hard-coded 'en'."""
    from jobdesk_app.gui.pages.runs_results_page import ResultDetailPane

    pane = ResultDetailPane()
    qtbot.addWidget(pane)

    pane.apply_language("zh")
    pane.clear()
    QApplication.processEvents()
    zh_text = tr("Select a task to see details", "zh")
    assert pane.title_label.text() == zh_text
    assert pane.title_label.text() != tr("Select a task to see details", "en")

    pane.apply_language("en")
    pane.clear()
    QApplication.processEvents()
    assert pane.title_label.text() == tr("Select a task to see details", "en")


# ── Properties panel sanity — covers the show_node_with_inputs API used by F2


def test_properties_panel_show_node_with_inputs_renders_summary(qtbot) -> None:
    """Direct test of the 4-arg show_node_with_inputs API."""
    panel = PropertiesPanel(language="en")
    qtbot.addWidget(panel)
    panel.show()
    QApplication.processEvents()

    node = default_node(NodeKind.OPT, position=(0.0, 0.0))
    panel.show_node_with_inputs(node.id, node.kind, dict(node.params), ["step1", "step2"])
    QApplication.processEvents()
    assert panel._inputs_label.isVisible()
    text = panel._inputs_label.text()
    assert "step1" in text and "step2" in text
