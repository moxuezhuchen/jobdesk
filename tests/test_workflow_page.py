"""Stage 4 — workflow builder page (GUI) tests."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def _state_stub():
    from jobdesk_app.gui.state import AppState
    return AppState()


def _log(msg):
    pass


def _status(msg):
    pass


def _error(title, message):
    raise AssertionError(f"{title}: {message}")


def test_workflow_page_builds_default_state(qapp):
    from jobdesk_app.gui.pages.workflow_builder_page import WorkflowBuilderPage

    page = WorkflowBuilderPage(_state_stub(), _log, _status, _error)
    page._refresh_yaml_preview()
    text = page.yaml_preview.toPlainText()
    assert "global:" in text
    assert "steps: []" in text or "steps:" in text


def test_workflow_page_add_calc_step_updates_preview(qapp):
    from jobdesk_app.gui.pages.workflow_builder_page import WorkflowBuilderPage

    page = WorkflowBuilderPage(_state_stub(), _log, _status, _error)
    page._on_add_step("calc")
    page._refresh_yaml_preview()
    text = page.yaml_preview.toPlainText()
    assert "type: calc" in text
    assert "iprog" in text


def test_workflow_page_add_confgen_step(qapp):
    from jobdesk_app.gui.pages.workflow_builder_page import WorkflowBuilderPage

    page = WorkflowBuilderPage(_state_stub(), _log, _status, _error)
    page._on_add_step("confgen")
    page._refresh_yaml_preview()
    text = page.yaml_preview.toPlainText()
    assert "type: confgen" in text
    assert "engine" in text


def test_workflow_page_round_trip_yaml(qapp, tmp_path):
    from jobdesk_app.gui.pages.workflow_builder_page import WorkflowBuilderPage
    from jobdesk_app.workflow.builder import yaml_to_form_state

    page = WorkflowBuilderPage(_state_stub(), _log, _status, _error)
    page._on_add_step("calc")
    # Tweak the first calc step's keyword via the form state directly
    page._form_state.steps[0].params["keyword"] = "B3LYP def2-SVP opt"
    page._refresh_yaml_preview()
    text = page.yaml_preview.toPlainText()
    parsed = yaml_to_form_state(text)
    assert len(parsed.steps) == 1
    assert parsed.steps[0].type == "calc"


def test_workflow_page_remove_step(qapp):
    from jobdesk_app.gui.pages.workflow_builder_page import WorkflowBuilderPage

    page = WorkflowBuilderPage(_state_stub(), _log, _status, _error)
    page._on_add_step("calc")
    page._on_add_step("confgen")
    assert len(page._form_state.steps) == 2
    page._current_step_index = 0
    page._on_remove_step()
    assert len(page._form_state.steps) == 1
    assert page._form_state.steps[0].type == "confgen"


def test_workflow_page_move_step(qapp):
    from jobdesk_app.gui.pages.workflow_builder_page import WorkflowBuilderPage

    page = WorkflowBuilderPage(_state_stub(), _log, _status, _error)
    page._on_add_step("calc")
    page._on_add_step("confgen")
    page._current_step_index = 0
    page._on_move_step(1)
    assert page._form_state.steps[0].type == "confgen"
    assert page._form_state.steps[1].type == "calc"


def test_workflow_page_validate_button_calls_runtime(qapp):
    from jobdesk_app.gui.pages.workflow_builder_page import WorkflowBuilderPage
    from jobdesk_app.workflow.builder import (
        default_form_state,
        StepState,
        form_state_to_yaml,
    )

    page = WorkflowBuilderPage(_state_stub(), _log, _status, _error)
    # Build a valid form state and inject it via load.
    state = default_form_state()
    state.global_options["charge"] = 0
    state.global_options["keyword"] = "B3LYP def2-SVP"
    state.steps.append(
        StepState(
            type="calc",
            params={"name": "x", "iprog": "orca", "itask": "opt_freq", "keyword": "B3LYP def2-SVP opt"},
        )
    )
    page._form_state = state
    page._refresh_step_list()
    page._render_global_form()
    page._render_step_form()
    page._refresh_yaml_preview()
    page._on_validate()  # should not raise


def test_workflow_page_signal_emitted_on_submit(qapp):
    from jobdesk_app.gui.pages.workflow_builder_page import WorkflowBuilderPage

    page = WorkflowBuilderPage(_state_stub(), _log, _status, _error)
    page._on_add_step("calc")
    page._form_state.global_options["keyword"] = "B3LYP def2-SVP"
    captured = []
    page.workflow_built.connect(lambda text, payload: captured.append((text, payload)))
    # Force a server id so submit doesn't bail out.
    page._state.last_agent_server = "test-server"
    page._on_submit_to_agent()
    assert len(captured) == 1
    text, payload = captured[0]
    assert "global:" in text
    assert "type: calc" in text
    assert payload == {"name": "wizard", "steps": []}


def test_workflow_page_language_round_trip(qapp):
    from jobdesk_app.gui.pages.workflow_builder_page import WorkflowBuilderPage

    page = WorkflowBuilderPage(_state_stub(), _log, _status, _error)
    page.apply_language("zh")
    assert page.btn_load.text() == "\u52a0\u8f7d YAML"
    page.apply_language("en")
    assert page.btn_load.text() == "Load YAML"
