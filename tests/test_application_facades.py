from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from jobdesk_app.application.facades import ServerSnapshot
from jobdesk_app.core.run import RunMode, RunSource, RunSpec, WorkflowKind
from jobdesk_app.core.submit import SubmitResult
from jobdesk_app.core.submit_payload import InputSource, SubmitPayload
from jobdesk_app.infrastructure.application_facades import DefaultRunApplication, DefaultSettingsApplication
from jobdesk_app.infrastructure.runtime.run_coordinator import RunOperationOutcome


def _payload() -> SubmitPayload:
    return SubmitPayload(
        kind="single",
        inputs=[InputSource(Path("/remote/input.gjf"), side="remote", kind="gjf")],
        program="gaussian",
        calc=SimpleNamespace(),
        workflow=None,
        output_dir=Path("."),
        server_id="wsl",
        remote_dir="/tmp/jobdesk-test",
    )


def _record():
    return SimpleNamespace(
        run_id="run-1",
        server_id="wsl",
        workflow_kind=WorkflowKind.gaussian,
        created_at="2026-09-04T00:00:00",
        status_summary={"submitted": 1},
        remote_dir="/tmp/jobdesk-test",
        local_dir="C:/workspace",
        command_template="g16 < {input} > {output}",
        run_dir=Path("C:/workspace/runs/run-1"),
        mode="selected_files",
    )


def test_submit_facade_owns_create_then_exactly_one_dispatch(monkeypatch, tmp_path):
    spec = RunSpec(
        server_id="wsl",
        remote_dir="/tmp/jobdesk-test",
        command_template="g16 < {input} > {output}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/input.gjf")],
    )
    batch = SimpleNamespace(
        ok=True,
        errors=[],
        specs=[spec],
        local_paths=[],
        upload_targets=[],
        yaml_local_path=None,
        yaml_remote_path=None,
    )
    monkeypatch.setattr(
        "jobdesk_app.infrastructure.application_facades.SubmitUseCase.execute",
        lambda _self, _payload: batch,
    )
    submitted = SubmitResult("run-1", 1, "/tmp/jobdesk-test", warnings=["warning"])
    client = SimpleNamespace(
        submit_with_outcome=lambda request: (
            request,
            RunOperationOutcome(submit_results=[submitted]),
        )
    )
    monkeypatch.setattr(
        "jobdesk_app.infrastructure.application_facades.SSHConfFlowClient",
        lambda *_args: client,
    )
    record = _record()

    class Coordinator:
        calls = 0

        def create_run(self, value, *, local_dir):
            assert value is spec
            assert local_dir == str(tmp_path)
            self.calls += 1
            return RunOperationOutcome(records=[record])

    service = SimpleNamespace(workspace_dir=tmp_path, load_tasks=lambda _run_id: [])
    coordinator = Coordinator()
    facade = DefaultRunApplication(service, coordinator, SimpleNamespace())

    outcome = facade.submit(_payload())

    assert outcome.ok
    assert coordinator.calls == 1
    assert outcome.value is not None
    assert outcome.value.submitted_task_count == 1
    assert outcome.value.warnings == ("warning",)
    assert [run.summary.run_id for run in outcome.value.runs] == ["run-1"]


def test_submit_facade_does_not_dispatch_when_durable_create_fails(monkeypatch, tmp_path):
    spec = SimpleNamespace(workflow_kind=WorkflowKind.gaussian)
    batch = SimpleNamespace(ok=True, errors=[], specs=[spec])
    monkeypatch.setattr(
        "jobdesk_app.infrastructure.application_facades.SubmitUseCase.execute",
        lambda _self, _payload: batch,
    )
    client = SimpleNamespace(
        submit_with_outcome=lambda _request: (_ for _ in ()).throw(AssertionError("dispatch must not run"))
    )
    monkeypatch.setattr(
        "jobdesk_app.infrastructure.application_facades.SSHConfFlowClient",
        lambda *_args: client,
    )
    from jobdesk_app.infrastructure.runtime.run_coordinator import OperationFailure

    failure = OperationFailure.from_text("cannot create", stage="create")
    coordinator = SimpleNamespace(create_run=lambda *_args, **_kwargs: RunOperationOutcome(errors=[failure]))
    service = SimpleNamespace(workspace_dir=tmp_path)
    facade = DefaultRunApplication(service, coordinator, SimpleNamespace())

    outcome = facade.submit(_payload())

    assert not outcome.ok
    assert [item.message for item in outcome.failures] == ["cannot create"]


def test_settings_mutation_preserves_unknown_and_advanced_yaml(monkeypatch, tmp_path):
    import yaml

    path = tmp_path / "servers.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "future_root": {"keep": True},
                "servers": {
                    "wsl": {
                        "display_name": "Old",
                        "host": "localhost",
                        "username": "old",
                        "scheduler": {"type": "slurm", "future": "keep"},
                        "future_server": 42,
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jobdesk_app.infrastructure.application_facades.get_default_servers_path",
        lambda: path,
    )
    monkeypatch.setattr(
        "jobdesk_app.infrastructure.config.servers.get_default_servers_path",
        lambda: path,
    )
    facade = DefaultSettingsApplication()

    outcome = facade.save_server(ServerSnapshot("wsl", "New", "127.0.0.1", 2222, "user"))

    assert outcome.ok
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["future_root"] == {"keep": True}
    assert raw["servers"]["wsl"]["scheduler"] == {"type": "slurm", "future": "keep"}
    assert raw["servers"]["wsl"]["future_server"] == 42
    assert raw["servers"]["wsl"]["display_name"] == "New"
    assert raw["servers"]["wsl"]["port"] == 2222


def test_run_subscription_is_idempotently_closed():
    events = []
    service = SimpleNamespace(load_tasks=lambda _run_id: [])
    facade = DefaultRunApplication(service, SimpleNamespace(), SimpleNamespace())

    subscription = facade.subscribe("run-1", events.append)
    for _ in range(100):
        if events:
            break
        import time

        time.sleep(0.01)
    subscription.close()
    subscription.close()
    facade.close()

    assert events
    assert events[0].run_id == "run-1"


def test_retry_facade_prepares_and_dispatches_once(monkeypatch, tmp_path):
    record = _record()
    calls = []
    coordinator = SimpleNamespace(retry_failed=lambda run_id: RunOperationOutcome(records=[record], changed_count=1))
    submitted = RunOperationOutcome(records=[record])
    client = SimpleNamespace(submit_with_outcome=lambda request: (calls.append(request.run_id), submitted))
    monkeypatch.setattr(
        "jobdesk_app.infrastructure.application_facades.SSHConfFlowClient",
        lambda *_args: client,
    )
    service = SimpleNamespace(workspace_dir=tmp_path, load_tasks=lambda _run_id: [])
    facade = DefaultRunApplication(service, coordinator, SimpleNamespace())

    outcome = facade.retry_failed("run-1")

    assert outcome.ok
    assert calls == ["run-1"]
