"""GUI smoke test — drive the JobDesk main window offscreen end-to-end.

This is intentionally NOT a pytest test. It is a stand-alone Python script
(``python scripts/smoke_gui_offscreen.py``) that catches import-time
errors, signal-slot wiring errors, and "the button does nothing when
clicked" bugs that pure unit tests miss.

We use ``QT_QPA_PLATFORM=offscreen`` so the script runs on a headless
machine (CI, a developer box with no display) and never touches the
real WSL g16 install.

Happy-path journey (each step is isolated so one failure doesn't hide
another):

    1.  ``qt_binding`` — ``QT_QPA_PLATFORM=offscreen`` set BEFORE any
        Qt import. Print confirmation.
    2.  ``qapp`` — ``QApplication`` instance.
    3.  ``main_window`` — instantiate :class:`MainWindow`. The four
        concrete pages (``FileTransferPage`` / ``RunsResultsPage`` /
        ``SettingsServersPage``) are replaced with lightweight stubs
        that expose the signals ``MainWindow.__init__`` wires up.
        This avoids hitting the network during construction.
    4a. ``switch_to_submit`` — flip the shell to index 1 and verify
        the page is visible.
    4b. ``editor_visible`` — verify ``SubmitPage.editor`` is visible.
    4c. ``add_two_nodes`` — call ``editor.scene().add_node(...)`` twice
        for compatible kinds (``XYZ_FILE`` → ``PRE_OPT`` → ``OPT``).
    4d. ``connect_edge`` — call ``editor.scene().add_edge_at(...)`` to
        wire ``XYZ_FILE.out → PRE_OPT.in``.
    4e. ``graph_summary`` — verify the underlying ``NodeGraph`` has
        2 nodes and 1 edge and 0 errors that block a normal flow.
    4f. ``runs_page`` — flip the shell to index 2 and call
        ``refresh_run_list()``; expect the table to be empty (no
        runs in this workspace).
    4g. ``snapshot`` — flip back to Submit, grab the Submit page into
        ``tmp60f7j8ix/smoke_gui_offscreen_submit.png``.
    5.  print a clear pass/fail summary and exit non-zero if any step
        failed.

The script is read-only against the source tree, does not commit, and
does not call into WSL.
"""
from __future__ import annotations

import os
import sys
import traceback
from contextlib import contextmanager
from typing import Callable, Iterator

# ── 1. Qt platform MUST be set before importing PySide6 ───────────────────
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


# ── Step tracker ─────────────────────────────────────────────────────────
_STEPS: list[tuple[str, str, str | None]] = []  # (name, status, error)


def _record(name: str, ok: bool, err: str | None = None) -> None:
    status = "PASS" if ok else "FAIL"
    _STEPS.append((name, status, err))
    line = f"    [{status}] {name}"
    if err:
        line += f"  ←  {err.splitlines()[-1]}"
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
    except Exception as exc:
        tb = traceback.format_exc()
        _record(name, False, tb)
        print(f"        full traceback:\n{tb}")
    else:
        _record(name, True)


# ── 2. QApplication ──────────────────────────────────────────────────────


def step_qapp() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setOrganizationName("JobDeskSmoke")
    app.setApplicationName("smoke_gui_offscreen")
    return app


# ── 3. MainWindow with stubbed pages ─────────────────────────────────────
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
    a :class:`SessionPool` and a real :class:`RunMonitor` — neither is
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
    """Returns a fully-populated GuiSettings — no disk I/O, no yaml."""

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


# ── 4a / 4b. page navigation + editor visibility ─────────────────────────

SUBMIT_PAGE_INDEX = 1   # "rocket"
RUNS_PAGE_INDEX = 2     # "bar-chart"


def step_switch_to_submit(window) -> bool:
    # ``shell.set_current`` triggers the sidebar + the page-changed
    # signal which fires ``_on_nav``.
    window.shell.set_current(SUBMIT_PAGE_INDEX)
    app = QApplication.instance()
    app.processEvents()
    return window.shell.pages.currentIndex() == SUBMIT_PAGE_INDEX


