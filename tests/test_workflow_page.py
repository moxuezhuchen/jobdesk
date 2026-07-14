"""Regression tests for the two-pane YAML + simple-flow workflow page."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import yaml

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QApplication  # noqa: E402

from jobdesk_app.core.workflow_spec import WorkflowSpec  # noqa: E402
from jobdesk_app.gui.nodegraph.model import Edge, NodeKind, default_node  # noqa: E402
from jobdesk_app.gui.pages.workflow_page import WorkflowPage  # noqa: E402
from jobdesk_app.services.method_presets import MethodPresetStore  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


class _StubState:
    current_project_root = None
    repo = None


@pytest.fixture
def page(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("jobdesk_app.services.method_presets.get_app_data_dir", lambda: tmp_path)
    widget = WorkflowPage(state=_StubState(), language="en", preset_store=MethodPresetStore())
    yield widget
    widget.close()
    widget.deleteLater()


def _first_step_id(page: WorkflowPage) -> str:
    step = next(
        (node for node in page._draft.graph.nodes.values() if node.kind not in {NodeKind.XYZ_FILE, NodeKind.OUTPUT}),
        None,
    )
    if step is None:
        page._add_step()
        step = next(node for node in page._draft.graph.nodes.values() if node.kind not in {NodeKind.XYZ_FILE, NodeKind.OUTPUT})
    return step.id


def test_page_has_two_authoring_panes_and_generated_preview(page):
    assert page.minimumWidth() >= 1040
    assert page.settings_tabs.count() == 2
    assert page.flow_scroll.widget() is page._flow_body
    assert page._flow_layout.count() >= 3  # input, output, spacer
    assert not [node for node in page._draft.graph.nodes.values() if node.kind in {
        NodeKind.OPT, NodeKind.SINGLE_POINT, NodeKind.FREQUENCY,
        NodeKind.CONF_GEN, NodeKind.PRE_OPT, NodeKind.TS, NodeKind.REFINE,
    }]
    assert "Add at least one workflow step" in page.full_yaml_preview.toPlainText()
    assert page.save_workflow_button.text() == "Save workflow"


def test_add_step_appends_to_simple_flow_and_updates_yaml(page):
    before = len([node for node in page._draft.graph.nodes.values() if node.kind in {NodeKind.OPT, NodeKind.SINGLE_POINT, NodeKind.FREQUENCY, NodeKind.CONF_GEN, NodeKind.PRE_OPT, NodeKind.TS, NodeKind.REFINE}])
    page.step_yaml_editor.setPlainText(
        "name: sp\n"
        "type: calc\n"
        "params:\n"
        "  iprog: orca\n"
        "  itask: sp\n"
        "  keyword: B3LYP def2-SVP\n"
    )
    page._add_step()
    steps = [node for node in page._draft.graph.nodes.values() if node.kind in {NodeKind.OPT, NodeKind.SINGLE_POINT, NodeKind.FREQUENCY, NodeKind.CONF_GEN, NodeKind.PRE_OPT, NodeKind.TS, NodeKind.REFINE}]
    assert len(steps) == before + 1
    assert page._selected_node_id is not None
    assert "itask: sp" in page.full_yaml_preview.toPlainText()


def test_each_flow_card_deletes_its_own_step_and_empty_flow_is_allowed(page):
    original_id = _first_step_id(page)
    page._add_step()

    # The original card must delete itself even while the new step is
    # selected; card controls cannot depend on incidental selection.
    page._delete_step(original_id)
    assert original_id not in page._draft.graph.nodes

    remaining_id = _first_step_id(page)
    page._delete_step(remaining_id)
    assert not [node for node in page._draft.graph.nodes.values() if node.kind in {
        NodeKind.OPT, NodeKind.SINGLE_POINT, NodeKind.FREQUENCY,
        NodeKind.CONF_GEN, NodeKind.PRE_OPT, NodeKind.TS, NodeKind.REFINE,
    }]
    assert "Add at least one workflow step" in page.full_yaml_preview.toPlainText()


def test_builtin_steps_are_available_but_no_workflow_is_preloaded(page):
    assert page.preset_combo.count() == 0
    assert page.step_preset_combo.isEnabled()
    assert page.save_step_preset_btn.isEnabled()
    assert "type: confgen" in page.step_yaml_editor.toPlainText()
    step_names = {page.step_preset_combo.itemText(index) for index in range(page.step_preset_combo.count())}
    assert {"confgen", "b3lyp_631gd_opt_freq", "b3lyp_def2tzvp_opt_freq"} <= step_names


def test_opening_confgen_step_replaces_the_selected_card_yaml(page):
    node_id = _first_step_id(page)
    page._on_node_selected(node_id)
    index = page.step_preset_combo.findText("confgen")
    page.step_preset_combo.setCurrentIndex(index)

    page._apply_step_preset()

    text = page.step_yaml_editor.toPlainText()
    assert "type: confgen" in text
    assert "chains:" in text
    assert "iprog: orca" not in text


def test_new_step_creates_an_independent_editable_fragment(page):
    page._new_step()

    assert page._selected_node_id is None
    assert page.step_preset_combo.currentIndex() == -1
    assert page.step_yaml_editor.toPlainText().startswith("name: new_step")
    assert page.save_step_preset_btn.isEnabled()
    assert not page.apply_step_preset_btn.isEnabled()


def test_new_confgen_step_uses_a_valid_confgen_fragment(page):
    page._new_step("confgen")

    text = page.step_yaml_editor.toPlainText()
    assert "name: new_confgen" in text
    assert "type: confgen" in text
    assert "chains:" in text
    assert not page.step_error_label.text()


def test_standalone_step_yaml_can_be_applied_before_switching(page):
    """The left step editor is usable without selecting a graph card."""
    page._new_step()
    page.step_yaml_editor.setPlainText(
        "name: reusable_ts\n"
        "type: calc\n"
        "params:\n"
        "  iprog: orca\n"
        "  itask: ts\n"
    )
    assert page._step_text_dirty
    page._apply_step_yaml()
    assert not page._step_text_dirty
    assert not page.step_error_label.text()


def test_steps_and_global_yaml_generate_a_reloadable_workflow(page):
    """Exercise the user path: select steps, assemble, save, and reopen."""
    for step_name in ("confgen", "b3lyp_631gd_opt_freq", "b3lyp_def2tzvp_opt_freq"):
        page.step_preset_combo.setCurrentIndex(page.step_preset_combo.findText(step_name))
        page._add_step()

    page.global_yaml_editor.setPlainText(
        "cores_per_task: 12\n"
        "total_memory: 24GB\n"
        "charge: -1\n"
        "multiplicity: 2\n"
    )
    page._apply_global_yaml()

    generated = page._build_workflow_yaml()
    parsed = yaml.safe_load(generated)
    WorkflowSpec.from_yaml(generated)

    assert parsed["global"] == {
        "cores_per_task": 12,
        "total_memory": "24GB",
        "charge": -1,
        "multiplicity": 2,
    }
    assert [step["type"] for step in parsed["steps"]] == ["confgen", "calc", "calc"]
    assert parsed["steps"][0]["params"]["angle_step"] == 120
    assert parsed["steps"][1]["params"]["iprog"] == "gaussian"
    assert parsed["steps"][2]["params"]["iprog"] == "orca"
    assert parsed["steps"][1]["inputs"] == ["confgen"]
    assert parsed["steps"][2]["inputs"] == ["b3lyp_631gd_opt_freq"]

    page._store.save_user_yaml("assembled_workflow", generated)
    page._refresh_workflow_presets()
    page._draft.dirty = False
    page.preset_combo.setCurrentIndex(0)

    reopened = yaml.safe_load(page._build_workflow_yaml())
    assert reopened == parsed


def test_workflow_chooser_lists_only_user_saved_workflows(page):
    page._store.save_user(
        "my_workflow",
        WorkflowSpec.from_form(
            work_dir_name="", program="orca", method="B3LYP", basis="def2-SVP",
            charge=0, multiplicity=1, nproc=4, memory_mb=4096, steps=("opt", "sp"),
        ),
    )

    page._refresh_workflow_presets()

    assert page.preset_combo.count() == 1
    assert page.preset_combo.itemText(0) == "my_workflow"
    assert page.preset_combo.itemData(0) == ("my_workflow", "user")


def test_step_yaml_applies_to_selected_node_and_regenerates_workflow(page):
    node_id = _first_step_id(page)
    page._on_node_selected(node_id)
    page.step_yaml_editor.setPlainText(
        "name: optimisation\n"
        "type: calc\n"
        "params:\n"
        "  iprog: orca\n"
        "  itask: opt\n"
        "  keyword: B3LYP def2-TZVP\n"
    )
    page._apply_step_yaml()
    node = page._draft.graph.nodes[node_id]
    assert node.title == "optimisation"
    assert node.params["iprog"] == "orca"
    assert "name: optimisation" in page.full_yaml_preview.toPlainText()


def test_validate_commits_pending_step_yaml_without_an_apply_button(page):
    node_id = _first_step_id(page)
    page._on_node_selected(node_id)
    page.step_yaml_editor.setPlainText(
        "name: renamed_opt\n"
        "type: calc\n"
        "params:\n"
        "  iprog: orca\n"
        "  itask: opt\n"
        "  keyword: PBE0 def2-SVP\n"
    )

    assert page._step_text_dirty
    page._validate_workflow()

    assert not page._step_text_dirty
    assert page._draft.graph.nodes[node_id].title == "renamed_opt"
    assert "name: renamed_opt" in page.full_yaml_preview.toPlainText()


def test_step_yaml_rejects_graph_owned_inputs(page):
    page._on_node_selected(_first_step_id(page))
    page.step_yaml_editor.setPlainText("name: bad\ntype: calc\nparams: {}\ninputs: [other]\n")
    page._apply_step_yaml()
    assert "Topology is graph-owned" in page.step_error_label.text()


def test_global_yaml_is_separate_from_step_yaml(page):
    _first_step_id(page)
    page.global_yaml_editor.setPlainText("cores_per_task: 16\ntotal_memory: 32GB\ncharge: -1\nmultiplicity: 2\n")
    page._apply_global_yaml()
    output = page.full_yaml_preview.toPlainText()
    assert "cores_per_task: 16" in output
    assert "charge: -1" in output


def test_graph_edge_generates_step_inputs(page):
    graph = page._draft.graph
    root = graph.nodes[_first_step_id(page)]
    child = default_node(NodeKind.SINGLE_POINT, position=(520.0, 120.0))
    child.title = "sp"
    child.params = {"iprog": "orca", "itask": "sp", "keyword": "B3LYP def2-TZVP"}
    graph.add_node(child)
    graph.add_edge(Edge(Edge.new_id(), root.id, "out", child.id, "in"))
    text = page._build_workflow_yaml()
    assert "inputs:\n  -" in text
    assert root.title in text


def test_fan_in_is_rejected_for_execution(page):
    graph = page._draft.graph
    first = graph.nodes[_first_step_id(page)]
    second = default_node(NodeKind.OPT, position=(520.0, 40.0))
    second.title = "second"
    graph.add_node(second)
    xyz = next(node for node in graph.nodes.values() if node.kind is NodeKind.XYZ_FILE)
    graph.add_edge(Edge(Edge.new_id(), xyz.id, "out", second.id, "in"))
    target = default_node(NodeKind.SINGLE_POINT, position=(760.0, 120.0))
    target.title = "target"
    graph.add_node(target)
    graph.add_edge(Edge(Edge.new_id(), first.id, "out", target.id, "in"))
    graph.add_edge(Edge(Edge.new_id(), second.id, "out", target.id, "in"))
    with pytest.raises(ValueError, match="multiple inputs"):
        page._build_workflow_yaml()


def test_saved_preset_can_be_dispatched(page):
    _first_step_id(page)
    page._draft.preset = type("SavedWorkflow", (), {"name": "saved", "source": "user"})()
    page._draft.dirty = False
    captured = []
    page.preset_chosen_for_submit.connect(lambda name, source: captured.append((name, source)))
    page._on_use_for_submit()
    assert captured
