from jobdesk_app.services.program_adapters import ConfFlowAdapter


def test_confflow_adapter_builds_one_run_task_with_config_and_summary_outputs():
    spec = ConfFlowAdapter.build_spec(
        server_id="wsl",
        remote_dir="/tmp/jobdesk",
        xyz_path="/tmp/jobdesk/water.xyz",
        config_path="/tmp/jobdesk/confflow.yaml",
        resume=True,
    )

    assert [source.path for source in spec.sources] == ["/tmp/jobdesk/water.xyz"]
    assert [source.path for source in spec.supporting_sources] == ["/tmp/jobdesk/confflow.yaml"]
    assert spec.command_template == "confflow {name} -c confflow.yaml -w {basename}_confflow_work --resume"
    assert spec.result_templates == [
        "{basename}.txt",
        "{basename}min.xyz",
        "{basename}_confflow_work/run_summary.json",
        "{basename}_confflow_work/workflow_stats.json",
    ]
