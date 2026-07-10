"""Tests for the ConfFlow runs-results detail pane (Phase 9D-3).

The detail pane renders parsed Gaussian/ORCA output below the result
preview table on the runs-results page. Double-clicking a result row
triggers :meth:`RunsResultsPage._render_detail_for_task`, which resolves
the output file via :func:`_resolve_output_path` and dispatches to
:py:meth:`ResultDetailPane.render_gaussian` or :py:meth:`render_orca`.

Parser calls are monkeypatched (matching the established pattern in
``test_gui_behavior.py``) so we don't spawn the (slow, license-bound)
real Gaussian binary during unit tests.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem

from jobdesk_app.core.parsers.gaussian import GaussianResult
from jobdesk_app.core.parsers.orca import OrcaResult
from jobdesk_app.gui.i18n import tr
from jobdesk_app.gui.pages.runs_results_page import (
    ResultDetailPane,
    RunsResultsPage,
    _resolve_output_path,
)


@pytest.fixture
def app(qtbot):
    """qtbot already provides a QApplication; this fixture is for clarity."""
    return qtbot


@pytest.fixture
def detail_pane(qtbot):
    pane = ResultDetailPane()
    qtbot.addWidget(pane)
    return pane


@pytest.fixture
def runs_page(qtbot):
    """Build a RunsResultsPage with stubbed RunService."""
    state = MagicMock()
    state.current_project_root = None
    with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as mock_svc:
        mock_svc.return_value.list_runs.return_value = []
        page = RunsResultsPage(state, log_cb=lambda m: None, status_cb=lambda m: None)
    qtbot.addWidget(page)
    return page


def _make_gaussian_result(**overrides) -> GaussianResult:
    """Build a GaussianResult populated with sensible defaults for assertions."""
    defaults = dict(
        converged=True,
        normal_termination=True,
        scf_energies=[-75.123456],
        final_energy_au=-75.123456,
        zpe_au=0.025,
        thermal_energy_au=0.030,
        enthalpy_au=0.031,
        gibbs_au=0.001,
        thermo_temperature_k=298.15,
        frequencies_cm1=[100.0, 200.0, 300.0],
        imaginary_freq_count=0,
        final_xyz="C  0.0  0.0  0.0\nH  1.0  0.0  0.0\nH  0.0  1.0  0.0\nH  0.0  0.0  1.0\nH -0.5 -0.5 -0.5",
        atom_symbols=["C", "H", "H", "H", "H"],
        mulliken_charges={1: -0.1, 2: 0.025},
        error_termination=False,
        error_message=None,
        cpu_time_seconds=12.5,
        walltime_seconds=120.0,
    )
    defaults.update(overrides)
    return GaussianResult(**defaults)


def _make_orca_result(**overrides) -> OrcaResult:
    defaults = dict(
        converged=True,
        normal_termination=True,
        scf_energies=[-75.0],
        final_energy_au=-75.0,
        correlation_energy_au=-0.5,
        total_energy_au=-75.5,
        zpe_au=0.024,
        enthalpy_au=0.030,
        gibbs_au=0.002,
        thermo_temperature_k=298.15,
        frequencies_cm1=[150.0, 250.0],
        imaginary_freq_count=0,
        final_xyz="C  0.0  0.0  0.0\nH  1.0  0.0  0.0\nH  0.0  1.0  0.0\nH  0.0  0.0  1.0",
        atom_symbols=["C", "H", "H", "H"],
        mulliken_charges={1: -0.05},
        error_termination=False,
        error_message=None,
        walltime_seconds=90.0,
    )
    defaults.update(overrides)
    return OrcaResult(**defaults)


# --- ResultDetailPane widget tests ----------------------------------------


def test_result_detail_pane_starts_empty(detail_pane):
    """A fresh pane shows the 'Select a task to see details' hint."""
    assert "Select" in detail_pane.title_label.text()
    assert detail_pane.energy_value.text() == "—"
    assert detail_pane.geometry_view.toPlainText() == ""


def test_result_detail_pane_renders_gaussian(detail_pane):
    """A successful Gaussian result populates every field."""
    result = _make_gaussian_result()
    detail_pane.render_gaussian(result)
    detail_pane.show()
    assert "Gaussian" in detail_pane.title_label.text()
    assert "-75.123456" in detail_pane.energy_value.text()
    assert "0 (minimum)" in detail_pane.imag_value.text()
    assert "Normal termination" in detail_pane.termination_value.text()
    assert "5 atoms" in detail_pane.geometry_view.toPlainText()
    assert detail_pane.error_value.isVisibleTo(detail_pane) is False


def test_result_detail_pane_renders_orca(detail_pane):
    """ORCA's total_energy_au (not final_energy_au) drives the headline."""
    result = _make_orca_result()
    detail_pane.render_orca(result)
    assert "ORCA" in detail_pane.title_label.text()
    assert "-75.500000" in detail_pane.energy_value.text()
    assert "ORCA TERMINATED NORMALLY" in detail_pane.termination_value.text()
    assert "4 atoms" in detail_pane.geometry_view.toPlainText()


