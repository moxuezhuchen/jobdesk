"""Real WorkflowPage load/save coverage for producer-owned v0.6 fields."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import yaml

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QApplication  # noqa: E402

from jobdesk_app.gui.pages.workflow_page import WorkflowPage  # noqa: E402
from jobdesk_app.services.method_presets import MethodPresetStore  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "workflow_documents"


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


class _StubState:
    current_project_root = None
    repo = None


def _load_page(qapp, monkeypatch, tmp_path) -> WorkflowPage:
    monkeypatch.setattr("jobdesk_app.services.method_presets.get_app_data_dir", lambda: tmp_path)
    store = MethodPresetStore()
    source = (FIXTURES / "v06_extensions_dag.yaml").read_text(encoding="utf-8")
    store.save_user_yaml("extensions", source)
    return WorkflowPage(state=_StubState(), language="en", preset_store=store)


def _load_document_page(qapp, monkeypatch, tmp_path, name: str, source: str) -> WorkflowPage:
    monkeypatch.setattr("jobdesk_app.services.method_presets.get_app_data_dir", lambda: tmp_path)
    store = MethodPresetStore()
    store.save_user_yaml(name, source)
    return WorkflowPage(state=_StubState(), language="en", preset_store=store)


def _new_page(qapp, monkeypatch, tmp_path) -> WorkflowPage:
    monkeypatch.setattr("jobdesk_app.services.method_presets.get_app_data_dir", lambda: tmp_path)
    return WorkflowPage(state=_StubState(), language="en", preset_store=MethodPresetStore())


def test_real_page_load_save_preserves_v06_dag_extensions(qapp, monkeypatch, tmp_path):
    page = _load_page(qapp, monkeypatch, tmp_path)
    try:
        source = yaml.safe_load((FIXTURES / "v06_extensions_dag.yaml").read_text(encoding="utf-8"))
        assert page._draft.preset is not None
        assert page._draft.projection_error
        assert len(page._draft.graph.nodes) > 0

        by_name = {node.title: node.id for node in page._draft.graph.nodes.values()}
        assert {edge.src_node for edge in page._draft.graph.edges.values() if edge.dst_node == by_name["left"]} == {
            by_name["source"]
        }
        assert {edge.src_node for edge in page._draft.graph.edges.values() if edge.dst_node == by_name["right"]} == {
            by_name["source"]
        }
        assert len(page._draft.graph.incoming_edges(by_name["join"])) == 2

        with patch(
            "jobdesk_app.gui.pages.workflow_page.QInputDialog.getText",
            return_value=("extensions_copy", True),
        ):
            assert page.save_current()

        saved = yaml.safe_load((tmp_path / "method_presets" / "extensions_copy.yaml").read_text(encoding="utf-8"))
        assert saved == source
    finally:
        page.close()
        page.deleteLater()


def test_real_page_known_param_edit_merges_unknown_fields(qapp, monkeypatch, tmp_path):
    page = _load_page(qapp, monkeypatch, tmp_path)
    try:
        source_node = next(node for node in page._draft.graph.nodes.values() if node.title == "source")
        page._on_node_selected(source_node.id)
        page.step_yaml_editor.setPlainText(
            yaml.safe_dump(
                {
                    "name": "source",
                    "type": "calc",
                    "params": {
                        "itask": "sp",
                        "iprog": "gaussian",
                        "keyword": "B3LYP def2-SVP",
                        "x-param": "untouched",
                    },
                    "disabled": True,
                },
                sort_keys=False,
            )
        )
        page._apply_step_yaml()
        assert page._draft.dirty

        with patch(
            "jobdesk_app.gui.pages.workflow_page.QInputDialog.getText",
            return_value=("extensions_edited", True),
        ):
            assert page.save_current()

        saved = yaml.safe_load((tmp_path / "method_presets" / "extensions_edited.yaml").read_text(encoding="utf-8"))
        first = saved["steps"][0]
        assert first["params"]["keyword"] == "B3LYP def2-SVP"
        assert first["params"]["x-param"] == "untouched"
        assert first["disabled"] is True
        assert saved["global"]["x-global"] == "preserved"
        assert saved["x-top"] == {"wizard": "metadata"}
        assert saved["steps"][3]["inputs"] == ["left", "right"]
        assert saved["steps"][3]["x-step"] == "preserve-me"
    finally:
        page.close()
        page.deleteLater()


def test_real_page_loaded_rename_updates_downstream_inputs(qapp, monkeypatch, tmp_path):
    """A loaded graph rename rewrites name-based edges on save."""

    source = """\
