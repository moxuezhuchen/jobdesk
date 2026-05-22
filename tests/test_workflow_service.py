"""Tests for workflow chain (WorkflowSpec, WorkflowRun, WorkflowRunner)."""
from pathlib import Path

import pytest

from jobdesk_app.services.workflow_service import (
    BUILTIN_WORKFLOWS,
    WorkflowRun,
    WorkflowRunner,
    WorkflowSpec,
    WorkflowStep,
)


@pytest.fixture(autouse=True)
def _isolate_runs_dir(tmp_path, monkeypatch):
    """Prevent tests from writing to global AppData runs_dir."""
    from jobdesk_app.services.run_service import RunService
    runs_dir = tmp_path / "_runs"
    runs_dir.mkdir()
    original_init = RunService.__init__

    def _patched(self, workspace_dir=None, **kwargs):
        original_init(self, workspace_dir, **kwargs)
        self.runs_dir = runs_dir

    monkeypatch.setattr(RunService, "__init__", _patched)


class TestWorkflowSpec:
    def test_topological_order_linear(self):
        spec = WorkflowSpec(
            name="test",
            steps=[
                WorkflowStep(name="opt", command_template="g16 {name}"),
                WorkflowStep(name="freq", command_template="g16 {name}", depends_on=["opt"]),
                WorkflowStep(name="sp", command_template="orca {name}", depends_on=["freq"]),
            ],
        )
        order = spec.topological_order()
        names = [s.name for s in order]
        assert names.index("opt") < names.index("freq")
        assert names.index("freq") < names.index("sp")

    def test_topological_order_parallel(self):
        spec = WorkflowSpec(
            name="test",
            steps=[
                WorkflowStep(name="a", command_template="cmd"),
                WorkflowStep(name="b", command_template="cmd"),
                WorkflowStep(name="c", command_template="cmd", depends_on=["a", "b"]),
            ],
        )
        order = spec.topological_order()
        names = [s.name for s in order]
        assert names.index("a") < names.index("c")
        assert names.index("b") < names.index("c")

    def test_step_lookup(self):
        spec = WorkflowSpec(
            name="test",
            steps=[WorkflowStep(name="opt", command_template="g16 {name}")],
        )
        assert spec.step("opt") is not None
        assert spec.step("missing") is None

    def test_cycle_detection(self):
        import pytest
        spec = WorkflowSpec(
            name="test",
            steps=[
                WorkflowStep(name="a", command_template="cmd", depends_on=["b"]),
                WorkflowStep(name="b", command_template="cmd", depends_on=["a"]),
            ],
        )
        with pytest.raises(ValueError, match="cycle"):
            spec.topological_order()


class TestWorkflowRun:
    def test_save_and_load(self, tmp_path):
        wf_run = WorkflowRun(
            workflow_id="wf_001",
            workflow_name="opt_freq",
            workspace_dir=tmp_path,
            server_id="hpc1",
            remote_dir="/scratch/test",
            sources=["/remote/mol.gjf"],
        )
        wf_run.step_run_ids["opt"] = "run_001"
        wf_run.step_status["opt"] = "running"
        wf_run.save()

        loaded = WorkflowRun.load(tmp_path, "wf_001")
        assert loaded.workflow_id == "wf_001"
        assert loaded.workflow_name == "opt_freq"
        assert loaded.step_run_ids["opt"] == "run_001"
        assert loaded.step_status["opt"] == "running"
        assert loaded.sources == ["/remote/mol.gjf"]


