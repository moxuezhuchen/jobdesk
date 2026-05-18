from pathlib import Path

from jobdesk_app.config.loader import load_project
from jobdesk_app.config.runtime import RuntimeBindingStore
from jobdesk_app.services.project_wizard import (
    WizardBindingSpec,
    WizardDiscoverySpec,
    WizardProfileSpec,
    WizardProjectSpec,
    create_project_from_wizard,
)
from jobdesk_app.services.project_service import create_project_context


def test_create_project_from_wizard_writes_new_schema_project(tmp_path):
    project_root = tmp_path / "wizard_project"
    runtime_path = tmp_path / "runtime_bindings.yaml"
    spec = WizardProjectSpec(
        project_id="wizard-demo",
        project_name="Wizard Demo",
        project_root=project_root,
        input_dir="inputs",
        result_dir="results",
        discoveries=[
            WizardDiscoverySpec(
                name="shell_jobs",
                mode="flat_single",
                entry_glob="*.sh",
                execution_profile="shell",
            )
        ],
        profiles=[
            WizardProfileSpec(
                name="shell",
                label="Shell",
                command="bash {entry_name}",
                max_parallel=2,
            )
        ],
        bindings=[
            WizardBindingSpec(
                execution_profile="shell",
                server_id="srv1",
                remote_work_dir="/tmp/jobdesk/wizard-demo",
                max_parallel=3,
            )
        ],
        runtime_bindings_path=runtime_path,
    )

    result = create_project_from_wizard(spec)

    assert result.project_yaml_path == project_root / "project.yaml"
    assert (project_root / "inputs").is_dir()
    assert (project_root / "results").is_dir()
    cfg = load_project(project_root)
    assert cfg.project_id == "wizard-demo"
    assert cfg.task_discoveries[0].execution_profile == "shell"
    assert cfg.execution_profiles["shell"].command == "bash {entry_name}"
    binding = RuntimeBindingStore(runtime_path).get_binding("wizard-demo", "shell")
    assert binding is not None
    assert binding.server_id == "srv1"
    assert binding.remote_work_dir == "/tmp/jobdesk/wizard-demo"
    assert binding.max_parallel == 3


def test_create_project_from_wizard_refuses_existing_project_yaml(tmp_path):
    project_root = tmp_path / "wizard_project"
    project_root.mkdir()
    (project_root / "project.yaml").write_text("existing: true\n", encoding="utf-8")
    spec = WizardProjectSpec(
        project_id="wizard-demo",
        project_name="Wizard Demo",
        project_root=project_root,
        discoveries=[
            WizardDiscoverySpec(
                name="shell_jobs",
                mode="flat_single",
                entry_glob="*.sh",
                execution_profile="shell",
            )
        ],
        profiles=[
            WizardProfileSpec(
                name="shell",
                label="Shell",
                command="bash {entry_name}",
            )
        ],
    )

    try:
        create_project_from_wizard(spec)
    except FileExistsError as exc:
        assert "project.yaml" in str(exc)
    else:
        raise AssertionError("expected FileExistsError")


def test_create_project_from_wizard_context_can_load(tmp_path):
    project_root = tmp_path / "wizard_project"
    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text(
        "servers:\n  srv1: {host: h, username: u, auth_method: key}\n",
        encoding="utf-8",
    )
    spec = WizardProjectSpec(
        project_id="wizard-demo",
        project_name="Wizard Demo",
        project_root=project_root,
        discoveries=[
            WizardDiscoverySpec(
                name="shell_jobs",
                mode="flat_single",
                entry_glob="*.sh",
                execution_profile="shell",
            )
        ],
        profiles=[
            WizardProfileSpec(
                name="shell",
                label="Shell",
                command="bash {entry_name}",
            )
        ],
    )

    create_project_from_wizard(spec)

    ctx = create_project_context(project_root, servers_path)
    assert ctx.project_id == "wizard-demo"
    assert ctx.local_input_dir == (project_root / "inputs").resolve()