def step_editor_visible(window) -> bool:
    editor = window.submit_page.editor
    if editor is None:
        raise RuntimeError("editor attribute is None")
    # ``WorkflowGraphEditor`` is a plain :class:`QWidget` embedded as a
    # child of the SubmitPage's VBoxLayout (Phase 11.1 — was a
    # ``QMainWindow`` before, which Qt refused to embed silently). The
    # smoke-level guarantee we need is:
    #   1. parented to the SubmitPage (catches missing wiring),
    #   2. NOT explicitly hidden (catches explicit hide() regressions),
    #   3. visible to its parent (catches the embed-as-window regression
    #      that this smoke was missing),
    #   4. has a valid geometry with positive area (catches zero-sized
    #      or never-laid-out widgets),
    #   5. has a live scene + view + graph underneath (catches a
    #      constructor regression that drops one of those members),
    #   6. the snapshot PNG is not all-black (catches "geometry OK but
    #      nothing was painted").
    actual_parent = editor.parent()
    parent_ok = actual_parent is window.submit_page
    visible_to_parent = editor.isVisibleTo(actual_parent) if actual_parent is not None else False
    geom = editor.geometry()
    geometry_ok = geom.isValid() and geom.width() > 0 and geom.height() > 0
    scene_ok = (
        editor.scene() is not None
        and editor.view() is not None
        and editor.scene().graph() is not None
    )
    if not (parent_ok and visible_to_parent and geometry_ok and scene_ok):
        diag = {
            "parent_is_submit_page": parent_ok,
            "actual_parent_type": type(actual_parent).__name__ if actual_parent is not None else "None",
            "is_hidden": editor.isHidden(),
            "is_visible_to_parent": visible_to_parent,
            "geometry": [geom.x(), geom.y(), geom.width(), geom.height()],
            "geometry_ok": geometry_ok,
            "scene_ok": scene_ok,
        }
        raise RuntimeError(f"editor not properly attached: {diag}")
    return True


# ── 4c. add two nodes ────────────────────────────────────────────────────

def step_add_two_nodes(window):
    from jobdesk_app.gui.nodegraph.model import NodeKind

    scene = window.submit_page.editor.scene()
    graph = scene.graph()

    # XYZ_FILE is a source: out=STRUCTURE. PRE_OPT accepts STRUCTURE in.
    n1 = scene.add_node(NodeKind.XYZ_FILE, (-200.0, 0.0))
    n2 = scene.add_node(NodeKind.PRE_OPT, (200.0, 0.0))

    n1_id = n1.node_id
    n2_id = n2.node_id
    return {"n1": n1_id, "n2": n2_id}


# ── 4d. connect with an edge ─────────────────────────────────────────────

def step_connect_edge(window, ids: dict) -> str | None:
    scene = window.submit_page.editor.scene()
    edge_item = scene.add_edge_at(ids["n1"], "out", ids["n2"], "in")
    if edge_item is None:
        return None
    return edge_item.edge_id


# ── 4e. summary ──────────────────────────────────────────────────────────

def step_graph_summary(window) -> dict:
    graph = window.submit_page.editor.scene().graph()
    issues = graph.validate()
    return {
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "errors": [i for i in issues if i.severity == "error"],
        "warnings": [i for i in issues if i.severity == "warning"],
    }


# ── 4f. Runs page empty state ────────────────────────────────────────────

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


# ── 4g. snapshot ─────────────────────────────────────────────────────────

