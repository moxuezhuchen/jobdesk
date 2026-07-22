from jobdesk_app.core.run import build_run_plan
from jobdesk_app.services.program_adapters import ConfFlowAdapter


def test_confflow_adapter_builds_one_run_task_with_config_and_summary_outputs():
    spec = ConfFlowAdapter.build_spec(
        server_id="wsl",
        remote_dir="/tmp/jobdesk",
        xyz_paths=["/tmp/jobdesk/water.xyz"],
        config_path="/tmp/jobdesk/confflow.yaml",
        resume=True,
    )

    assert [source.path for source in spec.sources] == ["/tmp/jobdesk/water.xyz"]
    assert [source.path for source in spec.supporting_sources] == ["/tmp/jobdesk/confflow.yaml"]
    assert spec.command_template == (
        "workspace=/tmp/jobdesk && source={path} && "
        'staged="$workspace/"{artifact_name} && cd "$workspace" && '
        'if [ "$source" != "$staged" ]; then cp -- "$source" "$staged"; fi && '
        'confflow "$staged" -c /tmp/jobdesk/confflow.yaml '
        '-w "$workspace/"{basename}_confflow_work --resume'
    )
    assert spec.result_templates == [
        "{basename}.txt",
        "{basename}min.xyz",
        "{basename}_confflow_work/run_summary.json",
        "{basename}_confflow_work/workflow_stats.json",
        "{basename}_confflow_work/.workflow_state.json",
    ]


def test_confflow_adapter_batch_multiple_xyz_shared_yaml():
    spec = ConfFlowAdapter.build_spec(
        server_id="wsl",
        remote_dir="/tmp/jobdesk",
        xyz_paths=[
            "/tmp/jobdesk/mol1.xyz",
            "/tmp/jobdesk/mol2.xyz",
            "/tmp/jobdesk/mol3.xyz",
        ],
        config_path="/tmp/jobdesk/confflow.yaml",
        max_parallel=3,
    )

    assert len(spec.sources) == 3
    assert spec.max_parallel == 3
    assert [s.path for s in spec.supporting_sources] == ["/tmp/jobdesk/confflow.yaml"]

    plan = build_run_plan(spec, run_id="batch01")
    assert len(plan.tasks) == 3
    for task in plan.tasks:
        assert "confflow.yaml" in task.command
        assert task.supporting_paths == ["/tmp/jobdesk/confflow.yaml"]
        assert len(task.remote_result_files) == 5
        assert task.remote_result_files[-1].endswith("_confflow_work/.workflow_state.json")

    # Verify per-molecule outputs
    assert "mol1_confflow_work/run_summary.json" in plan.tasks[0].remote_result_files[2]
    assert "mol2_confflow_work/run_summary.json" in plan.tasks[1].remote_result_files[2]


def test_confflow_adapter_single_xyz_is_valid_batch_of_one():
    """Single molecule submission still works as a batch of one."""
    spec = ConfFlowAdapter.build_spec(
        server_id="wsl",
        remote_dir="/tmp/jobdesk",
        xyz_paths=["/tmp/jobdesk/water.xyz"],
        config_path="/tmp/jobdesk/confflow.yaml",
        max_parallel=4,
    )

    plan = build_run_plan(spec, run_id="single01")
    assert len(plan.tasks) == 1
    assert spec.max_parallel == 4


def test_confflow_adapter_quotes_explicit_workspace_config_and_source_paths():
    spec = ConfFlowAdapter.build_spec(
        server_id="wsl",
        remote_dir="/tmp/project with spaces/.jobdesk_submissions/run-safe",
        xyz_paths=["/shared/input files/mol one.xyz"],
        config_path="/tmp/project with spaces/.jobdesk_submissions/run-safe/workflow.yaml",
        resume=True,
    )

    command = build_run_plan(spec, run_id="quoted").tasks[0].command
    assert "workspace='/tmp/project with spaces/.jobdesk_submissions/run-safe'" in command
    assert "source='/shared/input files/mol one.xyz'" in command
    assert "staged=\"$workspace/\"'mol one.xyz'" in command
    assert 'confflow "$staged"' in command
    assert "-c '/tmp/project with spaces/.jobdesk_submissions/run-safe/workflow.yaml'" in command
    assert "-w \"$workspace/\"'mol one'_confflow_work --resume" in command


def test_same_basename_inputs_get_distinct_staged_work_and_checkpoint_paths():
    spec = ConfFlowAdapter.build_spec(
        server_id="wsl",
        remote_dir="/tmp/jobdesk/.jobdesk_submissions/one-batch",
        xyz_paths=["/shared/a/same.xyz", "/shared/b/same.xyz"],
        config_path="/tmp/jobdesk/.jobdesk_submissions/one-batch/workflow.yaml",
    )

    assert [source.rendered_name for source in spec.sources] == ["same.xyz", "same_2.xyz"]
    plan = build_run_plan(spec, run_id="same-batch")
    assert [task.task_id for task in plan.tasks] == ["same", "same_2"]
    assert "{artifact_name}" not in plan.tasks[0].command
    assert 'staged="$workspace/"same.xyz' in plan.tasks[0].command
    assert 'staged="$workspace/"same_2.xyz' in plan.tasks[1].command
    assert set(plan.tasks[0].remote_result_files).isdisjoint(plan.tasks[1].remote_result_files)
    assert plan.tasks[0].remote_result_files[-2:] == [
        "same_confflow_work/workflow_stats.json",
        "same_confflow_work/.workflow_state.json",
    ]
    assert plan.tasks[1].remote_result_files[-2:] == [
        "same_2_confflow_work/workflow_stats.json",
        "same_2_confflow_work/.workflow_state.json",
    ]
