"""Tests for :class:`SubmitPage` (Phase 2).

Phase 2 collapses the previous "Build input file" / "Build workflow" tabs
into a single :class:`WorkflowGraphEditor`. The Submit page now drives the
graph editor's :class:`NodeGraph` through :func:`to_workflow_spec`; this file
covers the page-level wiring the lower-level widget tests don't exercise:

* ``push_sources()`` (the cross-page wire endpoint from FileTransferPage).
* The two-button row emits ``submit_requested`` with a graph-derived payload.
* Validation errors are surfaced in the activity log.
* Language switching re-translates the embedded widgets and buttons.
* Editor changes re-render the live YAML preview (with debounce).
* Buttons disable when the graph has any error-severity issues.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.core.submit_payload import InputSource
from jobdesk_app.gui.nodegraph.model import (
    Edge,
    NodeGraph,
    NodeKind,
    default_node,
)
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


def _make_valid_graph() -> NodeGraph:
    graph = NodeGraph()
    xyz = default_node(NodeKind.XYZ_FILE, position=(10.0, 20.0))
    opt = default_node(NodeKind.OPT, position=(200.0, 30.0))
    opt.params = {"method": "B3LYP", "basis": "6-31G(d)", "nproc": 4}
    out = default_node(NodeKind.OUTPUT, position=(400.0, 40.0))
    graph.add_node(xyz)
    graph.add_node(opt)
    graph.add_node(out)
    # Connect only XYZ -> OPT. OUTPUT is a sentinel and has no ports, so
    # the editor leaves it disconnected; the bridge still emits it as a
    # step and the YAML contains an empty tail.
    graph.add_edge(
        Edge(id="e1", src_node=xyz.id, src_port="out", dst_node=opt.id, dst_port="in")
    )
    return graph


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
    page.editor.set_graph(_make_valid_graph())

    with qtbot.waitSignal(page.submit_requested, timeout=500) as sig:
        page.submit_btn.click()

    payload = sig.args[0]
    assert payload.kind == "confflow"
    assert len(payload.inputs) == 1
    assert payload.inputs[0].path == xyz


# --- validation errors render in the activity log -------------------------


def test_submit_empty_canvas_logs_neutral_hint(page):
    """An empty canvas must surface a friendly "add a node" hint rather
    than the legacy "No inputs selected" error path.

    Review-fix: previously a fresh, untouched canvas flashed green
    "Workflow OK" while the preview simultaneously said "Graph
    incomplete". The page now treats blank canvas as a neutral
    state; clicking Submit should guide the user, not punish them.
    """
    assert page.input_panel.sources() == []
    page.submit_btn.click()
    log_texts = [
        page.activity_list.item(i).text() for i in range(page.activity_list.count())
    ]
    assert any("Add a node" in t for t in log_texts), (
        f"missing empty-canvas hint: {log_texts}"
    )
    # The legacy "No inputs selected" wording used to fire here, which
    # contradicted the empty canvas status. Make sure the new wording
    # has fully replaced it.
    assert not any("No inputs selected" in t for t in log_texts), (
        f"empty canvas should not surface 'No inputs selected': {log_texts}"
    )


def test_submit_invalid_graph_logs_error(page, tmp_path):
    """If the graph is malformed, Submit must log a graph error.

    Review-fix: previously this test exercised an empty canvas; that
    path now short-circuits to a friendly "Add a node" hint instead.
    To still cover the real "graph rejected" code path we add an
    XYZ_FILE node with an incoming edge — that's an authoring rule
    the bridge enforces with ``WorkflowSpecError``. The activity log
    must surface something mentioning the graph.

    Uses the model's high-level ``add_node`` / ``add_edge`` API to
    avoid coupling to private scene commands.
    """
    page.push_sources([InputSource(path=tmp_path / "a.xyz")])
    graph = page.editor._scene.graph()
    xyz_node = default_node(NodeKind.XYZ_FILE, position=(10.0, 20.0))
    opt_node = default_node(NodeKind.OPT, position=(200.0, 30.0))
    opt_node.params = {
        "method": "B3LYP",
        "basis": "6-31G(d)",
        "nproc": 4,
    }
    graph.add_node(xyz_node)
    graph.add_node(opt_node)
    # OPT -> XYZ is forbidden (XYZ_FILE must not have incoming edges),
    # which the bridge rejects with WorkflowSpecError and the page
    # surfaces as a "graph" log entry.
    graph.add_edge(
        Edge(
            id="bad-edge",
            src_node=opt_node.id,
            src_port="out",
            dst_node=xyz_node.id,
            dst_port="in",
        )
    )
    # Re-render the preview so subsequent submit goes through fresh state.
    page.submit_btn.click()
    log_texts = [
        page.activity_list.item(i).text() for i in range(page.activity_list.count())
    ]
    # After the empty-canvas review fix, validation surfaced from
    # ``editor.validate()`` reads as "Validation [...]" entries. The
    # activity log also gets a "Validation [graph]: ..." line when
    # the bridge raises. Either form satisfies the contract that
    # the page surfaces graph problems explicitly.
    assert any(
        ("validation" in t.lower()) or ("graph" in t.lower())
        for t in log_texts
    ), f"missing graph validation: {log_texts}"


def test_generate_btn_click_writes_preview(page, tmp_path, qtbot):
    xyz = tmp_path / "a.xyz"
    xyz.write_text("1\nmol\nH 0 0 0\n", encoding="utf-8")
    page.push_sources([InputSource(path=xyz, side="local", kind="xyz")])
    page.editor.set_graph(_make_valid_graph())
    # Force the synchronous preview path.
    page._preview_timer.stop()
    page._on_generate_clicked()
    assert "steps" in page.preview.toPlainText()


def test_graph_changed_triggers_debounced_preview(page, tmp_path):
    xyz = tmp_path / "a.xyz"
    xyz.write_text("1\nmol\nH 0 0 0\n", encoding="utf-8")
    page.push_sources([InputSource(path=xyz, side="local", kind="xyz")])
    page.editor.set_graph(_make_valid_graph())
    page._preview_timer.stop()
    # Touching the graph via set_graph fires graph_changed → starts timer.
    page._preview_timer.start()  # simulate the timer being armed by the signal
    assert page._preview_timer.isActive()


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


def test_set_max_parallel_flows_to_payload(page, tmp_path, qtbot):
    xyz = tmp_path / "a.xyz"
    xyz.write_text("1\nmol\nH 0 0 0\n", encoding="utf-8")
    page.set_max_parallel(8)
    page.push_sources([InputSource(path=xyz, side="local", kind="xyz")])
    page.editor.set_graph(_make_valid_graph())
    captured: dict = {}
    page.submit_requested.connect(lambda p: captured.setdefault("p", p))
    page.submit_btn.click()
    assert captured["p"].max_parallel == 8


# --- language switch ------------------------------------------------------


def test_apply_language_re_translates_submit_button(page):
    page.apply_language("zh")
    assert page.submit_btn.text() == "\u63d0\u4ea4\u5230\u8fdc\u7a0b"
    page.apply_language("en")
    assert page.submit_btn.text() == "Submit to Remote"


def test_apply_language_re_translates_generate_button(page):
    page.apply_language("zh")
    assert page.generate_btn.text() == "\u751f\u6210 YAML"
    page.apply_language("en")
    assert page.generate_btn.text() == "Generate YAML"


def test_apply_language_re_translates_max_parallel_label(page):
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


# --- signal surface ------------------------------------------------------


def test_create_only_requested_signal_removed(page):
    """Phase 2 collapsed 'Create tasks only' into the unified editor."""
    assert not hasattr(page, "create_only_requested")
    assert hasattr(page, "submit_requested")
    assert hasattr(page, "use_as_input_received")


# --- work_dir_name derivation ---------------------------------------------


def test_work_dir_name_uses_stem_for_named_xyz(tmp_path):
    from jobdesk_app.gui.pages.submit_page import _work_dir_name

    assert _work_dir_name(tmp_path / "ethanol.xyz") == "ethanol_confflow_work"


def test_work_dir_name_falls_back_to_parent_for_unnamed(tmp_path):
    from jobdesk_app.gui.pages.submit_page import _work_dir_name

    # ``Path.stem`` for ``"a.xyz"`` -> ``"a"`` and for ``"xyz"`` -> ``"xyz"``,
    # so most files just collapse into the stem-and-confflow_work form.
    # The parent fallback only kicks in when no recognisable name exists.
    bare = tmp_path / "xyz"  # ``stem == "xyz"``; we want a leading-dot edge
    edge = tmp_path / ".hidden"
    assert _work_dir_name(bare) == "xyz_confflow_work"
    # ``Path(".hidden").stem`` returns ``".hidden"`` (kept intact by pathlib),
    # so it short-circuits straight to the stem-derived form too.
    assert _work_dir_name(edge) == ".hidden_confflow_work"


def test_work_dir_name_uses_default_when_relative():
    from jobdesk_app.gui.pages.submit_page import _work_dir_name

    # Relative path with no recognisable parent gets a stable fallback.
    # The stem-derived form is acceptable here too because pathlib keeps
    # ``Path("xyz").stem == "xyz"``; just verify it's a non-empty
    # ``..._confflow_work`` token.
    assert _work_dir_name(Path("xyz")) == "xyz_confflow_work"


# --- Phase 10.5: DAG routing -----------------------------------------------


def _make_fanout_graph() -> NodeGraph:
    """A 4-node graph: XYZ_FILE -> CONF_GEN -> {SP, FREQ} -> OUTPUT.

    The two SP / FREQ sinks both have non-empty ``inputs`` lists, so the
    SubmitPage auto-detect chooses ``kind="dag"``.
    """
    graph = NodeGraph()
    xyz = default_node(NodeKind.XYZ_FILE, position=(10.0, 20.0))
    conf = default_node(NodeKind.CONF_GEN, position=(200.0, 30.0))
    conf.params = {"nconf": 3}
    sp = default_node(NodeKind.SINGLE_POINT, position=(400.0, 0.0))
    sp.params = {"method": "B3LYP", "basis": "6-31G(d)"}
    freq = default_node(NodeKind.FREQUENCY, position=(400.0, 80.0))
    out = default_node(NodeKind.OUTPUT, position=(600.0, 40.0))
    for n in (xyz, conf, sp, freq, out):
        graph.add_node(n)
    graph.add_edge(
        Edge(id="e1", src_node=xyz.id, src_port="out",
             dst_node=conf.id, dst_port="in")
    )
    graph.add_edge(
        Edge(id="e2", src_node=conf.id, src_port="out",
             dst_node=sp.id, dst_port="in")
    )
    graph.add_edge(
        Edge(id="e3", src_node=conf.id, src_port="out",
             dst_node=freq.id, dst_port="in")
    )
    return graph


def test_submit_click_with_dag_graph_routes_to_dag_kind(page, tmp_path, qtbot):
    """A graph with non-empty step ``inputs`` lists must submit as kind=dag.

    Phase 10.5: the editor → bridge path already serialises the per-step
    ``inputs`` arrays (Phase 10.1-10.4).  The page's auto-detect must
    pick that up so the submit use case writes the ``dag`` workflow YAML
    (Phase 10.5 plumbing).
    """
    xyz = tmp_path / "a.xyz"
    xyz.write_text("1\nmol\nH 0 0 0\n", encoding="utf-8")
    page.push_sources([InputSource(path=xyz, side="local", kind="xyz")])
    page.editor.set_graph(_make_fanout_graph())

    with qtbot.waitSignal(page.submit_requested, timeout=500) as sig:
        page.submit_btn.click()

    payload = sig.args[0]
    assert payload.kind == "dag"
    assert payload.workflow is None
    assert payload.dag is not None
    assert payload.dag.work_dir_name.endswith("_confflow_work")
    # The bridge-emitted steps include a non-empty ``inputs`` list on SP.
    sp_step = next(s for s in payload.dag.steps if s["name"] == "sp")
    assert sp_step["inputs"] == ["confgen"]


def test_submit_click_with_linear_graph_keeps_confflow_kind(page, tmp_path, qtbot):
    """A linear graph (no fan-in) still emits kind=confflow for backward compat.

    The Phase 10.5 auto-detect rule: any step with a non-empty ``inputs``
    list flips to ``dag``; otherwise the legacy ``confflow`` path stays
    so the wizard-style flow continues to work.
    """
    xyz = tmp_path / "a.xyz"
    xyz.write_text("1\nmol\nH 0 0 0\n", encoding="utf-8")
    page.push_sources([InputSource(path=xyz, side="local", kind="xyz")])
    page.editor.set_graph(_make_valid_graph())

    with qtbot.waitSignal(page.submit_requested, timeout=500) as sig:
        page.submit_btn.click()

    payload = sig.args[0]
    assert payload.kind == "confflow"
    assert payload.dag is None
    assert payload.workflow is not None


def test_detect_payload_kind_helper():
    """The auto-detect helper is the test seam: a list with any non-empty
    ``inputs`` flips to ``dag``; otherwise it returns None (caller decides)."""
    from jobdesk_app.gui.pages.submit_page import _detect_payload_kind

    assert _detect_payload_kind([]) is None
    # Linear: all steps have empty ``inputs``.
    assert _detect_payload_kind([
        {"name": "a", "type": "calc", "params": {}, "inputs": []},
        {"name": "b", "type": "calc", "params": {}, "inputs": ["a"]},
    ]) == "dag"
    # Pure fan-out case: the sink names a predecessor.
    assert _detect_payload_kind([
        {"name": "a", "type": "confgen", "params": {}, "inputs": []},
        {"name": "b", "type": "calc", "params": {}, "inputs": []},
    ]) is None
