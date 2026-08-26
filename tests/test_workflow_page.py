"""Regression tests for the two-pane YAML + simple-flow workflow page."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import yaml

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QSplitter, QWidget  # noqa: E402

from jobdesk_app.core.workflow_spec import WorkflowSpec  # noqa: E402
from jobdesk_app.gui.i18n import tr  # noqa: E402
from jobdesk_app.gui.nodegraph.model import Edge, NodeKind, default_node  # noqa: E402
from jobdesk_app.gui.pages.workflow_page import WorkflowPage  # noqa: E402
from jobdesk_app.services.gui_settings import GuiSettings, GuiSettingsStore  # noqa: E402
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
        step = next(
            node for node in page._draft.graph.nodes.values() if node.kind not in {NodeKind.XYZ_FILE, NodeKind.OUTPUT}
        )
    return step.id


def test_page_has_two_authoring_panes_and_generated_preview(page):
    assert page.minimumWidth() == 0
    assert page.settings_tabs.count() == 2
    splitter = page.findChild(QSplitter, "WorkflowAuthoringSplitter")
    assert splitter is page._workspace
    assert splitter.count() == 2
    assert splitter.widget(0) is page.settings_tabs.parentWidget()
    assert splitter.widget(1) is page._graph_panel
    assert page.flow_scroll.widget() is page._flow_body
    assert page._flow_layout.count() >= 3  # input, output, spacer
    assert not [
        node
        for node in page._draft.graph.nodes.values()
        if node.kind
        in {
            NodeKind.OPT,
            NodeKind.SINGLE_POINT,
            NodeKind.FREQUENCY,
            NodeKind.CONF_GEN,
            NodeKind.PRE_OPT,
            NodeKind.TS,
            NodeKind.REFINE,
        }
    ]
    assert not page._draft.graph.nodes
    assert page.full_yaml_preview.toPlainText() == ""
    assert page.validation_label.objectName() == "WorkflowValidationLabel"
    assert page.validation_label.property("validationState") == "incomplete"
    assert page.selected_step_label.text() == "Draft step — not added to workflow."
    assert page.save_workflow_button.text() == "Save workflow"
    assert page.btn_dispatch.objectName() == "WorkflowDispatchBtn"


def test_page_does_not_force_a_1006_by_652_host_window_to_grow(page, qapp):
    host = QWidget()
    host.resize(1006, 652)
    page.setParent(host)
    page.show()
    host.show()
    QApplication.processEvents()

    assert host.size().width() == 1006
    assert host.size().height() == 652

    page.setParent(None)
    host.close()


def test_empty_workflow_error_uses_active_language(page):
    page._language = "zh"

    with pytest.raises(ValueError, match="请至少添加一个工作流步骤"):
        page._build_workflow_yaml()


def test_add_step_appends_to_simple_flow_and_updates_yaml(page):
    before = len(
        [
            node
            for node in page._draft.graph.nodes.values()
            if node.kind
            in {
                NodeKind.OPT,
                NodeKind.SINGLE_POINT,
                NodeKind.FREQUENCY,
                NodeKind.CONF_GEN,
                NodeKind.PRE_OPT,
                NodeKind.TS,
                NodeKind.REFINE,
            }
        ]
    )
    page.step_yaml_editor.setPlainText(
        "name: sp\ntype: calc\nparams:\n  iprog: orca\n  itask: sp\n  keyword: B3LYP def2-SVP\n"
    )
    page._add_step()
    steps = [
        node
        for node in page._draft.graph.nodes.values()
        if node.kind
        in {
            NodeKind.OPT,
            NodeKind.SINGLE_POINT,
            NodeKind.FREQUENCY,
            NodeKind.CONF_GEN,
            NodeKind.PRE_OPT,
            NodeKind.TS,
            NodeKind.REFINE,
        }
    ]
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
    assert not [
        node
        for node in page._draft.graph.nodes.values()
        if node.kind
        in {
            NodeKind.OPT,
            NodeKind.SINGLE_POINT,
            NodeKind.FREQUENCY,
            NodeKind.CONF_GEN,
            NodeKind.PRE_OPT,
            NodeKind.TS,
            NodeKind.REFINE,
        }
    ]
    assert not page._draft.graph.nodes
    assert page.full_yaml_preview.toPlainText() == ""
    assert page.validation_label.property("validationState") == "incomplete"


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
    page.step_yaml_editor.setPlainText("name: reusable_ts\ntype: calc\nparams:\n  iprog: orca\n  itask: ts\n")
    assert page._step_text_dirty
    page._apply_step_yaml()
    assert not page._step_text_dirty
    assert not page.step_error_label.text()


def test_steps_and_global_yaml_generate_a_reloadable_workflow(page):
    """Exercise the user path: select steps, assemble, save, and reopen."""
    for step_name in ("confgen", "b3lyp_631gd_opt_freq", "b3lyp_def2tzvp_opt_freq"):
        page.step_preset_combo.setCurrentIndex(page.step_preset_combo.findText(step_name))
        page._add_step()

    page.global_yaml_editor.setPlainText("cores_per_task: 12\ntotal_memory: 24GB\ncharge: -1\nmultiplicity: 2\n")
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
            work_dir_name="",
            program="orca",
            method="B3LYP",
            basis="def2-SVP",
            charge=0,
            multiplicity=1,
            nproc=4,
            memory_mb=4096,
            steps=("opt", "sp"),
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
        "name: optimisation\ntype: calc\nparams:\n  iprog: orca\n  itask: opt\n  keyword: B3LYP def2-TZVP\n"
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
        "name: renamed_opt\ntype: calc\nparams:\n  iprog: orca\n  itask: opt\n  keyword: PBE0 def2-SVP\n"
    )

    assert page._step_text_dirty
    page._validate_workflow()

    assert not page._step_text_dirty
    assert page._draft.graph.nodes[node_id].title == "renamed_opt"
    assert "name: renamed_opt" in page.full_yaml_preview.toPlainText()


def test_preview_retranslates_after_language_switch(page):
    toggle = page.findChild(QPushButton, "PreviewToggleBtn")
    title = next(label for label in page.findChildren(QLabel) if label.text() == "YAML Preview")

    page.apply_language("zh")

    assert title.text() == "YAML 预览"
    assert toggle.toolTip() == "显示 YAML 预览"
    page._set_preview_expanded(True)
    assert toggle.toolTip() == "隐藏 YAML 预览"


def test_page_retranslates_static_controls_immediately(page):
    page.apply_language("zh")

    assert page._header.findChild(QLabel).text() == "工作流"
    assert page.preset_combo.placeholderText() == "暂无已保存的工作流"
    assert page.btn_new.text() == "新建"
    assert page.btn_validate.text() == "校验"
    assert page.settings_tabs.tabText(0) == "步骤 YAML"
    assert page.settings_tabs.tabText(1) == "全局 YAML"
    assert page.new_step_button.text() == "新建步骤"
    assert page.add_step_button.text() == "添加当前步骤"
    assert page.save_workflow_button.text() == "保存工作流"
    assert page.btn_dispatch.text() == "使用此工作流提交"

    page._new_step()
    page._add_step()
    page.apply_language("zh")
    assert page.inputs_label.text() == tr("Inputs: {names}", "zh", names=tr("workflow input", "zh"))


def test_flow_icon_buttons_name_their_target_for_tooltips_and_accessibility(page):
    page._new_step()
    page._add_step()
    node_id = _first_step_id(page)
    node = page._draft.graph.nodes[node_id]
    page._refresh_flow_diagram()
    card = next(card for card in page._flow_body.findChildren(QWidget) if card.objectName() == "WorkflowStepCard")
    buttons = card.findChildren(QPushButton)
    up, down, remove = (button for button in buttons if button.objectName() != "WorkflowStepSelectBtn")

    assert up.toolTip() == f"Move up: {node.title}"
    assert up.accessibleName() == up.toolTip()
    assert down.toolTip() == f"Move down: {node.title}"
    assert down.accessibleName() == down.toolTip()
    assert remove.toolTip() == f"Delete: {node.title}"
    assert remove.accessibleName() == remove.toolTip()


def test_validate_keeps_yaml_preview_collapsed_until_user_expands_it(page):
    toggle = page.findChild(QPushButton, "PreviewToggleBtn")

    assert toggle is not None
    assert page.full_yaml_preview.isHidden()
    assert toggle.text() == "\u25b6"
    assert toggle.toolTip() == "Show YAML preview"

    _first_step_id(page)
    page._validate_workflow()

    assert page.full_yaml_preview.isHidden()
    assert toggle.text() == "\u25b6"
    assert toggle.toolTip() == "Show YAML preview"

    toggle.click()

    assert not page.full_yaml_preview.isHidden()
    assert toggle.text() == "\u25bc"
    assert toggle.toolTip() == "Hide YAML preview"


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
    page.set_server_status(True, "connected")
    page._update_action_state()
    captured = []
    page.preset_chosen_for_submit.connect(lambda name, source: captured.append((name, source)))
    page._on_use_for_submit()
    assert captured


def test_four_step_guide_and_action_reasons_follow_authoring_state(page):
    guide = [label.text() for label in page.findChildren(QLabel) if label.objectName() == "WorkflowGuideStep"]
    assert guide == [
        "1. Choose or edit a step",
        "2. Add steps to the workflow",
        "3. Validate and save the workflow",
        "4. Submit a saved workflow",
    ]

    assert page.add_step_button.isEnabled()
    assert not page.btn_validate.isEnabled()
    assert not page.save_workflow_button.isEnabled()
    assert not page.btn_dispatch.isEnabled()
    assert page.btn_validate.toolTip() == "Add at least one workflow step."
    assert page.action_reason_label.text() == "Add at least one workflow step."

    _first_step_id(page)

    assert page.btn_validate.isEnabled()
    assert page.save_workflow_button.isEnabled()
    assert not page.btn_dispatch.isEnabled()
    assert page.btn_dispatch.toolTip() == "Save the workflow before submitting."

    page._draft.preset = type("SavedWorkflow", (), {"name": "saved", "source": "user"})()
    page._draft.dirty = False
    page._update_action_state()
    assert not page.btn_dispatch.isEnabled()
    assert page.btn_dispatch.toolTip() == "Connect to a server before submitting."

    page.set_server_status(True, "server")
    assert page.btn_dispatch.isEnabled()
    assert page.action_reason_label.text() == "Ready to submit."


def test_invalid_pending_yaml_disables_actions_and_uses_structured_feedback(page):
    _first_step_id(page)
    page.step_yaml_editor.setPlainText("not: [valid")

    assert not page.add_step_button.isEnabled()
    assert not page.btn_validate.isEnabled()
    assert not page.save_workflow_button.isEnabled()
    assert not page.btn_dispatch.isEnabled()
    assert page.add_step_button.toolTip() == "Fix the step YAML before continuing."
    assert page.validation_label.property("validationState") == "invalid"
    assert page.full_yaml_preview.toPlainText() != ""
    assert "Cannot generate workflow YAML" not in page.full_yaml_preview.toPlainText()
    assert not page.full_yaml_preview.toPlainText().lstrip().startswith("#")


def test_public_focus_helper_targets_step_editor(page):
    page.show()
    page.focus_authoring()
    QApplication.processEvents()

    assert page.step_yaml_editor.hasFocus()


def test_authoring_splitter_sizes_restore_and_persist(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("jobdesk_app.services.method_presets.get_app_data_dir", lambda: tmp_path)
    store = GuiSettingsStore(tmp_path / "gui_settings.yaml")
    store.save(GuiSettings(splitter_sizes={"workflow.authoring": [360, 680]}))
    widget = WorkflowPage(
        state=_StubState(),
        language="en",
        preset_store=MethodPresetStore(),
        settings_store=store,
    )
    widget.resize(1200, 800)
    widget.show()
    QApplication.processEvents()

    restored = widget._workspace.sizes()
    assert restored[0] < restored[1]

    widget._workspace.setSizes([420, 620])
    widget._workspace.splitterMoved.emit(420, 1)
    persisted = store.load().splitter_sizes["workflow.authoring"]
    assert len(persisted) == 2
    assert persisted[0] > 0
    assert persisted[1] > 0

    widget.close()
    widget.deleteLater()