class TestWorkflowRunner:
    def test_start_creates_workflow_run(self, tmp_path):
        spec = BUILTIN_WORKFLOWS["opt_freq"]
        runner = WorkflowRunner(tmp_path)
        wf_run = runner.start(spec, "hpc1", "/scratch/test", ["/remote/mol.gjf"])
        assert wf_run.workflow_id
        assert wf_run.workflow_name == "opt_freq"
        # Workflow file should be saved
        wf_file = tmp_path / ".jobdesk" / "workflows" / f"{wf_run.workflow_id}.json"
        assert wf_file.exists()

    def test_advance_starts_first_step(self, tmp_path):
        spec = WorkflowSpec(
            name="test",
            steps=[
                WorkflowStep(name="opt", command_template="echo {name}"),
                WorkflowStep(name="freq", command_template="echo {name}", depends_on=["opt"]),
            ],
        )
        runner = WorkflowRunner(tmp_path)
        wf_run = runner.start(spec, "srv", "/tmp/test", ["/remote/a.gjf"])
        started, _ = runner.advance(spec, wf_run, None, None)
        assert "opt" in started
        assert "freq" not in started  # freq depends on opt

    def test_advance_starts_second_step_when_first_complete(self, tmp_path):
        spec = WorkflowSpec(
            name="test",
            steps=[
                WorkflowStep(name="opt", command_template="echo {name}"),
                WorkflowStep(name="freq", command_template="echo {name}", depends_on=["opt"]),
            ],
        )
        runner = WorkflowRunner(tmp_path)
        wf_run = runner.start(spec, "srv", "/tmp/test", ["/remote/a.gjf"])
        runner.advance(spec, wf_run, None, None)
        # Simulate opt completing
        wf_run.step_status["opt"] = "completed"
        wf_run.save()
        started, _ = runner.advance(spec, wf_run, None, None)
        assert "freq" in started

    def test_sync_status_marks_completed(self, tmp_path):
        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest
        from jobdesk_app.services.run_service import RunService

        spec = WorkflowSpec(
            name="test",
            steps=[WorkflowStep(name="opt", command_template="echo {name}")],
        )
        runner = WorkflowRunner(tmp_path)
        wf_run = runner.start(spec, "srv", "/tmp/test", ["/remote/a.gjf"])
        runner.advance(spec, wf_run, None, None)

        # Manually mark the underlying run as downloaded
        run_id = wf_run.step_run_ids["opt"]
        svc = RunService(tmp_path)
        record = svc.load_run(run_id)
        tasks = Manifest.read(record.manifest_path)
        for t in tasks:
            t.status = TaskStatus.downloaded
        Manifest.write(record.manifest_path, tasks)
        svc.update_run_from_manifest(run_id)

        runner.sync_status(spec, wf_run)
        assert wf_run.step_status["opt"] == "completed"


class TestBuiltinWorkflows:
    def test_opt_freq_has_two_steps(self):
        spec = BUILTIN_WORKFLOWS["opt_freq"]
        assert len(spec.steps) == 2
        assert spec.steps[0].name == "opt"
        assert spec.steps[1].name == "freq"
        assert spec.steps[1].depends_on == ["opt"]
        assert spec.steps[1].input_from == "opt"

    def test_opt_freq_sp_has_three_steps(self):
        spec = BUILTIN_WORKFLOWS["opt_freq_sp"]
        assert len(spec.steps) == 3
        order = spec.topological_order()
        names = [s.name for s in order]
        assert names == ["opt", "freq", "sp"]

    def test_all_builtin_workflows_have_valid_topology(self):
        for name, spec in BUILTIN_WORKFLOWS.items():
            order = spec.topological_order()
            assert len(order) == len(spec.steps), f"{name} topology failed"


# ---- Valid Gaussian .log fragment for geometry extraction tests ----

_VALID_GAUSSIAN_LOG = """\
 Entering Gaussian System
 #p opt B3LYP/6-31G(d)

 Standard orientation:
 ---------------------------------------------------------------------
 Center     Atomic      Atomic             Coordinates (Angstroms)
 Number     Number       Type             X           Y           Z
 ---------------------------------------------------------------------
      1          6           0        0.000000    0.000000    0.000000
      2          8           0        0.000000    0.000000    1.200000
      3          1           0        0.000000    0.930000   -0.560000
      4          1           0        0.000000   -0.930000   -0.560000
 ---------------------------------------------------------------------
 Normal termination of Gaussian 16.
"""

_INVALID_GAUSSIAN_LOG = """\
 Entering Gaussian System
 #p opt B3LYP/6-31G(d)

 Convergence failure -- run terminated.
"""


