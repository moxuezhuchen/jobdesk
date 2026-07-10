"""Properties panel: editing a spinbox emits the change + undo command.

We use the public ``node_params_changed`` signal as the assertion
hook: the editor (and the scene in our tests) subscribes to it and
pushes a :class:`SetParamsCommand` onto the undo stack.
"""
from __future__ import annotations

from jobdesk_app.gui.nodegraph.canvas import GraphScene, GraphView
from jobdesk_app.gui.nodegraph.model import NodeKind
from jobdesk_app.gui.nodegraph.properties import PropertiesPanel
from jobdesk_app.gui.nodegraph.serialization import SetParamsCommand


def _make_editor(qtbot):
    scene = GraphScene()
    view = GraphView(scene)
    panel = PropertiesPanel(language="en")
    qtbot.addWidget(view)
    qtbot.addWidget(panel)
    scene._properties_panel = panel  # type: ignore[attr-defined]
    return scene, view, panel


def test_selecting_node_populates_form(graph_scene):
    scene, _view = graph_scene
    panel = PropertiesPanel(language="en")
    node = scene.add_node(NodeKind.OPT, (10.0, 10.0))
    model_node = scene.graph().nodes[node.node_id]
    panel.show_node(model_node.id, model_node.kind, dict(model_node.params))
    # The OPT schema has 7 fields, all of which should now have widgets.
    assert len(panel._widgets) == 7
    assert "method" in panel._widgets
    assert "basis" in panel._widgets


def test_changing_spinbox_emits_node_params_changed(qtbot):
    scene, _view, panel = _make_editor(qtbot)
    node = scene.add_node(NodeKind.OPT, (10.0, 10.0))
    model_node = scene.graph().nodes[node.node_id]
    panel.show_node(model_node.id, model_node.kind, dict(model_node.params))
    captured: list[tuple[str, dict]] = []
    panel.node_params_changed.connect(lambda nid, params: captured.append((nid, params)))
    nproc_widget = panel._widgets["nproc"]
    nproc_widget.setValue(16)
    assert captured, "spinbox edit should emit node_params_changed"
    nid, params = captured[-1]
    assert nid == node.node_id
    assert params["nproc"] == 16


def test_param_edit_pushes_undo_command(qtbot):
    scene, _view, panel = _make_editor(qtbot)
    node = scene.add_node(NodeKind.OPT, (10.0, 10.0))
    model_node = scene.graph().nodes[node.node_id]
    # Seed the params dict so the test compares against a known baseline.
    initial_params = {"nproc": 8, "method": "B3LYP"}
    model_node.params = dict(initial_params)
    panel.show_node(model_node.id, model_node.kind, dict(model_node.params))
    panel.node_params_changed.connect(
        lambda nid, params: scene.undo_stack().push(SetParamsCommand(scene.graph(), nid, params))
    )
    baseline = scene.undo_stack().count()
    nproc_widget = panel._widgets["nproc"]
    nproc_widget.setValue(16)
    assert scene.undo_stack().count() == baseline + 1
    assert scene.graph().nodes[node.node_id].params["nproc"] == 16
    scene.undo_stack().undo()
    assert scene.graph().nodes[node.node_id].params["nproc"] == 8
    scene.undo_stack().redo()
    assert scene.graph().nodes[node.node_id].params["nproc"] == 16


def test_panel_for_kind_without_schema_shows_placeholder(qtbot):
    scene, _view, panel = _make_editor(qtbot)
    node = scene.add_node(NodeKind.XYZ_FILE, (10.0, 10.0))
    model_node = scene.graph().nodes[node.node_id]
    panel.show_node(model_node.id, model_node.kind, dict(model_node.params))
    assert panel._widgets == {}
    # The placeholder label is shown when the form is empty. We use the
    # ``_form_host.isVisible()`` flag as the "form is not shown"
    # assertion because QLabel.isVisible() depends on a visible parent
    # which the test does not establish.
    assert panel._form_host.isVisible() is False


# ── Phase 10.3: incoming-edges summary in properties panel ────────────


def test_panel_shows_incoming_edges_summary(qtbot):
    scene, _view, panel = _make_editor(qtbot)
    node = scene.add_node(NodeKind.OPT, (10.0, 10.0))
    model_node = scene.graph().nodes[node.node_id]
    panel.show_node_with_inputs(
        model_node.id,
        model_node.kind,
        dict(model_node.params),
        ["step1", "step2"],
    )
    # The summary header has been populated; Qt's ``isVisible()`` is
    # unreliable without a shown parent chain, so we assert on the
    # text it carries instead.
    text = panel._inputs_label.text()
    assert "step1" in text and "step2" in text
    assert "incoming" in text


def test_panel_summary_uses_singular_for_one_predecessor(qtbot):
    scene, _view, panel = _make_editor(qtbot)
    node = scene.add_node(NodeKind.OPT, (10.0, 10.0))
    model_node = scene.graph().nodes[node.node_id]
    panel.show_node_with_inputs(
        model_node.id,
        model_node.kind,
        dict(model_node.params),
        ["step1"],
    )
    text = panel._inputs_label.text()
    assert "step1" in text
    # Just one edge: the plural form should not be used.
    assert "1 incoming edge" in text


def test_panel_summary_zero_incoming(qtbot):
    scene, _view, panel = _make_editor(qtbot)
    node = scene.add_node(NodeKind.OPT, (10.0, 10.0))
    model_node = scene.graph().nodes[node.node_id]
    panel.show_node_with_inputs(
        model_node.id,
        model_node.kind,
        dict(model_node.params),
        [],
    )
    text = panel._inputs_label.text()
    assert "0 incoming" in text