version: "0.6"
global:
  cores_per_task: 2
steps:
  - name: first
    type: calc
    params: {itask: opt, iprog: orca, keyword: B3LYP def2-SVP}
    disabled: true
  - name: second
    type: calc
    params: {itask: opt, iprog: orca, keyword: B3LYP def2-SVP}
    inputs: [first]
    x-step: preserve
"""
    page = _load_document_page(qapp, monkeypatch, tmp_path, "rename_source", source)
    try:
        first_id = next(node.id for node in page._draft.graph.nodes.values() if node.title == "first")
        page._on_node_selected(first_id)
        page.step_yaml_editor.setPlainText(
            "name: renamed\ntype: calc\nparams:\n  itask: opt\n  iprog: orca\n  keyword: B3LYP def2-SVP\n"
        )
        page._apply_step_yaml()

        assert page._draft.dirty
        assert not page._draft.topology_dirty

        with patch(
            "jobdesk_app.gui.pages.workflow_page.QInputDialog.getText",
            return_value=("rename_saved", True),
        ):
            assert page.save_current()

        saved = yaml.safe_load((tmp_path / "method_presets" / "rename_saved.yaml").read_text(encoding="utf-8"))
        assert [step["name"] for step in saved["steps"]] == ["renamed", "second"]
        assert saved["steps"][1]["inputs"] == ["renamed"]
        assert saved["steps"][0]["disabled"] is True
        assert saved["steps"][1]["x-step"] == "preserve"

        renamed_id = next(node.id for node in page._draft.graph.nodes.values() if node.title == "renamed")
        page._on_node_selected(renamed_id)
        page.step_yaml_editor.setPlainText(
            "name: renamed_again\ntype: calc\nparams:\n  itask: opt\n  iprog: orca\n" "  keyword: B3LYP def2-SVP\n"
        )
        page._apply_step_yaml()
        with patch(
            "jobdesk_app.gui.pages.workflow_page.QInputDialog.getText",
            return_value=("rename_saved_again", True),
        ):
            assert page.save_current()
        saved_again = yaml.safe_load(
            (tmp_path / "method_presets" / "rename_saved_again.yaml").read_text(encoding="utf-8")
        )
        assert saved_again["steps"][1]["inputs"] == ["renamed_again"]
    finally:
        page.close()
        page.deleteLater()


def test_real_page_duplicate_rename_refuses_save_without_overwriting_preset(qapp, monkeypatch, tmp_path):
    source = """\
version: "0.6"
global:
  cores_per_task: 2
steps:
  - name: first
    type: calc
    params: {itask: opt, iprog: orca, keyword: B3LYP def2-SVP}
  - name: second
    type: calc
    params: {itask: opt, iprog: orca, keyword: PBE0 def2-SVP}
    inputs: [first]