class TestPrepareDownstreamInputs:
    """Tests for _prepare_downstream_inputs geometry extraction."""

    def test_normal_path_generates_gjf_with_remote_source(self, tmp_path):
        """Valid .log makes advance start downstream, pending_uploads non-empty,
        generates .gjf, RunSource uses /remote/... path."""
        spec = WorkflowSpec(
            name="test",
            steps=[
                WorkflowStep(name="opt", command_template="g16 {name}"),
                WorkflowStep(name="freq", command_template="g16 {name}", depends_on=["opt"], input_from="opt"),
            ],
        )
        runner = WorkflowRunner(tmp_path)
        wf_run = runner.start(spec, "srv", "/scratch/proj", ["/remote/mol.gjf"])
        # Start opt
        runner.advance(spec, wf_run, None, None)
        run_id = wf_run.step_run_ids["opt"]

        # Simulate opt completed with valid geometry in results
        results_dir = tmp_path / "results" / run_id / "mol"
        results_dir.mkdir(parents=True)
        (results_dir / "mol.log").write_text(_VALID_GAUSSIAN_LOG, encoding="utf-8")

        wf_run.step_status["opt"] = "completed"
        wf_run.save()

        # Advance should start freq with generated input.
        started, pending_uploads = runner.advance(spec, wf_run, None, None)

        assert "freq" in started
        assert len(pending_uploads) > 0

        # Verify generated .gjf exists locally
        local_paths = list(pending_uploads.keys())
        assert any(p.endswith(".gjf") for p in local_paths)
        gjf_path = next(p for p in local_paths if p.endswith(".gjf"))
        assert Path(gjf_path).exists()

        # Verify RunSource uses remote posix path, not Windows local path
        from jobdesk_app.services.run_service import RunService
        svc = RunService(tmp_path)
        freq_run_id = wf_run.step_run_ids["freq"]
        from jobdesk_app.core.manifest import Manifest
        tasks = Manifest.read(svc.load_run(freq_run_id).manifest_path)
        for t in tasks:
            assert t.remote_job_dir.startswith("/")
            assert "\\" not in t.remote_job_dir

        # Verify remote paths in pending_uploads are posix
        for remote in pending_uploads.values():
            assert remote.startswith("/scratch/proj/")
            assert "\\" not in remote

    def test_geometry_extraction_failure_blocks_advance(self, tmp_path):
        """Invalid .log leaves downstream unstarted and pending_uploads empty."""
        spec = WorkflowSpec(
            name="test",
            steps=[
                WorkflowStep(name="opt", command_template="g16 {name}"),
                WorkflowStep(name="freq", command_template="g16 {name}", depends_on=["opt"], input_from="opt"),
            ],
        )
        runner = WorkflowRunner(tmp_path)
        wf_run = runner.start(spec, "srv", "/scratch/proj", ["/remote/mol.gjf"])
        runner.advance(spec, wf_run, None, None)
        run_id = wf_run.step_run_ids["opt"]

        # Simulate opt completed but .log has no valid geometry
        results_dir = tmp_path / "results" / run_id / "mol"
        results_dir.mkdir(parents=True)
        (results_dir / "mol.log").write_text(_INVALID_GAUSSIAN_LOG, encoding="utf-8")

        wf_run.step_status["opt"] = "completed"
        wf_run.save()

        started, pending_uploads = runner.advance(spec, wf_run, None, None)

        assert "freq" not in started
        assert pending_uploads == {}


# ---- Water opt_freq workflow smoke test ----

_WATER_OPT_LOG = """\
 Entering Gaussian System
 #p opt B3LYP/6-31G(d)

 water optimization

 Standard orientation:
 ---------------------------------------------------------------------
 Center     Atomic      Atomic             Coordinates (Angstroms)
 Number     Number       Type             X           Y           Z
 ---------------------------------------------------------------------
      1          8           0        0.000000    0.000000    0.117370
      2          1           0        0.000000    0.757160   -0.469480
      3          1           0        0.000000   -0.757160   -0.469480
 ---------------------------------------------------------------------
 Normal termination of Gaussian 16.
"""


class TestWaterWorkflowSmoke:
    """End-to-end smoke: opt_freq workflow with water molecule."""

    def test_water_opt_freq_advance_generates_freq_gjf(self, tmp_path):
        spec = BUILTIN_WORKFLOWS["opt_freq"]
        runner = WorkflowRunner(tmp_path)
        wf_run = runner.start(spec, "srv", "/remote/water", ["/remote/water/water_opt.gjf"])

        # Advance starts opt.
        started, _ = runner.advance(spec, wf_run, None, None)
        assert started == ["opt"]
        run_id = wf_run.step_run_ids["opt"]

        # Simulate opt completed with water geometry
        results_dir = tmp_path / "results" / run_id / "water_opt"
        results_dir.mkdir(parents=True)
        (results_dir / "water_opt.log").write_text(_WATER_OPT_LOG, encoding="utf-8")
        wf_run.step_status["opt"] = "completed"
        wf_run.save()

        # Advance starts freq from extracted geometry.
        started, pending_uploads = runner.advance(spec, wf_run, None, None)
        assert started == ["freq"]
        assert len(pending_uploads) == 1

        # Verify generated .gjf path
        gjf_local = next(iter(pending_uploads.keys()))
        gjf_remote = next(iter(pending_uploads.values()))
        expected_staging = (
            tmp_path / ".jobdesk" / "workflow_inputs"
            / wf_run.workflow_id / "freq" / "water_opt_freq.gjf"
        )
        assert Path(gjf_local) == expected_staging
        assert expected_staging.exists()

        # Verify remote target path
        assert gjf_remote == "/remote/water/water_opt_freq.gjf"

        # Verify .gjf content
        content = expected_staging.read_text(encoding="utf-8")
        assert "# B3LYP/6-31G(d) freq" in content
        # Must contain O and two H atoms
        coord_lines = [l for l in content.splitlines() if l.strip().startswith(("O ", "H "))]
        assert len(coord_lines) == 3
        assert coord_lines[0].strip().startswith("O")
        assert coord_lines[1].strip().startswith("H")
        assert coord_lines[2].strip().startswith("H")