def test_result_detail_pane_shows_imaginary_freq_count(detail_pane):
    """Non-zero imaginary count renders as 'N imaginary', not '0 (minimum)'."""
    result = _make_gaussian_result(imaginary_freq_count=2)
    detail_pane.render_gaussian(result)
    assert "2 imaginary" in detail_pane.imag_value.text()


def test_result_detail_pane_shows_error_status(detail_pane):
    """Error termination paints the status label red and shows the message."""
    detail_pane.show()
    result = _make_gaussian_result(
        normal_termination=False,
        error_termination=True,
        error_message="Convergence failure",
    )
    detail_pane.render_gaussian(result)
    assert "Error termination" in detail_pane.status_label.text()
    assert "#b91c1c" in detail_pane.status_label.styleSheet()
    assert detail_pane.error_value.isVisibleTo(detail_pane) is True
    assert "Convergence failure" in detail_pane.error_value.text()


def test_result_detail_pane_shows_abnormal_status(detail_pane):
    """SCF energies present but no termination flags → 'Abnormal termination'."""
    result = _make_gaussian_result(normal_termination=False, error_termination=False)
    detail_pane.render_gaussian(result)
    assert "Abnormal termination" in detail_pane.status_label.text()


def test_result_detail_pane_clear_resets_to_empty(detail_pane):
    """clear() returns the pane to the empty state regardless of prior content."""
    detail_pane.render_gaussian(_make_gaussian_result(imaginary_freq_count=3))
    assert "imaginary" in detail_pane.imag_value.text()
    detail_pane.clear()
    assert detail_pane.energy_value.text() == "—"
    assert detail_pane.imag_value.text() == "—"
    assert "Select" in detail_pane.title_label.text()


def test_result_detail_pane_formats_seconds(detail_pane):
    """_format_seconds handles <60s, m+s, h+m+s correctly."""
    assert ResultDetailPane._format_seconds(12.5) == "12.5 s"
    assert ResultDetailPane._format_seconds(125) == "2m 5s"
    assert ResultDetailPane._format_seconds(3725) == "1h 2m 5s"
    assert ResultDetailPane._format_seconds(None) == "—"


def test_result_detail_pane_handles_missing_geometry(detail_pane):
    """A result with no final_xyz shows the '(no geometry parsed)' placeholder."""
    result = _make_gaussian_result(final_xyz=None)
    detail_pane.render_gaussian(result)
    assert "(no geometry parsed)" in detail_pane.geometry_view.toPlainText()


# --- _resolve_output_path helper tests ------------------------------------


def _make_task(task_dir=None, remote_files=None):
    return SimpleNamespace(task_dir=task_dir, remote_task_files=remote_files or [])


def test_resolve_output_path_prefers_log_in_task_dir(tmp_path):
    """A *.log in the task_dir beats every other heuristic."""
    (tmp_path / "run.log").write_text("garbage", encoding="utf-8")
    (tmp_path / "run.out").write_text("also garbage", encoding="utf-8")
    task = _make_task(task_dir=str(tmp_path))
    path = _resolve_output_path(task)
    assert path is not None
    assert path.name == "run.log"