def step_snapshot(window) -> str:
    window.shell.set_current(SUBMIT_PAGE_INDEX)
    app = QApplication.instance()
    app.processEvents()
    out_path = os.path.join(_REPO_ROOT, "tmp60f7j8ix", "smoke_gui_offscreen_submit.png")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # Grab the Submit page first (cheap), then the editor itself so we
    # also verify the editor's paint pipeline produced something
    # non-trivial. Phase 11.1 caught a regression where the editor
    # window was hidden and the page-grab produced a pure-black PNG.
    submit_pixmap = window.submit_page.grab()
    editor_pixmap = window.submit_page.editor.grab()
    ok_submit = submit_pixmap.save(out_path, "PNG")
    editor_path = os.path.join(_REPO_ROOT, "tmp60f7j8ix", "smoke_gui_offscreen_editor.png")
    ok_editor = editor_pixmap.save(editor_path, "PNG")
    if not ok_submit or not os.path.exists(out_path):
        raise RuntimeError(f"snapshot.save() returned {ok_submit}; file exists? {os.path.exists(out_path)}")
    if not ok_editor or not os.path.exists(editor_path):
        raise RuntimeError(f"editor snapshot.save() returned {ok_editor}; file exists? {os.path.exists(editor_path)}")
    # Reject all-black PNGs — a zero-area or never-painted widget
    # would otherwise produce a valid PNG of nothing.
    if _is_all_black(editor_pixmap.toImage()):
        raise RuntimeError(
            f"editor snapshot is all-black — editor likely hidden or unpainted: {editor_path}"
        )
    return out_path


def _is_all_black(image) -> bool:
    """Return True if every visible pixel of ``image`` is black."""
    from PySide6.QtGui import QImage
    if image.isNull():
        return True
    w, h = image.width(), image.height()
    if w == 0 or h == 0:
        return True
    # Sample a 16x16 grid of pixels; if every sample is (0,0,0) we
    # treat the image as black. Cheap and avoids touching every pixel
    # for a high-DPI grab on Windows.
    samples = 0
    for y in (0, h // 4, h // 2, 3 * h // 4, h - 1):
        for x in (0, w // 4, w // 2, 3 * w // 4, w - 1):
            samples += 1
            px = image.pixelColor(x, y)
            if (px.red(), px.green(), px.blue()) != (0, 0, 0):
                return False
    return samples > 0


# ── main ─────────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 70)
    print("JobDesk GUI smoke (offscreen)")
    print("=" * 70)

    app: QApplication | None = None
    window = None

    with _step("qapp"):
        app = step_qapp()

    with _step("main_window"):
        window = step_main_window(app)

    # Make sure the deferred startup_recovery_finished emission has
    # landed (it was scheduled via QTimer.singleShot(0)).
    QApplication.processEvents()

    with _step("switch_to_submit"):
        ok = step_switch_to_submit(window)
        if not ok:
            raise RuntimeError(
                f"shell.pages.currentIndex={window.shell.pages.currentIndex()}, expected {SUBMIT_PAGE_INDEX}"
            )

    with _step("editor_visible"):
        ok = step_editor_visible(window)
        if not ok:
            raise RuntimeError("editor is not visible")

    ids = None
    with _step("add_two_nodes"):
        ids = step_add_two_nodes(window)
        if not ids["n1"] or not ids["n2"]:
            raise RuntimeError(f"add_node returned falsy ids: {ids}")

    edge_id = None
    with _step("connect_edge"):
        edge_id = step_connect_edge(window, ids)
        if not edge_id:
            raise RuntimeError("add_edge_at returned None")

    summary = None
    with _step("graph_summary"):
        summary = step_graph_summary(window)
        if summary["nodes"] != 2:
            raise RuntimeError(f"expected 2 nodes, got {summary['nodes']}")
        if summary["edges"] != 1:
            raise RuntimeError(f"expected 1 edge, got {summary['edges']}")
        # We expect one missing-required-input warning on the bare
        # second node when only one edge is wired; in our case the
        # PRE_OPT node HAS its required 'in' port wired, so no errors.
        if summary["errors"]:
            raise RuntimeError(f"unexpected graph errors: {summary['errors']}")

    row_count = None
    with _step("runs_page_empty"):
        row_count = step_runs_page(window)
        if row_count != 0:
            raise RuntimeError(f"expected 0 rows in Runs page, got {row_count}")

    snapshot_path = None
    with _step("snapshot"):
        snapshot_path = step_snapshot(window)

    # Clean shutdown — give the worker quit a tiny window.
    try:
        window.shutdown()
    except Exception:
        pass
    QApplication.processEvents()

    # ── 5. summary ──────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    failed = [s for s in _STEPS if s[1] == "FAIL"]
    for name, status, err in _STEPS:
        marker = "OK " if status == "PASS" else "BAD"
        print(f"  [{marker}] {name}")
    print()
    print(f"  graph summary : {summary}")
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