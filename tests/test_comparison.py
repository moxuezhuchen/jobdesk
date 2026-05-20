"""Tests for cross-run comparison and export."""
import tempfile
from pathlib import Path

from jobdesk_app.services.comparison import (
    RunComparison,
    compare_runs,
    export_csv,
    export_markdown,
    HARTREE_TO_KCAL,
)


def _make_comparison(rows: list[dict], fields: list[str]) -> RunComparison:
    return RunComparison(rows=rows, field_names=fields)


class TestExportCsv:
    def test_csv_has_header_and_rows(self):
        comp = _make_comparison(
            rows=[{"run_id": "r1", "task_id": "t1", "scf_energy": -78.5}],
            fields=["run_id", "task_id", "scf_energy"],
        )
        csv_str = export_csv(comp)
        lines = csv_str.strip().splitlines()
        assert lines[0] == "run_id,task_id,scf_energy"
        assert "r1" in lines[1]
        assert "-78.5" in lines[1]

    def test_csv_writes_to_file(self, tmp_path):
        comp = _make_comparison(
            rows=[{"run_id": "r1", "task_id": "t1", "scf_energy": -78.5}],
            fields=["run_id", "task_id", "scf_energy"],
        )
        out = tmp_path / "results.csv"
        export_csv(comp, out)
        assert out.exists()
        assert "r1" in out.read_text()

    def test_empty_comparison_produces_header_only(self):
        comp = _make_comparison(rows=[], fields=["run_id", "task_id"])
        csv_str = export_csv(comp)
        assert "run_id" in csv_str


class TestExportMarkdown:
    def test_markdown_has_table_structure(self):
        comp = _make_comparison(
            rows=[{"run_id": "r1", "task_id": "t1", "scf_energy": -78.5}],
            fields=["run_id", "task_id", "scf_energy"],
        )
        md = export_markdown(comp)
        assert "| run_id |" in md
        assert "| --- |" in md
        assert "r1" in md

    def test_empty_returns_no_data(self):
        comp = _make_comparison(rows=[], fields=[])
        assert export_markdown(comp) == "(no data)"


class TestCompareRuns:
    def test_relative_energy_computed(self, tmp_path, monkeypatch):
        """compare_runs should compute relative energies in kcal/mol."""
        from jobdesk_app.services.run_service import RunService
        runs_dir = tmp_path / "runs"
        original_init = RunService.__init__

        def _patched(self, workspace_dir=None, **kwargs):
            original_init(self, workspace_dir, **kwargs)
            self.runs_dir = runs_dir

        monkeypatch.setattr(RunService, "__init__", _patched)
        from jobdesk_app.core.run import RunSpec, RunMode, RunSource
        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest
        from jobdesk_app.core.models import ResultRecord

        # Create two runs
        svc = RunService(tmp_path)
        spec = RunSpec(
            server_id="s", remote_dir="/tmp/x",
            command_template="g16 {name}", max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource(path="/remote/mol.gjf")],
        )
        r1 = svc.create_run(spec)
        r2 = svc.create_run(spec)

        # Write fake result files with known energies
        for run_id, energy in [(r1.run_id, -78.5), (r2.run_id, -78.6)]:
            tasks = Manifest.read(svc.runs_dir / run_id / "manifest.tsv")
            for task in tasks:
                result_dir = tmp_path / "results" / run_id / task.task_id
                result_dir.mkdir(parents=True, exist_ok=True)
                log = result_dir / "mol.log"
                log.write_text(
                    f"SCF Done:  E(RB3LYP) =  {energy}     A.U. after    9 cycles\n"
                    "Normal termination of Gaussian 16.\n",
                    encoding="utf-8",
                )
                task.status = TaskStatus.downloaded
            Manifest.write(svc.runs_dir / run_id / "manifest.tsv", tasks)

        comparison = compare_runs(tmp_path, [r1.run_id, r2.run_id], "scf_energy", "gaussian_sp")
        assert len(comparison.rows) == 2
        # Lower energy should have rel=0
        lowest = comparison.rows[0]
        assert lowest["scf_energy_rel_kcal"] == 0.0
        # Higher energy should have positive rel
        higher = comparison.rows[1]
        expected_rel = round((-78.5 - (-78.6)) * HARTREE_TO_KCAL, 4)
        assert abs(higher["scf_energy_rel_kcal"] - expected_rel) < 0.01

    def test_empty_run_ids_returns_empty(self, tmp_path):
        comparison = compare_runs(tmp_path, [])
        assert comparison.rows == []