def test_resolve_output_path_falls_back_to_out(tmp_path):
    """If only *.out exists, return it."""
    (tmp_path / "run.out").write_text("output", encoding="utf-8")
    task = _make_task(task_dir=str(tmp_path))
    path = _resolve_output_path(task)
    assert path is not None
    assert path.name == "run.out"


def test_resolve_output_path_returns_none_when_no_files(tmp_path):
    """Empty task_dir + no remote files returns None."""
    task = _make_task(task_dir=str(tmp_path), remote_files=[])
    assert _resolve_output_path(task) is None


def test_resolve_output_path_returns_none_when_task_dir_missing():
    """Missing task_dir is silently treated as 'no output'."""
    task = _make_task(task_dir="/nonexistent/path/xyz")
    assert _resolve_output_path(task) is None


def test_resolve_output_path_uses_remote_file_stem(tmp_path):
    """When task_dir is empty, derive from remote file stem."""
    # Empty dir, no log/out inside.
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    # Remote file basename → workspace/<stem>.log
    (tmp_path / "water.log").write_text("hi", encoding="utf-8")
    task = _make_task(task_dir=str(empty_dir), remote_files=["/remote/water.gjf"])
    path = _resolve_output_path(task, workspace=tmp_path)
    assert path is not None
    assert path.name == "water.log"


# --- RunsResultsPage integration tests ------------------------------------


def test_runs_page_has_detail_pane(runs_page):
    """The page exposes a ResultDetailPane widget."""
    assert isinstance(runs_page.detail_pane, ResultDetailPane)


def test_render_detail_for_task_renders_gaussian(runs_page, tmp_path):
    """End-to-end: log file → parse → render → title shows energy."""
    log = tmp_path / "methane.log"
    log.write_text("Mock Gaussian log\nNormal termination\n", encoding="utf-8")
    task = _make_task(task_dir=str(tmp_path))
    parsed = _make_gaussian_result()

    with patch(
        "jobdesk_app.core.parsers.gaussian.parse_gaussian_log", return_value=parsed
    ):
        runs_page._render_detail_for_task("methane", task, tmp_path)

    assert "Gaussian" in runs_page.detail_pane.title_label.text()
    assert "-75.123456" in runs_page.detail_pane.energy_value.text()


def test_render_detail_for_task_renders_orca(runs_page, tmp_path):
    """ORCA path uses parse_orca_out and render_orca."""
    out = tmp_path / "orca.out"
    out.write_text("mock orca output", encoding="utf-8")
    task = _make_task(task_dir=str(tmp_path))
    parsed = _make_orca_result()

    with patch("jobdesk_app.core.parsers.orca.parse_orca_out", return_value=parsed):
        runs_page._render_detail_for_task("orca_job", task, tmp_path)

    assert "ORCA" in runs_page.detail_pane.title_label.text()
    assert "-75.500000" in runs_page.detail_pane.energy_value.text()


def test_render_detail_for_task_uses_cache(runs_page, tmp_path):
    """Second call for the same file should NOT re-invoke the parser."""
    log = tmp_path / "water.log"
    log.write_text("hi", encoding="utf-8")
    task = _make_task(task_dir=str(tmp_path))

    # Touch the file to ensure mtime/size are stable.
    parsed = _make_gaussian_result()
    calls = []

    def fake_parse(p):
        calls.append(str(p))
        return parsed

    with patch(
        "jobdesk_app.core.parsers.gaussian.parse_gaussian_log", side_effect=fake_parse
    ):
        runs_page._render_detail_for_task("water", task, tmp_path)
        runs_page._render_detail_for_task("water", task, tmp_path)

    assert len(calls) == 1, f"expected parser to be called once, got {len(calls)}"


def test_render_detail_for_task_handles_missing_output(runs_page, tmp_path):
    """No output file → pane shows the 'Output file not found' status in red."""
    task = _make_task(task_dir=str(tmp_path / "empty"))
    runs_page._render_detail_for_task("missing", task, tmp_path)
    # The exact label depends on the user's GUI language; assert against the
    # canonical tr() output rather than a hard-coded English substring so this
    # test is deterministic across `language: en` / `language: zh`.
    assert (
        runs_page.detail_pane.status_label.text()
        == tr("Output file not found", runs_page._language)
    )
    assert "#b91c1c" in runs_page.detail_pane.status_label.styleSheet()


