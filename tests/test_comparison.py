"""Tests for cross-run comparison and export."""

from jobdesk_app.services.comparison import (
    HARTREE_TO_KCAL,
    RunComparison,
    compare_runs,
    export_csv,
    export_markdown,
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

        monkeypatch.setenv("APPDATA", str(tmp_path))
        runs_dir = tmp_path / "JobDesk" / "runs"
        runs_dir.mkdir(parents=True)
        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.run import RunMode, RunSource, RunSpec

        # Create two runs
        svc = RunService(tmp_path, runs_dir=runs_dir)
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
            tasks = svc.repository.load_tasks(run_id)
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
            svc.repository.replace_tasks(run_id, tasks)

        comparison = compare_runs(tmp_path, [r1.run_id, r2.run_id], "scf_energy", "gaussian_sp")
        assert len(comparison.rows) == 2
        # Lower energy should have rel=0
        lowest = comparison.rows[0]
        assert lowest["scf_energy_rel_kcal"] == 0.0
        # Higher energy should have positive rel
        higher = comparison.rows[1]
        expected_rel = round((-78.5 - (-78.6)) * HARTREE_TO_KCAL, 4)
        assert abs(higher["scf_energy_rel_kcal"] - expected_rel) < 0.01

    def test_compare_runs_uses_each_record_local_dir(self, tmp_path, monkeypatch):
        """Global run records may point to results in different project roots."""
        from jobdesk_app.services.run_service import RunService

        monkeypatch.setenv("APPDATA", str(tmp_path))
        runs_dir = tmp_path / "JobDesk" / "runs"
        runs_dir.mkdir(parents=True)
        project_a = tmp_path / "project_a"
        project_b = tmp_path / "project_b"
        current_project = tmp_path / "current_project"
        for project in (project_a, project_b, current_project):
            project.mkdir()

        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.run import RunMode, RunSource, RunSpec

        spec = RunSpec(
            server_id="s", remote_dir="/tmp/x",
            command_template="g16 {name}", max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource(path="/remote/mol.gjf")],
        )
        svc_a = RunService(project_a, runs_dir=runs_dir)
        svc_b = RunService(project_b, runs_dir=runs_dir)
        r1 = svc_a.create_run(spec, run_id="run_a", local_dir=str(project_a))
        r2 = svc_b.create_run(spec, run_id="run_b", local_dir=str(project_b))

        for record, project, energy in [(r1, project_a, -78.5), (r2, project_b, -78.6)]:
            tasks = svc_a.repository.load_tasks(record.run_id)
            for task in tasks:
                result_dir = project / "results" / record.run_id / task.task_id
                result_dir.mkdir(parents=True, exist_ok=True)
                (result_dir / "mol.log").write_text(
                    f"SCF Done:  E(RB3LYP) =  {energy}     A.U. after    9 cycles\n"
                    "Normal termination of Gaussian 16.\n",
                    encoding="utf-8",
                )
                task.status = TaskStatus.downloaded
            svc_a.repository.replace_tasks(record.run_id, tasks)

        comparison = compare_runs(current_project, [r1.run_id, r2.run_id], "scf_energy", "gaussian_sp")

        assert [row["run_id"] for row in comparison.rows] == ["run_b", "run_a"]

    def test_empty_run_ids_returns_empty(self, tmp_path):
        comparison = compare_runs(tmp_path, [])
        assert comparison.rows == []
