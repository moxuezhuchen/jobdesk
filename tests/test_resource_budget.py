"""P-M2 (R-M2) ResourceBudget + TaskRecord round-trip tests."""

from __future__ import annotations

from jobdesk_app.core.manifest import ResourceBudget, TaskRecord
from jobdesk_app.core.run import RunMode, RunSpec, build_run_plan
from jobdesk_app.core.run import WorkflowKind


def test_resource_budget_effective_slots_multiplies_three_levels() -> None:
    """jobdesk × yaml × cores = effective_slots."""
    budget = ResourceBudget(jobdesk_max_parallel=2, yaml_max_parallel_jobs=4, cores_per_task=8)
    assert budget.effective_slots == 64


def test_resource_budget_exceeds_threshold_default() -> None:
    """64 > 0.8 * 70 → exceeds; 64 ≤ 0.8 * 80 → does not."""
    budget = ResourceBudget(jobdesk_max_parallel=2, yaml_max_parallel_jobs=4, cores_per_task=8)
    assert budget.exceeds(70) is True
    assert budget.exceeds(80) is False


def test_resource_budget_exceeds_below_threshold_returns_false() -> None:
    """64 ≤ 0.8 * 80 → not exceeded."""
    budget = ResourceBudget(jobdesk_max_parallel=2, yaml_max_parallel_jobs=4, cores_per_task=8)
    assert budget.exceeds(100) is False


def test_resource_budget_exceeds_with_none_server_is_false() -> None:
    """No max_cores configured → never warn."""
    budget = ResourceBudget(jobdesk_max_parallel=2, yaml_max_parallel_jobs=4, cores_per_task=8)
    assert budget.exceeds(None) is False


def test_task_record_round_trip_preserves_resource_budget_dict() -> None:
    """TaskRecord.model_dump → model_validate preserves the JSON dict."""
    payload = {"jobdesk_max_parallel": 2, "yaml_max_parallel_jobs": 4, "cores_per_task": 8}
    task = TaskRecord(
        task_id="t1",
        batch_id="run-1",
        remote_job_dir="/remote/run-1/t1",
        resource_budget=payload,
    )
    dump = task.model_dump(mode="json")
    assert dump.get("resource_budget") == payload
    reloaded = TaskRecord.model_validate(dump)
    assert reloaded.resource_budget == payload


def test_run_spec_propagates_resource_budget_to_task_plan() -> None:
    """RunSpec.resource_budget must flow into each RunTaskPlan."""
    from jobdesk_app.core.run import RunSource

    budget = ResourceBudget(jobdesk_max_parallel=2, yaml_max_parallel_jobs=4, cores_per_task=8)
    spec = RunSpec(
        server_id="wsl",
        remote_dir="/tmp/jobdesk",
        command_template="echo {path}",
        max_parallel=2,
        mode=RunMode.selected_files,
        sources=[RunSource("/tmp/jobdesk/water.xyz")],
        workflow_kind=WorkflowKind.confflow,
        resource_budget=budget,
    )
    plan = build_run_plan(spec, run_id="budget-1")
    assert len(plan.tasks) >= 1
    for task in plan.tasks:
        assert task.resource_budget is budget