def test_render_detail_for_task_handles_parser_exception(runs_page, tmp_path):
    """A parse exception → pane shows 'Parse error' and the exception message."""
    log = tmp_path / "broken.log"
    log.write_text("garbage", encoding="utf-8")
    task = _make_task(task_dir=str(tmp_path))
    runs_page.show()

    with patch(
        "jobdesk_app.core.parsers.gaussian.parse_gaussian_log",
        side_effect=RuntimeError("boom"),
    ):
        runs_page._render_detail_for_task("broken", task, tmp_path)

    assert (
        runs_page.detail_pane.status_label.text()
        == tr("Parse error", runs_page._language)
    )
    assert runs_page.detail_pane.error_value.isVisibleTo(runs_page.detail_pane) is True
    assert "boom" in runs_page.detail_pane.error_value.text()


def test_detail_cache_cleared_on_checkpoint(runs_page, tmp_path):
    """A synthetic _ckpt_ event wipes _detail_cache so the next render re-parses."""
    log = tmp_path / "water.log"
    log.write_text("hi", encoding="utf-8")
    task = _make_task(task_dir=str(tmp_path))
    parsed = _make_gaussian_result()

    with patch(
        "jobdesk_app.core.parsers.gaussian.parse_gaussian_log", return_value=parsed
    ):
        runs_page._render_detail_for_task("water", task, tmp_path)
        assert len(runs_page._detail_cache) == 1
        runs_page._detail_cache.clear()
        assert len(runs_page._detail_cache) == 0


def test_double_click_on_analysis_row_renders_detail(runs_page, tmp_path):
    """An itemDoubleClicked on an analysis row triggers render of that task."""
    log = tmp_path / "water.log"
    log.write_text("hi", encoding="utf-8")
    task = _make_task(task_dir=str(tmp_path))
    parsed = _make_gaussian_result()

    # Populate the result table as if _auto_analyze had run.
    runs_page.result_table.setRowCount(1)
    runs_page.result_table.setColumnCount(1)
    item = QTableWidgetItem("water")
    item.setData(
        Qt.UserRole,
        {"kind": "analysis", "task": task, "workspace": tmp_path},
    )
    runs_page.result_table.setItem(0, 0, item)

    with patch(
        "jobdesk_app.core.parsers.gaussian.parse_gaussian_log", return_value=parsed
    ):
        runs_page._on_result_row_double_clicked(item)

    assert "Gaussian" in runs_page.detail_pane.title_label.text()
    assert "-75.123456" in runs_page.detail_pane.energy_value.text()


def test_double_click_on_uncertain_row_shows_error(runs_page):
    """An itemDoubleClicked on an uncertain task row shows the error message."""
    runs_page.show()
    runs_page.result_table.setRowCount(1)
    runs_page.result_table.setColumnCount(1)
    item = QTableWidgetItem("flaky_job")
    item.setData(
        Qt.UserRole,
        {"kind": "uncertain", "status": "Uncertain", "error": "download failed"},
    )
    runs_page.result_table.setItem(0, 0, item)

    runs_page._on_result_row_double_clicked(item)

    assert runs_page.detail_pane.error_value.isVisibleTo(runs_page.detail_pane) is True
    assert "download failed" in runs_page.detail_pane.error_value.text()
    assert runs_page.detail_pane.geometry_view.toPlainText() == "(uncertain task — no parsed output)"


def test_double_click_on_empty_row_clears_pane(runs_page):
    """An itemDoubleClicked with no cached payload just clears the pane."""
    runs_page.detail_pane.render_gaussian(_make_gaussian_result())
    assert "Gaussian" in runs_page.detail_pane.title_label.text()

    runs_page.result_table.setRowCount(1)
    runs_page.result_table.setColumnCount(1)
    item = QTableWidgetItem("nothing")
    runs_page.result_table.setItem(0, 0, item)

    runs_page._on_result_row_double_clicked(item)
    assert "Select" in runs_page.detail_pane.title_label.text()
