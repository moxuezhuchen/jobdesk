"""Tests for :class:`SubmitPage` (Phase 14B).

The Submit page is the new unified submit UI: it embeds an
:class:`InputSourcePanel`, a :class:`CalculationWidget`, a
:class:`WorkflowWidget`, an :class:`InputBuilderWidget`, and emits
``submit_requested(SubmitPayload)`` / ``create_only_requested(...)``
signals. This file covers the page-level wiring that the lower-level
widget tests don't exercise:

* ``push_sources()`` (the cross-page wire endpoint from FileTransferPage).
* Submit / create-only click builds a payload and emits the signal.
* Validation errors are surfaced in the activity log.
* Language switching re-translates the embedded widgets.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.core.submit_payload import InputSource
from jobdesk_app.gui.pages.submit_page import SubmitPage

# --- fixtures -------------------------------------------------------------


@pytest.fixture
def app_state():
    state = MagicMock()
    state.current_project_root = Path(".")
    return state


@pytest.fixture
def page(qtbot, app_state):
    widget = SubmitPage(
        state=app_state,
        language="en",
        on_status=lambda m: None,
        on_error=lambda title, msg: None,
    )
    qtbot.addWidget(widget)
    return widget


# --- push_sources wire endpoint -------------------------------------------


def test_push_sources_replaces_input_list(page, tmp_path, qtbot):
    src1 = InputSource(path=tmp_path / "a.xyz", side="local", kind="xyz")
    src2 = InputSource(path=tmp_path / "b.xyz", side="local", kind="xyz")

    with qtbot.waitSignal(page.use_as_input_received, timeout=500) as sig:
        page.push_sources([src1, src2])

    received = sig.args[0]
    assert len(received) == 2
    assert {s.path.name for s in received} == {"a.xyz", "b.xyz"}


def test_push_sources_logs_to_activity_log(page, tmp_path):
    src = InputSource(path=tmp_path / "a.xyz")
    page.push_sources([src])
    last = page.activity_list.item(page.activity_list.count() - 1).text()
    assert "1" in last and "Files page" in last


def test_push_sources_with_empty_list_clears(page, tmp_path):
    src = InputSource(path=tmp_path / "a.xyz")
    page.push_sources([src])
    assert len(page.input_panel.sources()) == 1
    page.push_sources([])
    assert page.input_panel.sources() == []


# --- submit_requested signal ----------------------------------------------


def test_submit_click_emits_submit_requested(page, tmp_path, qtbot):
    xyz = tmp_path / "a.xyz"
    xyz.write_text("1\nmol\nH 0 0 0\n", encoding="utf-8")
    src = InputSource(path=xyz, side="local", kind="xyz")
    page.push_sources([src])
    # Set the XYZ path on the embedded InputBuilderWidget so the
    # input-mode validator passes.
    page._build_input_tab.set_xyz_path(xyz)

    with qtbot.waitSignal(page.submit_requested, timeout=500) as sig:
        page.submit_btn.click()

    payload = sig.args[0]
    assert payload.kind == "single"
    assert len(payload.inputs) == 1
    assert payload.inputs[0].path == xyz


def test_create_only_click_emits_create_only_requested(page, tmp_path, qtbot):
    xyz = tmp_path / "a.xyz"
    xyz.write_text("1\nmol\nH 0 0 0\n", encoding="utf-8")
    src = InputSource(path=xyz, side="local", kind="xyz")
    page.push_sources([src])
    page._build_input_tab.set_xyz_path(xyz)

    with qtbot.waitSignal(page.create_only_requested, timeout=500) as sig:
        page.create_only_btn.click()

    payload = sig.args[0]
    assert payload.kind == "single"


def test_confflow_mode_emits_confflow_kind(page, tmp_path, qtbot):
    xyz = tmp_path / "a.xyz"
    xyz.write_text("1\nmol\nH 0 0 0\n", encoding="utf-8")
    page.push_sources([InputSource(path=xyz, side="local", kind="xyz")])
    # Switch to Build workflow tab.
    page.mode_tabs.setCurrentIndex(1)

    with qtbot.waitSignal(page.submit_requested, timeout=500) as sig:
        page.submit_btn.click()

    payload = sig.args[0]
    assert payload.kind == "confflow"
    assert payload.workflow is not None
    assert payload.workflow.work_dir_name  # default value


# --- validation errors render in the activity log -------------------------


def test_submit_rejects_empty_inputs(page, qtbot):
    """No inputs selected — clicking Submit logs the validation error."""
    assert page.input_panel.sources() == []
    page.submit_btn.click()
    # Look at the activity log; expect at least one Validation [inputs]: line.
    log_texts = [page.activity_list.item(i).text() for i in range(page.activity_list.count())]
    assert any("inputs" in t for t in log_texts), f"missing inputs validation: {log_texts}"


def test_submit_xyz_path_required_when_inputs_present(page, tmp_path):
    """If inputs are present but the XYZ field is empty, validation logs it."""
    src = InputSource(path=tmp_path / "a.xyz")
    page.push_sources([src])
    # Do NOT set the XYZ path on the input builder.
    page.submit_btn.click()
    log_texts = [page.activity_list.item(i).text() for i in range(page.activity_list.count())]
    assert any("xyz" in t.lower() for t in log_texts), f"missing xyz validation: {log_texts}"


def test_confflow_mode_requires_calc_fields(page, tmp_path, qtbot):
    """Workflow mode without a method/basis should fail calc validation."""
    page.push_sources([InputSource(path=tmp_path / "a.xyz")])
    page.mode_tabs.setCurrentIndex(1)
    # Clear method / basis so calc validation fails.
    page._calc_widget.method_edit.clear()
    page._calc_widget.basis_edit.clear()

    page.submit_btn.click()
    log_texts = [page.activity_list.item(i).text() for i in range(page.activity_list.count())]
    assert any("method" in t for t in log_texts) or any("basis" in t for t in log_texts)


# --- server status / pill -------------------------------------------------


def test_server_pill_text_default_is_no_server(page):
    assert page.server_pill.text() == "No server"


def test_set_server_id_updates_pill(page):
    page.set_server_id("hpc-1")
    assert "hpc-1" in page.server_pill.text()


def test_set_server_status_toggles_remote_tab(page):
    """set_server_status(connected=True) adds the Remote tab; False removes it."""
    assert page.input_panel.remote_tab is None  # default: no remote
    page.set_server_status(connected=True, server_label="hpc")
    assert page.input_panel.remote_tab is not None
    page.set_server_status(connected=False, server_label="hpc")
    assert page.input_panel.remote_tab is None


# --- max_parallel --------------------------------------------------------


def test_set_max_parallel_updates_spinbox(page):
    page.set_max_parallel(8)
    assert page.max_parallel_spin.value() == 8
    # And the value flows through to the emitted payload.
    captured = {}
    page.submit_requested.connect(lambda p: captured.setdefault("p", p))
    page.input_panel.set_sources([InputSource(path=Path("a.xyz"), side="local", kind="xyz")])
    page._build_input_tab.set_xyz_path(Path("a.xyz"))
    page.submit_btn.click()
    assert captured["p"].max_parallel == 8


# --- language switch ------------------------------------------------------


def test_apply_language_re_translates_submit_button(page):
    page.apply_language("zh")
    # ZH translation exists for "Submit".
    assert page.submit_btn.text() == "\u63d0\u4ea4"
    page.apply_language("en")
    assert page.submit_btn.text() == "Submit"


def test_apply_language_re_translates_max_parallel_label(page):
    # "Max parallel:" has a ZH translation in i18n.py.
    page.apply_language("zh")
    assert page.max_parallel_label.text() == "\u6700\u5927\u5e76\u53d1:"
    page.apply_language("en")
    assert page.max_parallel_label.text() == "Max parallel:"


# --- on_submission_result ------------------------------------------------


def test_on_submission_result_logs_success(page):
    payload = MagicMock(records=[MagicMock(run_id="run-001")], errors=[])
    page.on_submission_result(payload)
    last = page.activity_list.item(page.activity_list.count() - 1).text()
    assert "Submitted" in last
    assert "run-001" in last


def test_on_submission_result_logs_failure(page):
    payload = MagicMock(records=[], errors=["scheduler down"])
    page.on_submission_result(payload)
    last = page.activity_list.item(page.activity_list.count() - 1).text()
    assert "Submit failed" in last
    assert "scheduler down" in last


# --- live preview refresh -------------------------------------------------


def test_refresh_input_preview_writes_to_preview_pane(page, tmp_path):
    """Switching to the Build input tab and clicking Refresh renders the
    input file to the preview pane."""
    xyz = tmp_path / "a.xyz"
    xyz.write_text("1\nmol\nH 0 0 0\n", encoding="utf-8")
    page.push_sources([InputSource(path=xyz, side="local", kind="xyz")])
    page._build_input_tab.set_xyz_path(xyz)
    page._build_input_tab.set_output_path(tmp_path / "out.gjf")
    page._refresh_input_preview()
    assert "%chk" in page.preview.toPlainText() or "nproc" in page.preview.toPlainText()
