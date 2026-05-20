"""Tests for workflow chain (WorkflowSpec, WorkflowRun, WorkflowRunner)."""
import tempfile
from pathlib import Path

from jobdesk_app.services.workflow_service import (
    WorkflowStep,
    WorkflowSpec,
    WorkflowRun,
    WorkflowRunner,
    BUILTIN_WORKFLOWS,
)


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
        started = runner.advance(spec, wf_run, None, None)
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
        started = runner.advance(spec, wf_run, None, None)
        assert "freq" in started

    def test_sync_status_marks_completed(self, tmp_path, monkeypatch):
        from jobdesk_app.services.run_service import RunService
        from jobdesk_app.core.run import RunSpec, RunMode, RunSource
        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest

        runs_dir = tmp_path / "runs"
        original_init = RunService.__init__

        def _patched(self, workspace_dir=None, **kwargs):
            original_init(self, workspace_dir, **kwargs)
            self.runs_dir = runs_dir

        monkeypatch.setattr(RunService, "__init__", _patched)

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
