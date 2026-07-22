from jobdesk_app.core.run import RunMode, RunSource, RunSpec, WorkflowKind, build_run_plan, chunk_sources


def test_build_run_plan_for_selected_files():
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="g16 {name}",
        max_parallel=4,
        mode=RunMode.selected_files,
        sources=[
            RunSource(path="/remote/jobs/a.gjf", is_dir=False),
            RunSource(path="/remote/jobs/b.gjf", is_dir=False),
        ],
    )

    plan = build_run_plan(spec, run_id="run001")

    assert plan.run_id == "run001"
    assert [task.task_id for task in plan.tasks] == ["a", "b"]
    assert [task.command for task in plan.tasks] == ["cd /remote/jobs && g16 a.gjf", "cd /remote/jobs && g16 b.gjf"]
    assert all(task.remote_job_dir.startswith("/remote/jobs/.jobdesk_runs/run001/") for task in plan.tasks)


def test_build_run_plan_keeps_root_remote_dir_absolute():
    spec = RunSpec(
        server_id="s1",
        remote_dir="/",
        command_template="g16 {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource(path="/water.gjf", is_dir=False)],
    )

    plan = build_run_plan(spec, run_id="run_root")

    assert plan.tasks[0].remote_job_dir == "/.jobdesk_runs/run_root/water"


def test_build_run_plan_for_selected_directories():
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash run.sh",
        max_parallel=2,
        mode=RunMode.selected_directories,
        sources=[
            RunSource(path="/remote/jobs/case001", is_dir=True),
        ],
    )

    plan = build_run_plan(spec, run_id="run002")

    assert plan.tasks[0].task_id == "case001"
    assert plan.tasks[0].command == "cd /remote/jobs/case001 && bash run.sh"


def test_build_run_plan_for_single_current_directory_command():
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash run_all.sh",
        max_parallel=1,
        mode=RunMode.current_directory,
        sources=[],
    )

    plan = build_run_plan(spec, run_id="run003")

    assert len(plan.tasks) == 1
    assert plan.tasks[0].task_id == "current_directory"
    assert plan.tasks[0].command == "cd /remote/jobs && bash run_all.sh"


def test_chunk_sources_splits_selected_inputs():
    chunks = chunk_sources(
        [
            RunSource("/r/a.gjf"),
            RunSource("/r/b.gjf"),
            RunSource("/r/c.gjf"),
        ],
        batch_size=2,
    )

    assert [[source.name for source in chunk] for chunk in chunks] == [["a.gjf", "b.gjf"], ["c.gjf"]]


def test_build_run_plan_preserves_supporting_inputs_and_declared_outputs():
    spec = RunSpec(
        server_id="wsl",
        remote_dir="/remote/jobs",
        command_template="confflow {name} -c settings.yaml -w {basename}_confflow_work",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/water.xyz")],
        supporting_sources=[RunSource("/remote/jobs/settings.yaml")],
        result_templates=["{basename}.txt", "{basename}_confflow_work/run_summary.json"],
    )

    plan = build_run_plan(spec, run_id="run004")

    assert len(plan.tasks) == 1
    assert plan.tasks[0].supporting_paths == ["/remote/jobs/settings.yaml"]
    assert plan.tasks[0].remote_result_files == [
        "water.txt",
        "water_confflow_work/run_summary.json",
    ]


def test_build_run_plan_declares_exact_confflow_paths():
    spec = RunSpec(
        server_id="wsl",
        remote_dir="/remote/submissions/job-1",
        command_template="confflow {name} -c workflow.yaml -w {basename}_confflow_work",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/source/water.xyz", artifact_stem="water_2")],
        supporting_sources=[RunSource("/remote/submissions/job-1/workflow.yaml")],
        result_templates=["{basename}.txt", "{basename}_confflow_work/run_summary.json"],
        workflow_kind=WorkflowKind.confflow,
    )

    task = build_run_plan(spec, run_id="run-paths").tasks[0]

    assert task.workflow_kind == WorkflowKind.confflow
    assert task.remote_config_path == "/remote/submissions/job-1/workflow.yaml"
    assert task.remote_workflow_dir == "/remote/submissions/job-1/water_2_confflow_work"
    assert task.remote_state_path == "/remote/submissions/job-1/water_2_confflow_work/.workflow_state.json"
    assert task.remote_stats_path == "/remote/submissions/job-1/water_2_confflow_work/workflow_stats.json"
    assert task.remote_log_path.endswith("/.jobdesk_runs/run-paths/water_2/.jobdesk_submit.log")
    assert task.remote_result_paths == [
        "/remote/submissions/job-1/water_2.txt",
        "/remote/submissions/job-1/water_2_confflow_work/run_summary.json",
    ]
    assert task.command.count("--resume") == 0
    assert task.dry_run_command.endswith(" --dry-run")
    assert task.resume_command.endswith(" --resume")
    assert task.resume_command.count("--resume") == 1
    assert task.resume_dry_run_command.endswith(" --resume --dry-run")
    assert task.resume_requested is False


def test_build_run_plan_disambiguates_sanitized_task_id_collisions():
    spec = RunSpec(
        server_id="wsl",
        remote_dir="/remote/jobs",
        command_template="confflow {name}",
        max_parallel=2,
        mode=RunMode.selected_files,
        sources=[
            RunSource("/remote/jobs/mol a.xyz"),
            RunSource("/remote/jobs/mol_a.xyz"),
        ],
        result_templates=["{basename}.txt"],
    )

    plan = build_run_plan(spec, run_id="collision01")

    assert [task.task_id for task in plan.tasks] == ["mol_a", "mol_a_2"]
    assert len({task.remote_job_dir for task in plan.tasks}) == 2