"""
    monkeypatch.setattr("jobdesk_app.services.method_presets.get_app_data_dir", lambda: tmp_path)
    store = MethodPresetStore()
    store.save_user_yaml("duplicate_source", source)
    errors: list[tuple[str, str]] = []
    page = WorkflowPage(
        state=_StubState(),
        language="en",
        preset_store=store,
        on_error=lambda title, message: errors.append((title, message)),
    )
    original_path = tmp_path / "method_presets" / "duplicate_source.yaml"
    target_path = tmp_path / "method_presets" / "duplicate_target.yaml"
    try:
        assert page._draft.preset is not None
        assert page._draft.preset.name == "duplicate_source"
        first_id = next(node.id for node in page._draft.graph.nodes.values() if node.title == "first")
        page._on_node_selected(first_id)
        page.step_yaml_editor.setPlainText(
            "name: second\ntype: calc\nparams:\n  itask: opt\n  iprog: orca\n  keyword: B3LYP def2-SVP\n"
        )

        assert page.save_current() is False
        assert not target_path.exists()
        assert original_path.read_text(encoding="utf-8") == source
        assert page._draft.preset.name == "duplicate_source"
        assert page.step_error_label.text() == "Step names must be unique."
        assert any("Step names must be unique." in message for _, message in errors)
    finally:
        page.close()
        page.deleteLater()


def test_real_page_new_graph_stays_editable_after_first_save(qapp, monkeypatch, tmp_path):
    page = _new_page(qapp, monkeypatch, tmp_path)
    try:
        page._new_step("calc")
        page.step_yaml_editor.setPlainText(
            "name: first\ntype: calc\nparams:\n  itask: opt\n  iprog: orca\n  keyword: B3LYP def2-SVP\n"
        )
        page._add_step()

        with patch(
            "jobdesk_app.gui.pages.workflow_page.QInputDialog.getText",
            return_value=("new_graph", True),
        ):
            assert page.save_current()

        assert page._draft.raw_document is None
        first_id = next(node.id for node in page._draft.graph.nodes.values() if node.title == "first")

        page._new_step("calc")
        page.step_yaml_editor.setPlainText(
            "name: second\ntype: calc\nparams:\n  itask: opt\n  iprog: orca\n  keyword: PBE0 def2-SVP\n"
        )
        page._add_step()
        second_id = next(node.id for node in page._draft.graph.nodes.values() if node.title == "second")
        page._move_step(second_id, -1)
        page._delete_step(first_id)

        assert [node.title for node in page._ordered_step_nodes()] == ["second"]
        assert page._draft.raw_document is None
        assert page._draft.topology_dirty

        with patch(
            "jobdesk_app.gui.pages.workflow_page.QInputDialog.getText",
            return_value=("new_graph_edited", True),
        ):
            assert page.save_current()

        saved = yaml.safe_load((tmp_path / "method_presets" / "new_graph_edited.yaml").read_text(encoding="utf-8"))
        assert [step["name"] for step in saved["steps"]] == ["second"]
        assert page._draft.raw_document is None
        assert not page._draft.topology_dirty
    finally:
        page.close()
        page.deleteLater()


def test_real_page_loaded_linear_graph_allows_topology_edit_and_merges_extensions(qapp, monkeypatch, tmp_path):
    source = """\
version: "0.6"
global:
  cores_per_task: 2
  x-global: preserve
steps:
  - name: first
    type: calc
    params: {itask: opt, x-param: first}
    disabled: true
  - name: second
    type: calc
    params: {itask: opt}
    inputs: [first]
    x-step: preserve
x-top: metadata
"""
    page = _load_document_page(qapp, monkeypatch, tmp_path, "linear", source)
    try:
        assert not page._draft.projection_error
        second_id = next(node.id for node in page._draft.graph.nodes.values() if node.title == "second")

        page._move_step(second_id, -1)
        assert [node.title for node in page._ordered_step_nodes()] == ["second", "first"]
        assert page._draft.topology_dirty

        with patch(
            "jobdesk_app.gui.pages.workflow_page.QInputDialog.getText",
            return_value=("linear_edited", True),
        ):
            assert page.save_current()

        saved = yaml.safe_load((tmp_path / "method_presets" / "linear_edited.yaml").read_text(encoding="utf-8"))
        assert [step["name"] for step in saved["steps"]] == ["second", "first"]
        assert saved["steps"][0]["x-step"] == "preserve"
        assert saved["steps"][1]["disabled"] is True
        assert saved["steps"][1]["params"]["x-param"] == "first"
        assert saved["global"]["x-global"] == "preserve"
        assert saved["x-top"] == "metadata"
        assert saved["steps"][1]["inputs"] == ["second"]
        assert not page._draft.topology_dirty
    finally:
        page.close()
        page.deleteLater()


def test_real_page_incompatible_loaded_topology_is_read_only_with_ui_reason(qapp, monkeypatch, tmp_path):
    page = _load_page(qapp, monkeypatch, tmp_path)
    try:
        assert page._draft.projection_error
        reason = page.action_reason_label.text()
        assert "read-only" in reason
        assert "original document" in reason
        assert not page.add_step_button.isEnabled()
        assert page.add_step_button.toolTip() == reason
        assert "Read-only topology" in page.dirty_label.text()

        before = [node.title for node in page._ordered_step_nodes()]
        source_id = next(node.id for node in page._draft.graph.nodes.values() if node.title == "source")
        page._move_step(source_id, 1)
        page._delete_step(source_id)
        assert [node.title for node in page._ordered_step_nodes()] == before
        assert not page._draft.dirty
    finally:
        page.close()
        page.deleteLater()
