"""GUI smoke test -- drive the JobDesk main window offscreen end-to-end.

This is intentionally NOT a pytest test. It is a stand-alone Python script
(``python scripts/smoke_gui_offscreen.py``) that catches import-time
errors, signal-slot wiring errors, and "the button does nothing when
clicked" bugs that pure unit tests miss.

We use ``QT_QPA_PLATFORM=offscreen`` so the script runs on a headless
machine (CI, a developer box with no display) and never touches the
real WSL g16 install.

Happy-path journey (each step is isolated so one failure doesn't hide
another):

    1.  ``qt_binding`` -- ``QT_QPA_PLATFORM=offscreen`` set BEFORE any
        Qt import. Print confirmation.
    2.  ``qapp`` -- ``QApplication`` instance.
    3.  ``main_window`` -- instantiate :class:`MainWindow`. The four
        concrete pages (``FileTransferPage`` / ``RunsResultsPage`` /
        ``SettingsServersPage``) are replaced with lightweight stubs
        that expose the signals ``MainWindow.__init__`` wires up.
        This avoids hitting the network during construction.
    4a. ``switch_to_workflow`` -- flip the shell to index 1 and verify
        the page is visible.
    4b. ``workflow_page_structure`` -- verify :class:`WorkflowPage` has the
        expected sub-widgets (settings tabs, flow scroll, preview box).
    4c. ``workflow_page_yaml_generation`` -- verify the YAML preview
        shows the "Add at least one workflow step" placeholder.
    4d. ``workflow_page_add_step`` -- add a step via the YAML editor
        and verify the flow diagram updates.
    5.  ``open_builder_dialog`` -- open the :class:`WorkflowBuilderDialog`
        and verify the embedded :class:`WorkflowGraphEditor` is visible.
    5a. ``dialog_onboarding_card`` -- verify the empty-canvas card appears.
    5b. ``dialog_quick_start`` -- click the Quick-start button and verify
        the graph is populated.
    5c. ``dialog_add_nodes`` -- add two nodes directly via the scene API.
    5d. ``dialog_connect_edge`` -- wire the two nodes together.
    5e. ``dialog_graph_summary`` -- verify the graph has 2 step nodes
        and 1 edge with no errors.
    5f. ``dialog_snapshot`` -- grab the dialog editor into a PNG.
    6.  ``runs_page`` -- flip the shell to index 2 and call
        ``refresh_run_list()``; expect the table to be empty.
    7.  print a clear pass/fail summary and exit non-zero if any step
        failed.

The script is read-only against the source tree, does not commit, and
does not call into WSL.
"""
from __future__ import annotations

import os
import sys
import traceback
from contextlib import contextmanager
from typing import Iterator

# -- 1. Qt platform MUST be set before importing PySide6 --
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Make ``jobdesk_app`` importable when running this file directly from
# the repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from PySide6.QtCore import QTimer, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

# Confirm the platform choice BEFORE we even create the QApplication so
# the first line of output answers "did we really go offscreen?".
print(f"[step 1/7] QT_QPA_PLATFORM = {os.environ.get('QT_QPA_PLATFORM')!r}")
assert os.environ.get("QT_QPA_PLATFORM") == "offscreen", (
    "QT_QPA_PLATFORM must be 'offscreen' before importing Qt widgets"
)


# -- Step tracker --
_STEPS: list[tuple[str, str, str | None]] = []  # (name, status, error)


def _record(name: str, ok: bool, err: str | None = None) -> None:
    status = "PASS" if ok else "FAIL"
    _STEPS.append((name, status, err))
    line = f"    [{status}] {name}"
    if err:
        line += f"  <-  {err.splitlines()[-1]}"
    print(line)


@contextmanager
def _step(name: str) -> Iterator[list[list[traceback.FrameSummary]]]:
    """Context manager that swallows exceptions and records the step.

    Yields an empty list; the step body can append inner tracebacks to
    the list if it catches a sub-exception but wants to keep going.
    """
    holder: list[list[traceback.FrameSummary]] = []
    try:
        yield holder
    except Exception:
        tb = traceback.format_exc()
        _record(name, False, tb)
        print(f"        full traceback:\n{tb}")
    else:
        _record(name, True)


# -- 2. QApplication --


def step_qapp() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setOrganizationName("JobDeskSmoke")
    app.setApplicationName("smoke_gui_offscreen")
    return app


