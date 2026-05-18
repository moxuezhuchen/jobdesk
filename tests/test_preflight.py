import yaml

from jobdesk_app.config.runtime import RuntimeBindingStore
from jobdesk_app.config.schema import RuntimeBinding
from jobdesk_app.services.preflight import preflight_project
from jobdesk_app.services.project_service import create_project_context


def _write_project(project_dir):
    (project_dir / "inputs").mkdir(parents=True)
    (project_dir / "inputs" / "a.sh").write_text("", encoding="utf-8")
    (project_dir / "project.yaml").write_text(yaml.safe_dump({
        "project_id": "preflight-demo",
        "project": {"name": "Preflight Demo"},
        "local_paths": {"input_dir": "./inputs", "result_dir": "./results"},
        "task_discoveries": [
            {"name": "shell_jobs", "mode": "flat_single", "entry_glob": "*.sh", "execution_profile": "shell"}
        ],
        "execution_profiles": {
            "shell": {"label": "Shell", "command": "bash {entry_name}"}
        },
    }), encoding="utf-8")


def test_preflight_project_reports_missing_binding(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _write_project(project_dir)
    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text(
        "servers:\n  srv1: {host: h, username: u, auth_method: key}\n",
        encoding="utf-8",
    )
    ctx = create_project_context(project_dir, servers_path)

    report = preflight_project(ctx, RuntimeBindingStore(tmp_path / "runtime.yaml"), servers_path)

    assert not report.ok
    assert any(issue.code == "missing_binding" for issue in report.errors)
    assert report.task_count == 1


def test_preflight_project_validates_scan_bindings_and_servers(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _write_project(project_dir)
    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text(
        "servers:\n  srv1: {host: h, username: u, auth_method: key}\n",
        encoding="utf-8",
    )
    runtime_path = tmp_path / "runtime.yaml"
    RuntimeBindingStore(runtime_path).save_binding(
        "preflight-demo",
        "shell",
        RuntimeBinding(server_id="srv1", remote_work_dir="/tmp/jobdesk/preflight"),
    )
    ctx = create_project_context(project_dir, servers_path)

    report = preflight_project(ctx, RuntimeBindingStore(runtime_path), servers_path)

    assert report.ok
    assert report.errors == []
    assert report.task_count == 1
    assert report.profiles == ["shell"]
    assert report.servers == ["srv1"]
