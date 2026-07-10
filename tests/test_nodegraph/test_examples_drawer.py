"""Built-in workflow example templates.

These tests assert three things about the on-disk fixtures under
``src/jobdesk_app/resources/workflow_examples``:

1. Every shipped fixture loads through ``serialization.from_json``.
2. Every loaded graph round-trips through ``serialization.to_json``
   (i.e. the JSON shapes are what the editor actually expects).
3. Clicking the toolbar Examples button on the editor with a known
   template id loads that template into the editor's graph.

The drawer is exercised via the public ``selected`` signal so the
test doesn't depend on Qt's popup positioning or menu event loop
quirks in offscreen mode.
"""
from __future__ import annotations

import pytest

from jobdesk_app.gui.nodegraph.editor import WorkflowGraphEditor
from jobdesk_app.gui.nodegraph.examples_drawer import (
    EXAMPLE_TEMPLATES,
    ExamplesDrawer,
    all_example_ids,
    get_example,
)
from jobdesk_app.gui.nodegraph.serialization import from_json, to_json
from jobdesk_app.services.gui_settings import GuiSettings, GuiSettingsStore


EXPECTED_IDS = (
    "linear_opt_freq",
    "conformer_ensemble",
    "fan_out_gen_opt",
    "fan_in_refine",
)


# ── fixture-loading tests (pure data, no Qt) ──────────────────────────


@pytest.mark.parametrize("template_id", list(EXPECTED_IDS))
def test_example_fixture_loads(template_id):
    tpl = get_example(template_id)
    graph = tpl.load_graph()
    # Every shipped template has at least XYZ_FILE + one calc + OUTPUT.
    assert len(graph.nodes) >= 3, f"{template_id} has too few nodes"
    # And at least one edge that wires something.
    assert len(graph.edges) >= 1, f"{template_id} has no edges"


@pytest.mark.parametrize("template_id", list(EXPECTED_IDS))
def test_example_fixture_validates_clean(template_id):
    """Loaded template has zero GraphIssue severity=error entries."""
    graph = get_example(template_id).load_graph()
    issues = graph.validate()
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], (
        f"{template_id} has validation errors: "
        f"{[i.message for i in errors]}"
    )


@pytest.mark.parametrize("template_id", list(EXPECTED_IDS))
def test_example_fixture_round_trips(template_id):
    """from_json(to_json(g)) preserves nodes, edges, ports, params."""
    original = get_example(template_id).load_graph()
    reloaded = from_json(to_json(original))
    assert set(reloaded.nodes) == set(original.nodes)
    assert set(reloaded.edges) == set(original.edges)
    for nid, original_node in original.nodes.items():
        reloaded_node = reloaded.nodes[nid]
        assert reloaded_node.kind == original_node.kind
        assert reloaded_node.title == original_node.title
        assert reloaded_node.params == original_node.params
        assert tuple(reloaded_node.inputs) == tuple(original_node.inputs)
        assert tuple(reloaded_node.outputs) == tuple(original_node.outputs)


def test_example_ids_listing_is_complete():
    assert set(EXPECTED_IDS) == set(all_example_ids())
    assert set(EXPECTED_IDS) == {t.id for t in EXAMPLE_TEMPLATES}


def test_get_example_raises_for_unknown_id():
    with pytest.raises(KeyError):
        get_example("does_not_exist")


# ── drawer widget + editor wiring tests ──────────────────────────────


def test_drawer_emits_selected_with_known_template_id(qtbot):
    drawer = ExamplesDrawer(language="en")
    qtbot.addWidget(drawer)
    drawer.show()
    qtbot.waitUntil(lambda: drawer.isVisible(), timeout=500)

    captured: list[str] = []
    drawer.selected.connect(captured.append)
    drawer.selected.emit("linear_opt_freq")
    assert captured == ["linear_opt_freq"]


def test_editor_loads_template_via_examples_drawer(qtbot, tmp_path):
    """Clicking the toolbar Examples entry loads the graph on the editor."""
    store = GuiSettingsStore(tmp_path / "gui_settings.yaml")
    store.save(GuiSettings(show_onboarding=False))
    editor = WorkflowGraphEditor(language="en", settings_store=store)
    editor.resize(900, 560)
    qtbot.addWidget(editor)
    editor.show()
    qtbot.waitUntil(lambda: editor.isVisible(), timeout=500)

    drawer = editor._examples_btn  # type: ignore[attr-defined]
    assert isinstance(drawer, ExamplesDrawer)

    with qtbot.waitSignal(editor.example_template_requested, timeout=500) as sig:
        drawer.selected.emit("linear_opt_freq")
    assert sig.args == ["linear_opt_freq"]

    graph = editor.graph()
    assert len(graph.nodes) == 4  # xyz + opt + freq + output
    titles = sorted(n.title for n in graph.nodes.values())
    assert titles == ["Frequency", "Optimize", "Output", "XYZ input"]


def test_editor_examples_drawer_round_trips_each_template(qtbot, tmp_path):
    """Every shipped template id can be loaded into the editor cleanly."""
    store = GuiSettingsStore(tmp_path / "gui_settings.yaml")
    store.save(GuiSettings(show_onboarding=False))
    editor = WorkflowGraphEditor(language="en", settings_store=store)
    editor.resize(900, 560)
    qtbot.addWidget(editor)
    editor.show()
    qtbot.waitUntil(lambda: editor.isVisible(), timeout=500)

    drawer = editor._examples_btn  # type: ignore[attr-defined]
    for template_id in EXPECTED_IDS:
        editor.scene().clear_graph()
        drawer.selected.emit(template_id)
        # After a fresh load the editor must carry the same nodes as
        # the file-level loader sees.
        expected = get_example(template_id).load_graph()
        assert set(editor.graph().nodes) == set(expected.nodes), (
            f"editor mismatch for {template_id}"
        )
        # And the loaded graph must validate with no errors.
        errors = [
            i for i in editor.graph().validate() if i.severity == "error"
        ]
        assert errors == [], (
            f"{template_id} produced errors after editor load: "
            f"{[i.message for i in errors]}"
        )