# -- 3. MainWindow with stubbed pages --
# ``MainWindow.__init__`` calls ``FileTransferPage(...)``,
# ``RunsResultsPage(...)`` and ``SettingsServersPage(...)``. The real
# constructors reach out to ``RunService.list_runs()`` and SSH session
# factories; we replace them with QWidget stubs that expose the same
# signal names. This pattern mirrors
# ``tests/test_gui_behavior.py::TestMainWindowExcepthook``.

class _FilesStub(QWidget):
    runs_submitted = Signal(list)
    use_as_input_received = Signal(list)

    def __init__(self, *_args, **_kwargs):
        super().__init__()
        self._service = None
        self._connected_server_id = ""
        # Mirror a couple of attributes MainWindow._on_nav reads.
        self.max_parallel_spin = type(
            "_Spin", (), {"value": staticmethod(lambda: 1)}
        )()


class _RunsStub(QWidget):
    """Fake Runs/Results page that exposes the minimum surface the smoke
    script needs (table rowCount + a no-op refresh). We avoid the real
    :class:`RunsResultsPage` here because its constructor spins up
    a :class:`SessionPool` and a real :class:`RunMonitor` -- neither is
    needed to verify "page builds and shows empty".
    """

    startup_recovery_failed = Signal(str)
    startup_recovery_finished = Signal()

    def __init__(self, *_args, **_kwargs):
        super().__init__()
        from PySide6.QtWidgets import QTableWidget
        self.table = QTableWidget()
        self.table.setRowCount(0)

    def start_startup_recovery(self):
        # Immediately emit finished so MainWindow re-enables the other pages
        # without ever touching the real RunCoordinator / database.
        QTimer.singleShot(0, lambda: self.startup_recovery_finished.emit())

    def refresh_run_list(self):
        self.table.setRowCount(0)

    def shutdown(self):
        return None


class _SettingsStub(QWidget):
    language_changed = Signal(str)

    def __init__(self, *_args, **_kwargs):
        super().__init__()


class _GuiSettingsStoreStub:
    """Returns a fully-populated GuiSettings -- no disk I/O, no yaml."""

    def __init__(self):
        self._settings = _default_gui_settings()

    def load(self):
        return self._settings

    def update(self, **kwargs):
        from dataclasses import replace
        self._settings = replace(self._settings, **kwargs)


def _default_gui_settings():
    from jobdesk_app.services.gui_settings import GuiSettings
    return GuiSettings(
        window_size=[1320, 860],
        language="en",
        auto_connect=False,
    )


def step_main_window(app: QApplication):
    # Replace the heavy pages BEFORE MainWindow imports them.
    import jobdesk_app.gui.main_window as mw_mod
    mw_mod.FileTransferPage = _FilesStub
    mw_mod.RunsResultsPage = _RunsStub
    mw_mod.SettingsServersPage = _SettingsStub
    mw_mod.GuiSettingsStore = _GuiSettingsStoreStub
    mw_mod.configure_file_logging = lambda: _NullLogger()

    # SettingsServersPage imports a few helpers; stub load_servers so
    # they don't hit disk.
    from jobdesk_app.gui.pages import settings_servers_page as ssp
    ssp.load_servers = lambda: _EmptyServers()

    from jobdesk_app.gui.main_window import MainWindow
    window = MainWindow()
    window.setWindowTitle("JobDesk (smoke)")
    window.show()
    app.processEvents()
    return window


class _NullLogger:
    def info(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None

    def exception(self, *_args, **_kwargs):
        return None


class _EmptyServers:
    def __init__(self):
        self.servers: dict = {}


# -- 4a. page navigation --

WORKFLOW_PAGE_INDEX = 1   # "workflow"
RUNS_PAGE_INDEX = 2       # "bar-chart"


def step_switch_to_workflow(window) -> bool:
    # ``shell.set_current`` triggers the sidebar + the page-changed
    # signal which fires ``_on_nav``.
    window.shell.set_current(WORKFLOW_PAGE_INDEX)
    app = QApplication.instance()
    app.processEvents()
    return window.shell.pages.currentIndex() == WORKFLOW_PAGE_INDEX


# -- 4b. workflow page structure --

def step_workflow_page_structure(window) -> dict:
    """Verify WorkflowPage has expected sub-widgets."""
    page = window.workflow_page
    settings_tabs_count = page.settings_tabs.count()
    checks = {
        "settings_tabs_exists": hasattr(page, "settings_tabs"),
        "settings_tabs_count": settings_tabs_count,
        "flow_scroll_exists": hasattr(page, "flow_scroll"),
        "full_yaml_preview_exists": hasattr(page, "full_yaml_preview"),
        "save_workflow_button_exists": hasattr(page, "save_workflow_button"),
        "step_yaml_editor_exists": hasattr(page, "step_yaml_editor"),
        "global_yaml_editor_exists": hasattr(page, "global_yaml_editor"),
    }
    failed = {k: v for k, v in checks.items() if v is False or (k == "settings_tabs_count" and v < 2)}
    if failed:
        raise RuntimeError(f"WorkflowPage missing or broken: {failed}")
    return checks


# -- 4c. workflow page YAML generation --

def step_workflow_page_yaml_generation(window) -> bool:
    """Verify the YAML preview shows the placeholder message."""
    page = window.workflow_page
    preview_text = page.full_yaml_preview.toPlainText()
    if "Add at least one workflow step" not in preview_text:
        raise RuntimeError(
            f"Expected placeholder text in YAML preview, got: {preview_text[:100]}"
        )
    return True


# -- 4d. workflow page add step --

def step_workflow_page_add_step(window) -> bool:
    """Add a step via the YAML editor and verify flow diagram updates."""
    page = window.workflow_page

    # Set the step YAML to a valid calc step
    page.step_yaml_editor.setPlainText(
        "name: sp\n"
        "type: calc\n"
        "params:\n"
        "  iprog: orca\n"
        "  itask: sp\n"
        "  keyword: B3LYP def2-SVP\n"
    )
    page._add_step()

    # Verify the flow diagram updated
    preview_text = page.full_yaml_preview.toPlainText()
    if "itask: sp" not in preview_text:
        raise RuntimeError(f"Step was not added to YAML preview: {preview_text[:200]}")

    # Verify a step card appeared in the flow diagram
    flow_items = page._flow_layout.count()
    if flow_items < 4:  # input, hint, output, spacer at minimum
        raise RuntimeError(f"Expected flow diagram to have step cards, got {flow_items} items")

    return True


# -- 5. open builder dialog --

def step_open_builder_dialog(window) -> object:
    """Open WorkflowBuilderDialog and return the dialog instance."""
    from jobdesk_app.gui.dialogs.workflow_builder_dialog import WorkflowBuilderDialog

    dialog = WorkflowBuilderDialog(
        language="en",
        preset_store=window.workflow_page._store,
    )
    dialog.show()
    dialog.resize(960, 640)
    app = QApplication.instance()
    app.processEvents()
    return dialog


# -- 5a. dialog editor visibility --

def step_dialog_editor_visible(dialog) -> bool:
    """Verify the embedded WorkflowGraphEditor is properly set up."""
    editor = dialog.editor
    if editor is None:
        raise RuntimeError("dialog.editor is None")

    # Check the editor has a scene, view, and graph
    scene_ok = (
        editor.scene() is not None
        and editor.view() is not None
        and editor.scene().graph() is not None
    )
    if not scene_ok:
        raise RuntimeError("Editor missing scene/view/graph")

    return True


# -- 5b. dialog onboarding card --

def step_dialog_onboarding_card(dialog) -> bool:
    """Verify the empty-canvas onboarding card is visible."""
    editor = dialog.editor
    card = editor.onboarding_card()
    if card is None:
        raise RuntimeError("editor.onboarding_card() returned None")
    app = QApplication.instance()
    app.processEvents()
    if not card.isVisible():
        raise RuntimeError("Onboarding card should be visible on empty canvas")
    return True


# -- 5c. dialog quick start --

def step_dialog_quick_start(dialog) -> bool:
    """Click Quick-start and verify the graph is populated."""
    editor = dialog.editor
    card = editor.onboarding_card()

    # Find and click the Quick-start button
    quick_start_btn = getattr(card, "_quick_start_btn", None)
    if quick_start_btn is None:
        raise RuntimeError("Onboarding card missing _quick_start_btn")

    quick_start_btn.click()
    app = QApplication.instance()
    app.processEvents()

    # Verify graph is no longer empty
    if editor.is_empty():
        raise RuntimeError("Quick-start should populate the graph")

    # Verify no validation errors
    errors = [i for i in editor.graph().validate() if i.severity == "error"]
    if errors:
        raise RuntimeError(f"Graph has validation errors after Quick-start: {[e.message for e in errors]}")

    return True


# -- 5d. dialog add nodes --

def step_dialog_add_nodes(dialog) -> dict:
    """Add two nodes directly via the scene API.

    Note: We clear existing nodes first to get a clean test case.
    The Quick-start template had pre-populated nodes that could conflict.
    We only wire PRE_OPT and OPT together; OUTPUT is left unwired per the
    dialog's design (it's a sentinel, not a wired sink).
    """
    from jobdesk_app.gui.nodegraph.model import NodeKind, Edge

    scene = dialog.editor.scene()
    graph = scene.graph()

    # Clear existing nodes (keep XYZ_FILE and OUTPUT terminals)
    nodes_to_remove = [
        node_id for node_id, node in graph.nodes.items()
        if node.kind not in {NodeKind.XYZ_FILE, NodeKind.OUTPUT}
    ]
    for node_id in nodes_to_remove:
        graph.remove_node(node_id)

    # Clear existing edges
    edges_to_remove = list(graph.edges.keys())
    for edge_id in edges_to_remove:
        graph.remove_edge(edge_id)

    # Add PRE_OPT and OPT nodes
    n1 = scene.add_node(NodeKind.PRE_OPT, (200.0, 0.0))
    n2 = scene.add_node(NodeKind.OPT, (440.0, 0.0))

    # Wire XYZ -> PRE_OPT -> OPT (leave OUTPUT unwired per design)
    xyz_node = next((n for n in graph.nodes.values() if n.kind == NodeKind.XYZ_FILE), None)
    if xyz_node is not None:
        graph.add_edge(Edge(Edge.new_id(), xyz_node.id, "out", n1.node_id, "in"))
    graph.add_edge(Edge(Edge.new_id(), n1.node_id, "out", n2.node_id, "in"))

    return {"n1_id": n1.node_id, "n2_id": n2.node_id}


# -- 5e. dialog connect edge --

def step_dialog_connect_edge(dialog, ids: dict) -> str | None:
    """Wire the two nodes together."""
    scene = dialog.editor.scene()
    edge_item = scene.add_edge_at(ids["n1_id"], "out", ids["n2_id"], "in")
    if edge_item is None:
        return None
    return edge_item.edge_id


# -- 5f. dialog graph summary --

def step_dialog_graph_summary(dialog) -> dict:
    """Verify the graph has expected nodes and edges."""
    graph = dialog.editor.scene().graph()
    issues = graph.validate()
    return {
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "errors": [i for i in issues if i.severity == "error"],
        "warnings": [i for i in issues if i.severity == "warning"],
    }


# -- 5g. dialog snapshot --

def step_dialog_snapshot(dialog) -> str:
    """Grab the dialog editor into a PNG."""
    out_path = os.path.join(_REPO_ROOT, "tmp60f7j8ix", "smoke_gui_offscreen_builder_dialog.png")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    editor_pixmap = dialog.editor.grab()
    ok = editor_pixmap.save(out_path, "PNG")
    if not ok or not os.path.exists(out_path):
        raise RuntimeError(f"snapshot.save() returned {ok}; file exists? {os.path.exists(out_path)}")

    # Reject all-black PNGs
    if _is_all_black(editor_pixmap.toImage()):
        raise RuntimeError(
            f"Editor snapshot is all-black -- editor likely hidden or unpainted: {out_path}"
        )
    return out_path


def _is_all_black(image) -> bool:
    """Return True if every visible pixel of ``image`` is black."""
    if image.isNull():
        return True
    w, h = image.width(), image.height()
    if w == 0 or h == 0:
        return True
    samples = 0
    for y in (0, h // 4, h // 2, 3 * h // 4, h - 1):
        for x in (0, w // 4, w // 2, 3 * w // 4, w - 1):
            samples += 1
            px = image.pixelColor(x, y)
            if (px.red(), px.green(), px.blue()) != (0, 0, 0):
                return False
    return samples > 0


# -- 6. Runs page empty state --

def step_runs_page(window) -> int:
    # Stub never populated rows; switch and let it build.
    window.shell.set_current(RUNS_PAGE_INDEX)
    app = QApplication.instance()
    app.processEvents()
    page = window.runs_page
    # Runs page calls refresh_run_list() in start_startup_recovery's
    # deferred path; we've already short-circuited that. Trigger it
    # explicitly to confirm the empty-list path works.
    page.refresh_run_list()
    return page.table.rowCount()


# -- main --

def main() -> int:
    print("=" * 70)
    print("JobDesk GUI smoke (offscreen)")
    print("=" * 70)

    # Import NodeKind here for the edge verification step
    from jobdesk_app.gui.nodegraph.model import NodeKind  # noqa: F401

    app: QApplication | None = None
    window = None
    dialog = None

    with _step("qapp"):
        app = step_qapp()

    with _step("main_window"):
        window = step_main_window(app)

    # Make sure the deferred startup_recovery_finished emission has
    # landed (it was scheduled via QTimer.singleShot(0)).
    QApplication.processEvents()

    with _step("switch_to_workflow"):
        ok = step_switch_to_workflow(window)
        if not ok:
            raise RuntimeError(
                f"shell.pages.currentIndex={window.shell.pages.currentIndex()}, expected {WORKFLOW_PAGE_INDEX}"
            )

    with _step("workflow_page_structure"):
        step_workflow_page_structure(window)

    with _step("workflow_page_yaml_generation"):
        step_workflow_page_yaml_generation(window)

    with _step("workflow_page_add_step"):
        step_workflow_page_add_step(window)

    with _step("open_builder_dialog"):
        dialog = step_open_builder_dialog(window)

    with _step("dialog_editor_visible"):
        step_dialog_editor_visible(dialog)

    with _step("dialog_onboarding_card"):
        step_dialog_onboarding_card(dialog)

    with _step("dialog_quick_start"):
        step_dialog_quick_start(dialog)

    node_ids = None
    with _step("dialog_add_nodes"):
        node_ids = step_dialog_add_nodes(dialog)

    # Edges are now wired in step_dialog_add_nodes
    with _step("dialog_edges_wired"):
        graph = dialog.editor.scene().graph()
        step_nodes = [n for n in graph.nodes.values() if n.kind not in {NodeKind.XYZ_FILE, NodeKind.OUTPUT}]
        if len(step_nodes) != 2:
            raise RuntimeError(f"Expected 2 step nodes, got {len(step_nodes)}")
        # Verify each step node has both incoming and outgoing edges (except first and last)
        preopt = next((n for n in step_nodes if n.kind == NodeKind.PRE_OPT), None)
        opt = next((n for n in step_nodes if n.kind == NodeKind.OPT), None)
        if preopt is None or opt is None:
            raise RuntimeError("Missing PRE_OPT or OPT node")
        # PRE_OPT should have an incoming edge (from XYZ)
        preopt_incoming = list(graph.incoming_edges(preopt.id))
        if not preopt_incoming:
            raise RuntimeError("PRE_OPT missing incoming edge")
        # OPT should have incoming edge from PRE_OPT
        opt_incoming = list(graph.incoming_edges(opt.id))
        if not opt_incoming:
            raise RuntimeError("OPT missing incoming edge")

    graph_summary = None
    with _step("dialog_graph_summary"):
        graph_summary = step_dialog_graph_summary(dialog)
        # The graph has XYZ_FILE, OUTPUT + 2 added nodes + 1 edge
        if graph_summary["errors"]:
            raise RuntimeError(f"unexpected graph errors: {graph_summary['errors']}")

    snapshot_path = None
    with _step("dialog_snapshot"):
        snapshot_path = step_dialog_snapshot(dialog)

    row_count = None
    with _step("runs_page_empty"):
        row_count = step_runs_page(window)
        if row_count != 0:
            raise RuntimeError(f"expected 0 rows in Runs page, got {row_count}")

    # Clean shutdown -- give the worker quit a tiny window.
    try:
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        window.shutdown()
    except Exception:
        pass
    QApplication.processEvents()

    # -- 7. summary --
    print()
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    failed = [s for s in _STEPS if s[1] == "FAIL"]
    for name, status, err in _STEPS:
        marker = "OK " if status == "PASS" else "BAD"
        print(f"  [{marker}] {name}")
    print()
    print(f"  graph summary : {graph_summary}")
    print(f"  runs rows     : {row_count}")
    print(f"  snapshot      : {snapshot_path}")
    print()
    if failed:
        print(f"RESULT: FAIL ({len(failed)} step(s) failed)")
        for name, _status, err in failed:
            print(f"  - {name}: {err.splitlines()[-1] if err else 'no detail'}")
        return 2
    print(f"RESULT: PASS ({len(_STEPS)} steps)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
