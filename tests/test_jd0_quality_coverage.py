"""Deterministic behavior coverage for the Phase 2 high-risk adapters.

These tests deliberately exercise local validation and protocol framing with
small in-memory fakes.  They do not launch a producer, scheduler, or
scientific workload.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

import jobdesk_app.infrastructure.runtime.ssh_confflow_client as client_module
from jobdesk_app.application.confflow_client import RemoteRunReference, SubmitRequest
from jobdesk_app.core import workflow_spec as workflow_module
from jobdesk_app.core.confflow_preflight import ConfFlowCapabilities
from jobdesk_app.core.configuration_binding import ConfigurationBinding
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.run import RunMode, RunSource, RunSpec, WorkflowKind
from jobdesk_app.infrastructure.remote.confflow_probe import ConfFlowCapabilityPreflightError
from jobdesk_app.infrastructure.remote.scheduler import ResourceSpec
from jobdesk_app.infrastructure.runtime.confflow_control import (
    ControlArtifact,
    ControlArtifactManifest,
    ControlEvent,
    ControlEventPage,
    ControlProtocolError,
    ControlSnapshot,
)
from jobdesk_app.infrastructure.runtime.confflow_control_state import load_state, save_state
from jobdesk_app.infrastructure.runtime.run_service import RunService
from jobdesk_app.infrastructure.runtime.ssh_confflow_client import (
    ConfFlowClientError,
    SSHConfFlowClient,
    _artifact_entries,
    _assert_path_under,
    _canonical_scheduler_type,
    _capability_from_state,
    _control_expected_identity,
    _control_worker_enabled,
    _is_safe_absolute_remote_path,
    _monotonic_snapshot,
    _pattern_matches,
    _remote_input_path,
    _state_worker_attempt_root,
    _state_worker_handoff,
    _state_worker_handoff_path,
    _state_worker_input_path,
    _state_worker_work_dir,
    _validate_safe_component,
    _worker_executable_for,
    _worker_handoff,
    _worker_handoff_digest,
    _worker_state_root,
    _worker_work_dir_name,
    _workflow_config_path,
)
from jobdesk_app.infrastructure.runtime.ssh_confflow_control import (
    SSHControlTransport,
    build_control_launcher_script,
    resolve_control_state_root,
)


def _wire(operation: str, **fields: object) -> str:
    return json.dumps(
        {"protocol_schema": "confflow.control.v1", "operation": operation, "ok": True, **fields},
        separators=(",", ":"),
    )


class _ControlSSH:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, command: str, timeout: int):
        self.commands.append(command)
        if "printf" in command:
            return SimpleNamespace(exit_code=0, stdout="/srv/confflow\n", stderr="")
        if "capabilities" in command:
            return SimpleNamespace(
                exit_code=0,
                stdout=_wire("capabilities", supported_protocols=["confflow.control.v1"]),
                stderr="",
            )
        if "events" in command:
            return SimpleNamespace(
                exit_code=0,
                stdout=_wire(
                    "events",
                    run_id="run-1",
                    revision=2,
                    state="running",
                    events=[{"cursor": "r00000000000000000001", "revision": 2, "type": "running"}],
                    next_cursor="r00000000000000000001",
                ),
                stderr="",
            )
        if "artifacts" in command:
            return SimpleNamespace(
                exit_code=0,
                stdout=_wire("artifacts", run_id="run-1", revision=3, state="completed", artifacts=[]),
                stderr="",
            )
        state = "prepared" if "prepare" in command else ("queued" if "resume" in command else "running")
        return SimpleNamespace(
            exit_code=0,
            stdout=_wire(
                "prepare" if "prepare" in command else command.split(" control ", 1)[1].split()[0],
                run_id="run-1",
                revision=1,
                state=state,
            ),
            stderr="",
        )


class _ControlSFTP:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.removed: list[str] = []

    def mkdir_p(self, path: str) -> None:
        del path

    def upload_file(self, local_path: Path, remote_path: str, **kwargs):
        del kwargs
        self.files[remote_path] = local_path.read_bytes()
        return SimpleNamespace(status="transferred", reason="ok")

    def remove_file(self, remote_path: str) -> None:
        self.removed.append(remote_path)
        self.files.pop(remote_path, None)


def test_ssh_control_transport_runs_each_protocol_operation_and_cleans_request() -> None:
    ssh = _ControlSSH()
    sftp = _ControlSFTP()
    transport = SSHControlTransport(
        ssh,
        sftp,
        executable="/opt/confflow/bin/confflow",
        state_root="/tmp/jobdesk-control/../jobdesk-control",
        env_init_scripts=["/etc/profile.d/confflow.sh"],
    )

    assert transport.capabilities() is True
    assert transport.prepare({"run_id": "run-1", "idempotency_key": "jobdesk.run-1"}).state == "prepared"
    assert transport.execute("run-1").state == "running"
    assert transport.status("run-1").state == "running"
    page = transport.events("run-1", after="cursor-0")
    assert page.next_cursor == "r00000000000000000001"
    assert transport.cancel("run-1").state == "running"
    assert transport.resume("run-1", checkpoint="checkpoint-1").state == "queued"
    assert transport.artifacts("run-1").artifacts == ()
    assert sftp.removed == ["/tmp/jobdesk-control/jobdesk-requests/run-1-jobdesk.run-1.json"]
    assert not sftp.files
    assert any("control events" in command and "--after cursor-0" in command for command in ssh.commands)


def test_control_state_root_and_launcher_validation_paths() -> None:
    ssh = _ControlSSH()
    assert resolve_control_state_root(ssh, env_init_scripts=["source /etc/profile"]) == (
        "/srv/confflow/.local/state/confflow/control"
    )

    failing = SimpleNamespace(
        run=lambda command, timeout: SimpleNamespace(exit_code=7, stdout="", stderr="permission denied")
    )
    with pytest.raises(ControlProtocolError, match="cannot resolve producer HOME"):
        resolve_control_state_root(failing)
    malformed = SimpleNamespace(
        run=lambda command, timeout: SimpleNamespace(exit_code=0, stdout="relative\nextra\n", stderr="")
    )
    with pytest.raises(ControlProtocolError, match="not an absolute path"):
        resolve_control_state_root(malformed)

    script = build_control_launcher_script(
        executable="/opt/confflow/bin/confflow",
        worker_executable="/opt/confflow/bin/confflow-control-worker",
        handoff_path="/tmp/control/handoff.json",
        state_root="/tmp/control/state",
        run_id="run-1",
        metadata_path="/tmp/control/meta.json",
        scheduler_type="nohup",
        resources=ResourceSpec(cpus=1),
        worker_only=True,
    )
    assert "setsid --wait" in script
    assert "/opt/confflow/bin/confflow-control-worker --state-root /tmp/control/state --run-id run-1" in script
    assert "--handoff /tmp/control/handoff.json" in script
    assert "/tmp/control/meta.json" in script
    assert '"execution_state":"started"' in script
    assert '"execute_rc":0' in script
    assert '"worker_started":true' in script
    with pytest.raises(ValueError, match="Unknown scheduler"):
        build_control_launcher_script(
            executable="confflow",
            worker_executable="worker",
            handoff_path="/tmp/handoff",
            state_root="/tmp/state",
            run_id="run-1",
            metadata_path="/tmp/meta",
            scheduler_type="unknown",
            resources=ResourceSpec(cpus=1),
        )


def test_workflow_local_helpers_cover_legacy_shapes_and_units() -> None:
    """Unit evidence for pure migration helpers; public YAML flow is tested separately."""
    parse_mem = workflow_module._parse_mem_mb_local
    assert [parse_mem(value) for value in (None, "", 512, 1.5, "2GB", "512KB", "2B", "bad")] == [
        1024,
        1024,
        512,
        1,
        2048,
        1,
        1,
        1024,
    ]
    format_mem = workflow_module._format_mem_mb
    assert [format_mem(value) for value in (None, "bad", 1024, 1536, 500)] == ["4GB", "4GB", "1GB", "1.5GB", "500MB"]
    split = workflow_module._split_keyword_into_form
    assert split(None, has_method=True, has_basis=True) == ("", "", "")
    assert split("", has_method=False, has_basis=False) == ("", "", "")
    assert split("B3LYP def2-SVP Opt", has_method=True, has_basis=True) == (
        "B3LYP",
        "def2-SVP",
        "Opt",
    )
    assert split("raw keyword", has_method=False, has_basis=False) == ("raw", "", "keyword")
    assert workflow_module._iprog_token("g16") == "gaussian"
    assert workflow_module._iprog_token("orca") == "orca"
    assert workflow_module._iprog_token("custom") == "custom"
    assert workflow_module._iprog_token("") == "gaussian"
    assert workflow_module._itask_token("optfreq") == "opt_freq"
    assert workflow_module._itask_token("  SP ") == "sp"
    assert workflow_module._token_to_step("", idx=3)["name"] == "step_03"
    assert workflow_module._token_to_step("custom")["params"] == {"itask": "custom"}

    canonical = workflow_module._normalise_yaml_to_schema(
        {"global": {"nproc": "4", "memory_mb": 2048}, "steps": ["opt", {"name": "x", "iprog": "orca"}, 3]}
    )
    assert canonical["global"] == {"cores_per_task": 4, "total_memory": "2GB"}
    assert canonical["steps"][1]["params"] == {"iprog": "orca", "itask": "sp"}
    flat = workflow_module._normalise_yaml_to_schema({"program": "orca", "keyword": "B3LYP def2-SVP", "steps": ["sp"]})
    assert flat["steps"][0]["params"]["keyword"] == "B3LYP def2-SVP"
    nested = workflow_module._normalise_yaml_to_schema(
        {"calc": {"program": "gaussian", "keyword": "HF 3-21G", "steps": ["confgen"]}}
    )
    assert nested["steps"][0]["type"] == "confgen"
    assert workflow_module._normalise_yaml_to_schema([]) == {"global": {}, "steps": []}


def test_workflow_filter_validation_and_dry_run_error_paths(monkeypatch, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid workflow YAML"):
        workflow_module._validate_confflow_semantics({"global": {}, "steps": [{"name": "bad", "type": "wat"}]})
    with pytest.raises(ValueError, match="step inputs"):
        workflow_module._normalise_steps_list([{"name": "bad", "inputs": "not-a-list"}])
    with pytest.raises(ValueError, match="steps must be a list"):
        workflow_module._normalise_steps_list(("sp",))

    if not workflow_module._CONFFLOW_AVAILABLE:
        pytest.skip("ConfFlow model dependency unavailable")
    spec = workflow_module.WorkflowSpec.from_form(
        work_dir_name="quality",
        program="gaussian",
        method="HF",
        basis="3-21G",
        charge=0,
        multiplicity=1,
        nproc=2,
        memory_mb=1536,
        steps=("sp",),
        extra_options={"energy_window": 2.0, "custom_flag": True},
        extra_keyword="TightSCF",
        freeze=[1, 2],
        max_parallel_jobs=3,
    )
    filtered = spec._filter_user_facing_global(
        {
            "charge": 0,
            "multiplicity": 1,
            "freeze": [1],
            "custom_flag": True,
            "energy_tolerance": 0.1,
            "empty": "",
        }
    )
    assert filtered == {"freeze": [1], "custom_flag": True}
    filtered_steps = spec._filter_user_facing_steps(
        [
            {"name": "sp", "type": "calc", "params": {"itask": "sp", "iprog": "gaussian", "keyword": "HF"}},
            {"name": "custom", "type": "calc", "params": {"itask": "opt", "cores_per_task": 4}, "inputs": []},
        ],
        global_dict={"cores_per_task": 2},
        omit_type_calc=True,
    )
    assert filtered_steps[0] == {"name": "sp", "params": {"keyword": "HF"}}
    assert filtered_steps[1]["inputs"] == []
    assert "global:" not in spec.to_user_yaml()
    assert spec.to_form()["work_dir_name"] == "quality"

    with monkeypatch.context() as patcher:
        patcher.setattr(
            workflow_module.WorkflowSpec,
            "to_yaml",
            lambda self: (_ for _ in ()).throw(RuntimeError("broken")),
        )
        report = spec.dry_run()
    assert report.ok is False and "RuntimeError" in report.error
    target = tmp_path / "nested" / "workflow.yaml"
    assert workflow_module.write_workflow_yaml(spec, target) == target
    written = target.read_text(encoding="utf-8")
    assert written == spec.to_yaml()
    import yaml

    payload = yaml.safe_load(written)
    assert payload["global"] == {
        "cores_per_task": 2,
        "total_memory": "1.5GB",
        "charge": 0,
        "multiplicity": 1,
        "freeze": [1, 2],
        "max_parallel_jobs": 3,
        "energy_window": 2.0,
        "custom_flag": True,
    }
    assert payload["steps"] == [
        {
            "name": "sp",
            "type": "calc",
            "params": {"itask": "sp", "keyword": "HF 3-21G TightSCF", "iprog": "gaussian"},
        }
    ]


def test_workflow_public_yaml_round_trip_and_atomic_write(tmp_path: Path) -> None:
    """Exercise the public YAML parse/serialize/write chain end to end."""
    if not workflow_module._CONFFLOW_AVAILABLE:
        pytest.skip("ConfFlow model dependency unavailable")
    import yaml

    source = """\
global:
  cores_per_task: 2
  total_memory: 1GB
  charge: 0
  multiplicity: 1
steps:
  - name: optimize
    type: calc
    params:
      iprog: gaussian
      itask: opt
      keyword: HF 3-21G
"""
    spec = workflow_module.WorkflowSpec.from_yaml(source)
    rendered = spec.to_yaml()
    expected = yaml.safe_load(source)
    assert yaml.safe_load(rendered) == expected

    target = tmp_path / "nested" / "workflow.yaml"
    assert workflow_module.write_workflow_yaml(spec, target) == target
    assert target.read_text(encoding="utf-8") == rendered
    assert yaml.safe_load(target.read_text(encoding="utf-8")) == expected
    assert not target.with_suffix(target.suffix + ".tmp").exists()


def _task(task_id: str = "task-1", *, work_dir: str = "/remote/work/task-1"):
    return SimpleNamespace(
        task_id=task_id,
        status=TaskStatus.submitted,
        remote_workflow_dir=work_dir,
        remote_work_dir="/remote/input",
        remote_task_files=["input.xyz"],
        remote_config_path="/remote/workflow.yaml",
        remote_job_dir="/remote/job",
        remote_state_path="/remote/state",
        remote_result_paths=["result.json"],
        remote_job_id="job-1",
        error_message=None,
        model_copy=lambda update=None, deep=False: SimpleNamespace(
            **{**vars(_task(task_id, work_dir=work_dir)), **(update or {})}
        ),
    )


def test_confflow_client_helper_validation_and_artifact_paths(tmp_path: Path) -> None:
    """Unit evidence for fail-closed path/provenance helpers, not facade coverage."""
    assert _is_safe_absolute_remote_path("/a/b")
    assert not _is_safe_absolute_remote_path("/a/../b")
    assert _canonical_scheduler_type("sbatch") == "slurm"
    assert _canonical_scheduler_type("qsub") == "pbs"
    with pytest.raises(ValueError, match="Unknown scheduler"):
        _canonical_scheduler_type("grid")
    assert _worker_executable_for("/opt/confflow") == "/opt/confflow-control-worker"
    assert _worker_executable_for("confflow") == "confflow-control-worker"
    assert _worker_state_root("/tmp/control", "run-1") == "/tmp/jobdesk-run-1/state"
    with pytest.raises(ConfFlowClientError):
        _worker_state_root("relative", "run-1")
    with pytest.raises(ConfFlowClientError):
        _validate_safe_component("../bad", "component")
    _validate_safe_component("valid.name-1", "component")
    assert _worker_work_dir_name(_task(work_dir="/remote/work/terminal")) == "terminal"
    assert _worker_work_dir_name(_task(work_dir="/remote/work/mol one_confflow_work")) == ("mol one_confflow_work")
    assert _remote_input_path(_task()) == "/remote/input/input.xyz"
    assert (
        _remote_input_path(
            SimpleNamespace(
                remote_source_path="/shared/source files/input one.xyz",
                remote_task_files=["input one.xyz"],
                remote_work_dir="/remote/workspace",
            )
        )
        == "/shared/source files/input one.xyz"
    )
    with pytest.raises(ConfFlowClientError, match="exact input source path is unsafe"):
        _remote_input_path(
            SimpleNamespace(
                remote_source_path="/shared/../escape.xyz",
                remote_task_files=["escape.xyz"],
                remote_work_dir="/remote/workspace",
            )
        )
    with pytest.raises(ConfFlowClientError):
        _remote_input_path(SimpleNamespace(remote_task_files=["../bad.xyz"], remote_work_dir="/remote/input"))
    assert _workflow_config_path([_task()]) == "/remote/workflow.yaml"
    with pytest.raises(ConfFlowClientError):
        _workflow_config_path([SimpleNamespace(remote_config_path="relative.yaml")])

    handoff = _worker_handoff(
        run_id="run-1",
        workflow_path="/attempt/input/workflow.yaml",
        workflow_digest="a" * 64,
        input_path="/attempt/input/input.xyz",
        input_digest="b" * 64,
        work_dir="/attempt/results/task-1",
        task_id="task-1",
    )
    assert _worker_handoff_digest(handoff, "workflow_config") == "a" * 64
    assert _worker_handoff_digest(handoff, "tasks") == "b" * 64
    with pytest.raises(ConfFlowClientError):
        _worker_handoff_digest({}, "workflow_config")
    with pytest.raises(ConfFlowClientError):
        _worker_handoff_digest({"tasks": []}, "tasks")
    with pytest.raises(ConfFlowClientError):
        _worker_handoff_digest({"workflow_config": {"sha256": "bad"}}, "workflow_config")
    assert _state_worker_handoff({"worker_handoff": handoff}) == handoff
    assert (
        _state_worker_handoff_path({"input_manifest_path": "/attempt/input/worker-handoff.json"})
        == "/attempt/input/worker-handoff.json"
    )
    assert _state_worker_attempt_root({"input_manifest_path": "/attempt/input/worker-handoff.json"}) == "/attempt"
    assert _state_worker_work_dir({"worker_work_dir": "/attempt/results/task-1"}) == "/attempt/results/task-1"
    assert _state_worker_input_path(handoff) == "/attempt/input/input.xyz"
    with pytest.raises(ConfFlowClientError):
        _state_worker_handoff({})
    with pytest.raises(ConfFlowClientError):
        _state_worker_handoff_path({"input_manifest_path": "relative"})
    with pytest.raises(ConfFlowClientError):
        _state_worker_work_dir({"worker_work_dir": "relative"})
    with pytest.raises(ConfFlowClientError):
        _state_worker_input_path({"tasks": [{"input_xyz": "relative"}]})

    capability = SimpleNamespace(control_worker=True)
    assert _control_worker_enabled(capability, None)
    assert _control_worker_enabled(None, {"capability": {"capabilities": {"control_worker": True}}})
    assert not _control_worker_enabled(None, {"capability": []})
    identity = _control_expected_identity({"sha256": "A" * 64, "realpath": "/opt/confflow", "device": 8, "inode": 9})
    assert identity == {"sha256": "a" * 64, "realpath": "/opt/confflow", "device_inode": "8:9"}
    with pytest.raises(ConfFlowClientError):
        _control_expected_identity({"sha256": "bad"})
    assert (
        _monotonic_snapshot(ControlSnapshot("run-1", 1, "queued"), current_revision=2, current_state="running").state
        == "running"
    )
    assert (
        _monotonic_snapshot(ControlSnapshot("run-1", 2, "queued"), current_revision=2, current_state="running").state
        == "running"
    )
    assert (
        _monotonic_snapshot(ControlSnapshot("run-1", 3, "running"), current_revision=2, current_state="completed").state
        == "completed"
    )
    assert _pattern_matches("task-1/result.json", "*.json")
    artifact_entries = _artifact_entries(
        [ControlArtifact("b", "x", "a" * 64, 1, "text"), ControlArtifact("a", "y", "b" * 64, 1, "text")]
    )
    assert [(entry.task_id, entry.remote_paths) for entry in artifact_entries] == [("a", ("y",)), ("b", ("x",))]
    _assert_path_under("/attempt", "/attempt/input/x", "path")
    with pytest.raises(ConfFlowClientError):
        _assert_path_under("/attempt", "/other/x", "path")


class _StageSFTP:
    def __init__(self, sources: dict[str, bytes] | None = None, *, mode: int = stat.S_IFREG | 0o600) -> None:
        self.sources = sources or {}
        self.mode = mode
        self.files: dict[str, bytes] = {}
        self.directories: list[str] = []

    def mkdir_p(self, path: str) -> None:
        self.directories.append(path)

    def lstat(self, path: str):
        if path in self.sources or path in self.files:
            return SimpleNamespace(st_mode=self.mode)
        return None

    def download_file(self, remote_path: str, local_path: Path, **kwargs):
        del kwargs
        local_path.write_bytes(self.sources[remote_path])
        return SimpleNamespace(status="transferred", reason="ok")

    def upload_file(self, local_path: Path, remote_path: str, **kwargs):
        del kwargs
        self.files[remote_path] = local_path.read_bytes()
        return SimpleNamespace(status="transferred", reason="ok")


class _PublicSFTP(_StageSFTP):
    def lstat(self, path: str):
        if (
            path in self.sources
            or path in self.files
            or any(
                path.startswith(source.rstrip("/") + "/") or source.startswith(path.rstrip("/") + "/")
                for source in self.sources
            )
        ):
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o600)
        return None

    def stat(self, path: str):
        if path in self.sources:
            return SimpleNamespace(st_size=len(self.sources[path]))
        if path not in self.files:
            return None
        return SimpleNamespace(st_size=len(self.files[path]))

    def read_file_bytes(self, path: str, max_bytes: int = 65536) -> bytes:
        return self.files[path][:max_bytes]


def test_worker_staging_rejects_bad_sources_and_handoff_metadata(tmp_path: Path) -> None:
    """Unit evidence for staging invariants; public submit coverage is separate."""
    from jobdesk_app.infrastructure.runtime.ssh_confflow_client import (
        _stage_remote_file,
        _upload_control_worker_handoff,
    )

    source = b"source\n"
    digest = __import__("hashlib").sha256(source).hexdigest()
    sftp = _StageSFTP({"/source/workflow.yaml": source})
    _stage_remote_file(
        sftp, "/source/workflow.yaml", tmp_path / "workflow.yaml", "/attempt/input/workflow.yaml", digest
    )
    assert (tmp_path / "workflow.yaml").read_bytes() == source
    with pytest.raises(ConfFlowClientError, match="not a regular file"):
        _stage_remote_file(
            _StageSFTP({"/source": source}, mode=stat.S_IFLNK | 0o777),
            "/source",
            tmp_path / "bad",
            "/attempt/input/bad",
            digest,
        )

    failed_sftp = _StageSFTP({"/source": source})
    failed_sftp.download_file = lambda *args, **kwargs: SimpleNamespace(status="failed", reason="offline")
    with pytest.raises(ConfFlowClientError, match="source download failed"):
        _stage_remote_file(failed_sftp, "/source", tmp_path / "failed", "/attempt/input/failed", digest)
    with pytest.raises(ConfFlowClientError, match="digest mismatch"):
        _stage_remote_file(sftp, "/source/workflow.yaml", tmp_path / "wrong", "/attempt/input/wrong", "0" * 64)

    handoff = _worker_handoff(
        run_id="run-1",
        workflow_path="/attempt/input/workflow.yaml",
        workflow_digest="a" * 64,
        input_path="/attempt/input/input.xyz",
        input_digest="b" * 64,
        work_dir="/attempt/results/task-1",
        task_id="task-1",
    )
    kwargs = dict(
        worker_handoff=handoff,
        handoff_path="/attempt/input/worker-handoff.json",
        attempt_root="/attempt",
        workflow_path="/attempt/input/workflow.yaml",
        input_path="/attempt/input/input.xyz",
        remote_workflow_path="/source/workflow.yaml",
        remote_input_path="/source/input.xyz",
        workflow_digest="a" * 64,
        input_digest="b" * 64,
        handoff_bytes=b"{}",
    )
    # The in-memory path intentionally exercises the producer-less transport.
    uploaded = _StageSFTP()
    _upload_control_worker_handoff(uploaded, None, **kwargs)
    assert uploaded.files["/attempt/input/worker-handoff.json"] == b"{}"
    for digest_field, expected in (("workflow_digest", "0" * 64), ("input_digest", "0" * 64)):
        bad = dict(kwargs)
        bad[digest_field] = expected
        with pytest.raises(ConfFlowClientError, match="digest changed"):
            _upload_control_worker_handoff(_StageSFTP(), None, **bad)
    bad = dict(kwargs, worker_handoff={**handoff, "tasks": []})
    with pytest.raises(ConfFlowClientError, match="exactly one task"):
        _upload_control_worker_handoff(_StageSFTP(), None, **bad)
    bad = dict(kwargs, worker_handoff={**handoff, "workflow_config": {"path": "/other", "sha256": "a" * 64}})
    with pytest.raises(ConfFlowClientError, match="workflow path"):
        _upload_control_worker_handoff(_StageSFTP(), None, **bad)
    bad = dict(kwargs, worker_handoff={**handoff, "tasks": [{**handoff["tasks"][0], "input_xyz": "/other/input.xyz"}]})
    with pytest.raises(ConfFlowClientError, match="input path"):
        _upload_control_worker_handoff(_StageSFTP(), None, **bad)
    bad = dict(kwargs, worker_handoff={**handoff, "tasks": [{**handoff["tasks"][0], "work_dir": "relative"}]})
    with pytest.raises(ConfFlowClientError, match="work directory"):
        _upload_control_worker_handoff(_StageSFTP(), None, **bad)
    with pytest.raises(ConfFlowClientError, match="malformed"):
        _assert_path_under(None, "/attempt/input/x", "path")


def test_event_cursor_and_artifact_download_fail_closed(tmp_path: Path) -> None:
    """Unit evidence for protocol/artifact safety; public handle behavior is separate."""
    from jobdesk_app.infrastructure.runtime.ssh_confflow_client import (
        _assert_remote_not_symlink,
        _assert_safe_relative_artifact_path,
        _download_control_artifacts,
        _validate_event_page_cursor,
    )

    event = SimpleNamespace(cursor="r00000000000000000001", revision=1)
    page = SimpleNamespace(events=[event], next_cursor=event.cursor)
    with pytest.raises(ControlProtocolError, match="cursor is malformed"):
        _validate_event_page_cursor(page, "bad/")
    with pytest.raises(ControlProtocolError, match="repeats"):
        _validate_event_page_cursor(page, event.cursor)
    with pytest.raises(ControlProtocolError, match="next_cursor"):
        _validate_event_page_cursor(SimpleNamespace(events=[event], next_cursor="other"), None)
    with pytest.raises(ControlProtocolError, match="strictly increasing"):
        _validate_event_page_cursor(
            SimpleNamespace(
                events=[SimpleNamespace(cursor="a", revision=1), SimpleNamespace(cursor="b", revision=1)],
                next_cursor="b",
            ),
            None,
        )

    _assert_safe_relative_artifact_path("nested/result.json")
    for unsafe in ("../escape", "/absolute", "nested\\result", "nested/./result"):
        with pytest.raises(ValueError, match="unsafe"):
            _assert_safe_relative_artifact_path(unsafe)

    class Service:
        workspace_dir = tmp_path

        def __init__(self, tasks):
            self.tasks = tasks
            self.mutations = 0

        def load_tasks(self, run_id):
            return self.tasks

        def mutate_tasks(self, run_id, mutation):
            self.mutations += 1
            self.tasks = mutation(self.tasks)

    class DownloadSFTP:
        def __init__(self, content: bytes = b"ok\n", *, mode: int = stat.S_IFREG | 0o600, size: int | None = None):
            self.content = content
            self.mode = mode
            self.size = len(content) if size is None else size

        def lstat(self, path):
            return SimpleNamespace(st_mode=self.mode)

        def stat(self, path):
            return SimpleNamespace(st_size=self.size)

        def download_file(self, remote_path, local_path, **kwargs):
            del kwargs
            local_path.write_bytes(self.content)
            return SimpleNamespace(status="transferred", reason="ok")

    content = b"result\n"
    artifact = ControlArtifact(
        "task-1", "result.json", __import__("hashlib").sha256(content).hexdigest(), len(content), "text/plain"
    )
    service = Service([_task()])
    transfers, failures = _download_control_artifacts(service, "run-1", (artifact,), [], DownloadSFTP(content))
    assert len(transfers) == 1 and not failures and service.mutations == 1
    assert _download_control_artifacts(service, "run-1", (artifact,), ["*.out"], DownloadSFTP(content)) == ([], [])
    bad_cases = [
        (
            ControlArtifact("task-1", "../bad", artifact.sha256, artifact.size, artifact.content_schema),
            DownloadSFTP(content),
        ),
        (artifact, DownloadSFTP(content, mode=stat.S_IFLNK | 0o777)),
        (artifact, DownloadSFTP(content, size=99)),
        (
            ControlArtifact("unknown", "result.json", artifact.sha256, artifact.size, artifact.content_schema),
            DownloadSFTP(content),
        ),
        (
            ControlArtifact("task-1", "result.json", "0" * 64, artifact.size, artifact.content_schema),
            DownloadSFTP(content),
        ),
    ]
    for bad_artifact, bad_sftp in bad_cases:
        _transfers, bad_failures = _download_control_artifacts(service, "run-1", (bad_artifact,), [], bad_sftp)
        if bad_artifact.terminal == "unknown":
            assert not bad_failures
        else:
            assert bad_failures
    ambiguous = Service([_task("a", work_dir="/remote/work/a"), _task("b", work_dir="/remote/work/b")])
    _, ambiguous_failures = _download_control_artifacts(ambiguous, "run-1", (artifact,), [], DownloadSFTP(content))
    assert ambiguous_failures
    missing = DownloadSFTP(content)
    missing.lstat = lambda path: None
    with pytest.raises(ValueError, match="missing"):
        _assert_remote_not_symlink(missing, "/remote/work/task-1", "result.json")


class _ClientService:
    def __init__(self) -> None:
        self.record = SimpleNamespace(
            run_id="run-1",
            server_id="server",
            remote_dir="/remote/project",
            workflow_kind=None,
            status_summary={"submitted": 1},
        )
        self.tasks = [_task()]
        self.saved_tasks: list[object] = []
        self.updated_runs: list[object] = []

    def load_run(self, run_id: str):
        return self.record

    def load_tasks(self, run_id: str):
        return self.tasks

    def load_run_provenance(self, run_id: str):
        return None

    def load_configuration_binding(self, run_id: str):
        return SimpleNamespace()

    def update_run(self, record):
        self.updated_runs.append(record)

    def mutate_tasks(self, run_id, mutation):
        self.tasks = mutation(self.tasks)
        self.saved_tasks.append(self.tasks)


class _ClientCoordinator:
    def __init__(self) -> None:
        self.service = _ClientService()
        self.capability_result: object = object()
        self.server = SimpleNamespace(
            confflow_executable="",
            env_init_scripts=[],
            scheduler=SimpleNamespace(
                type="slurm",
                default_cpus=2,
                default_memory_mb=4096,
                default_walltime_minutes=60,
                default_partition="chem",
                default_account="acct",
                default_gpus=1,
                extra_directives=["--exclusive"],
            ),
        )

    def probe_capabilities(self, server_id: str, *, require_dag: bool = False):
        del server_id, require_dag
        if isinstance(self.capability_result, BaseException):
            raise self.capability_result
        return self.capability_result

    def server_config(self, server_id: str):
        del server_id
        return self.server

    def verify_configuration_binding(self, server_id: str, binding: object, *, require_dag: bool = False) -> None:
        del server_id, binding, require_dag


def _public_configuration_binding() -> ConfigurationBinding:
    return ConfigurationBinding(
        server_id="server",
        content_sha256="a" * 64,
        content_schema="confflow.config.validate-response.v1",
        contract_id="confflow.workflow-config",
        contract_version="2",
        schema_id="https://schemas.confflow.dev/config/v2/workflow.schema.json",
        schema_sha256="b" * 64,
        fixture_set="confflow.config_contract.v2",
        fixture_sha256="c" * 64,
        source="remote-cli",
        configured_executable="confflow",
        resolved_executable="/opt/confflow/bin/confflow",
        canonical_executable_identity_json='{"path":"/opt/confflow/bin/confflow"}',
        canonical_producer_provenance_json='{"version":"2.0.0"}',
        validated_at="2026-08-20T00:00:00+00:00",
    )


@dataclass
class _PublicControlTransport:
    sftp: _PublicSFTP
    artifact_terminal: str = "task-1"
    status_snapshots: list[ControlSnapshot] = field(default_factory=list)
    event_pages: list[ControlEventPage] = field(default_factory=list)
    event_afters: list[str | None] = field(default_factory=list)
    prepared: list[dict[str, object]] = field(default_factory=list)
    cancel_calls: list[str] = field(default_factory=list)

    def prepare(self, request: dict[str, object]) -> ControlSnapshot:
        self.prepared.append(request)
        return ControlSnapshot(str(request["run_id"]), 1, "prepared")

    def execute(self, run_id: str) -> ControlSnapshot:
        return ControlSnapshot(run_id, 1, "prepared")

    def status(self, run_id: str) -> ControlSnapshot:
        if self.status_snapshots:
            return self.status_snapshots.pop(0)
        return ControlSnapshot(run_id, 1, "running")

    def events(self, run_id: str, *, after: str | None) -> ControlEventPage:
        del run_id
        assert self.event_pages
        self.event_afters.append(after)
        page = self.event_pages.pop(0)
        return page

    def cancel(self, run_id: str) -> ControlSnapshot:
        self.cancel_calls.append(run_id)
        return ControlSnapshot(run_id, 4, "cancelled")

    def resume(self, run_id: str, *, checkpoint: str | None) -> ControlSnapshot:
        del checkpoint
        return ControlSnapshot(run_id, 7, "queued")

    def artifacts(self, run_id: str) -> ControlArtifactManifest:
        result_json = b'{"energy": -1}\n'
        result_out = b"out\n"
        return ControlArtifactManifest(
            ControlSnapshot(run_id, 3, "running"),
            (
                ControlArtifact(
                    self.artifact_terminal,
                    "result.json",
                    hashlib.sha256(result_json).hexdigest(),
                    len(result_json),
                    "application/json",
                ),
                ControlArtifact(
                    self.artifact_terminal,
                    "result.out",
                    hashlib.sha256(result_out).hexdigest(),
                    len(result_out),
                    "text/plain",
                ),
            ),
        )


class _PublicScheduler:
    def __init__(self, service: RunService, sftp: _PublicSFTP) -> None:
        self.service = service
        self.sftp = sftp
        self.calls: list[tuple[object, str, ResourceSpec]] = []

    def submit(self, ssh, script_path: str, resources: ResourceSpec) -> str:
        self.calls.append((ssh, script_path, resources))
        state = load_state(self.service, "run-1")
        assert state is not None
        launcher = state["launcher"]
        assert isinstance(launcher, dict)
        self.sftp.files[str(launcher["metadata_path"])] = json.dumps(
            {
                "content_schema": "jobdesk.confflow.launcher.v1",
                "run_id": "run-1",
                "scheduler_type": str(state["scheduler_type"]),
                "scheduler_job_id": "public-job-1",
                "pid": "public-job-1",
                "state_root": str(launcher["state_root"]),
                "command": str(launcher["command"]),
            }
        ).encode("utf-8")
        return "public-job-1"

    def poll(self, ssh, job_id: str):
        del ssh, job_id
        return "running"

    def cancel(self, ssh, job_id: str) -> None:
        del ssh, job_id


def _public_run_spec() -> RunSpec:
    return RunSpec(
        server_id="server",
        remote_dir="/remote/project",
        command_template="confflow {name} -c {path}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/project/methane.xyz")],
        supporting_sources=[RunSource("/remote/project/workflow.yaml")],
        workflow_kind=WorkflowKind.confflow,
    )


def _seed_public_control_state(service: RunService) -> None:
    attempt_root = "/home/test/.local/state/confflow/jobdesk-run-1"
    save_state(
        service,
        "run-1",
        {
            "content_schema": "jobdesk.confflow.backend.v1",
            "run_id": "run-1",
            "backend": "control",
            "protocol_schema": "confflow.control.v1",
            "state_locator": "/home/test/.local/state/confflow/control",
            "idempotency_key": "jobdesk.run-1",
            "producer_identity": {"sha256": "d" * 64},
            "capability": {"capabilities": {"control_worker": True}},
            "input_manifest_path": f"{attempt_root}/input/worker-handoff.json",
            "worker_attempt_root": attempt_root,
            "worker_work_dir": f"{attempt_root}/results/methane_confflow_work",
            "worker_executable": "/opt/confflow/bin/confflow-control-worker",
            "worker_handoff": {
                "content_schema": "confflow.control.worker-handoff.v1",
                "run_id": "run-1",
                "workflow_config": {"path": f"{attempt_root}/input/workflow.yaml", "sha256": "b" * 64},
                "tasks": [
                    {
                        "task_id": "methane",
                        "input_xyz": f"{attempt_root}/input/methane.xyz",
                        "work_dir": f"{attempt_root}/results/methane_confflow_work",
                        "sha256": "c" * 64,
                    }
                ],
            },
            "revision": 0,
            "state": "prepared",
        },
    )


def test_public_client_submit_attach_status_cancel_refresh_and_artifacts(tmp_path: Path, monkeypatch) -> None:
    """Prove the public facade reaches durable submit and handle operations."""
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run_with_configuration_binding(_public_run_spec(), _public_configuration_binding(), run_id="run-1")
    _seed_public_control_state(service)
    task_id = service.repository.load_tasks("run-1")[0].task_id
    sftp = _PublicSFTP()
    transport = _PublicControlTransport(
        sftp,
        artifact_terminal=task_id,
        status_snapshots=[
            ControlSnapshot("run-1", 2, "running"),
            ControlSnapshot("run-1", 6, "cancelled"),
        ],
        event_pages=[
            ControlEventPage(
                ControlSnapshot("run-1", 3, "running"),
                (ControlEvent("cursor-1", 3, "running"),),
                "cursor-1",
            ),
            ControlEventPage(
                ControlSnapshot("run-1", 5, "cancelled"),
                (ControlEvent("cursor-2", 5, "cancelled"),),
                "cursor-2",
            ),
        ],
    )
    scheduler = _PublicScheduler(service, sftp)
    coordinator = SimpleNamespace(service=service, verify_configuration_binding=lambda *args, **kwargs: None)
    client = SSHConfFlowClient(
        coordinator,
        "server",
        control_transport_factory=lambda run_id, locator: transport,
        scheduler_factory=lambda scheduler_type: scheduler,
        backend_mode="control",
    )
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    submitted = client.submit(SubmitRequest("run-1"))
    assert submitted.run_id == "run-1"
    assert len(transport.prepared) == 1
    assert len(scheduler.calls) == 1
    persisted = load_state(service, "run-1")
    assert persisted is not None
    assert persisted["dispatch_state"] == "submitted"
    assert persisted["scheduler_job_id"] == "public-job-1"
    assert str(persisted["launcher"]["script_path"]) in sftp.files
    duplicate, duplicate_outcome = client.submit_with_outcome(SubmitRequest("run-1"))
    assert duplicate.run_id == "run-1"
    assert duplicate_outcome.errors == []
    assert len(scheduler.calls) == 1

    attached = client.attach("run-1")
    restored = client.restore_handle(attached.to_dict())
    assert restored.run_id == attached.run_id
    running = attached.status()
    assert running.producer_state == "running"
    assert running.revision == 2
    events = attached.events()
    assert events.events == ({"cursor": "cursor-1", "revision": 3, "type": "running"},)
    assert transport.event_afters == [None]
    assert load_state(service, "run-1")["cursor"] == "cursor-1"
    artifacts = attached.artifacts()
    assert [(entry.task_id, entry.remote_paths) for entry in artifacts.entries] == [
        (task_id, ("result.json", "result.out")),
    ]
    task = service.repository.load_tasks("run-1")[0]
    remote_root = str(task.remote_workflow_dir).rstrip("/")
    sftp.sources[posixpath.join(remote_root, "result.json")] = b'{"energy": -1}\n'
    sftp.sources[posixpath.join(remote_root, "result.out")] = b"out\n"
    download = client.download_outcome(attached, ["*.json"])
    assert download.errors == []
    assert len(download.transfer_records) == 1
    local_work_dir = str(task.remote_workflow_dir).rstrip("/").rsplit("/", 1)[-1]
    assert (
        service.workspace_dir / "results" / "run-1" / local_work_dir / "result.json"
    ).read_bytes() == b'{"energy": -1}\n'
    assert service.repository.load_tasks("run-1")[0].status == TaskStatus.downloaded
    downloaded_snapshot = attached.download(["*.json"])
    assert downloaded_snapshot.producer_state == "cancelled"

    cancelled = attached.cancel()
    assert cancelled.producer_state == "cancelled"
    assert transport.cancel_calls == ["run-1"]
    resumed = attached.resume(checkpoint="checkpoint-1")
    assert resumed.producer_state == "cancelled"
    refresh = client.refresh_outcome(attached, [], download=True)
    assert refresh.errors == []
    assert len(refresh.transfer_records) == 2
    assert transport.event_afters == [None, "cursor-1"]
    assert load_state(service, "run-1")["cursor"] == "cursor-2"
    cancel_outcome = client.cancel_outcome(attached)
    assert cancel_outcome.errors == []
    assert cancel_outcome.changed_count == 1


def test_client_facade_probe_attach_restore_and_outcome_errors(monkeypatch) -> None:
    coordinator = _ClientCoordinator()
    client = SSHConfFlowClient(coordinator, "server")
    with pytest.raises(ValueError, match="only the control"):
        SSHConfFlowClient(coordinator, "server", backend_mode="legacy")

    with pytest.raises(ConfFlowClientError, match="capability selection failed"):
        client.probe()
    coordinator.capability_result = ValueError("probe failed")
    with pytest.raises(ConfFlowClientError, match="capability selection failed"):
        client.probe()
    coordinator.capability_result = ConfFlowCapabilityPreflightError("preflight failed")
    with pytest.raises(ConfFlowClientError, match="preflight failed"):
        client.probe_capabilities("server")
    capability = ConfFlowCapabilities(4, "2.0.0", True, True, True, control_worker=True)
    coordinator.capability_result = capability
    monkeypatch.setattr(client, "_resolve_control_state_locator", lambda value: "/durable/control")
    assert client.probe(require_dag=True) is capability

    coordinator.service.record.server_id = "other"
    with pytest.raises(ConfFlowClientError, match="belongs to server"):
        client.attach("run-1")
    coordinator.service.record.server_id = "server"
    monkeypatch.setattr(client_module, "load_state", lambda service, run_id: None)
    with pytest.raises(ConfFlowClientError, match="no durable control state"):
        client.attach("run-1")

    saved = RemoteRunReference("server", "run-1", "confflow.control.v1", {"sha256": "a" * 64}).to_dict()
    with pytest.raises(ConfFlowClientError, match="serialized handle belongs"):
        client.restore_handle({**saved, "server_id": "other"})
    monkeypatch.setattr(client, "attach", lambda run_id: SimpleNamespace(to_dict=lambda: {**saved, "run_id": "other"}))
    with pytest.raises(ConfFlowClientError, match="identity no longer matches"):
        client.restore_handle(saved)

    unresolved = {
        "dispatch_state": "dispatching",
        "reconcile_attempts": 3,
        "backend": "control",
    }
    monkeypatch.setattr(client, "_reconcile_control_dispatch", lambda record, state: unresolved)
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(client_module, "load_state", lambda service, run_id: unresolved)
    monkeypatch.setattr(client_module, "save_state", lambda service, run_id, state: captured.append(state))
    with pytest.raises(ConfFlowClientError, match="1..2048"):
        client.confirm_unresolved_dispatch_not_accepted("run-1", evidence=" ")
    with pytest.raises(ConfFlowClientError, match="1..2048"):
        client.confirm_unresolved_dispatch_not_accepted("run-1", evidence="x" * 2049)
    client.confirm_unresolved_dispatch_not_accepted("run-1", evidence="operator proof")
    persisted = captured[-1]
    assert persisted["dispatch_state"] == "failed"
    assert persisted["dispatch_outcome"] == "rejected"
    assert persisted["reconcile_attempts"] == 3
    assert persisted["recovery_state"] == "retry_authorized"
    assert isinstance(persisted["dispatch_updated_at"], str)
    resolution = persisted["dispatch_resolution"]
    assert resolution == {
        "kind": "scheduler_non_acceptance",
        "evidence": "operator proof",
        "recorded_at": resolution["recorded_at"],
    }
    assert isinstance(resolution["recorded_at"], str)

    coordinator.service.record.workflow_kind = SimpleNamespace(value="dag")
    client._selected_backend = None
    monkeypatch.setattr(
        client, "probe", lambda require_dag=False: (_ for _ in ()).throw(ConfFlowClientError("no probe"))
    )
    monkeypatch.setattr(client_module, "load_state", lambda service, run_id: None)
    result_handle, result = client.submit_with_outcome(SubmitRequest("run-1"))
    assert result_handle is None and result.errors == [
        "control backend admission failed [control_backend_admission_unavailable]"
    ]
    client._selected_backend = "control"
    monkeypatch.setattr(client_module, "load_state", lambda service, run_id: {"backend": "legacy"})
    _handle, retired = client.submit_with_outcome(SubmitRequest("run-1"))
    assert retired.errors == ["legacy ConfFlow backend is retired"]
    monkeypatch.setattr(client, "submit_with_outcome", lambda request: (None, SimpleNamespace(errors=["bad submit"])))
    with pytest.raises(ConfFlowClientError, match="bad submit"):
        client.submit(SubmitRequest("run-1"))
    monkeypatch.setattr(client, "submit_with_outcome", lambda request: (None, SimpleNamespace(errors=[])))
    with pytest.raises(ConfFlowClientError, match="no handle"):
        client.submit(SubmitRequest("run-1"))

    class CancelHandle:
        run_id = "run-1"

        def cancel(self):
            return None

    outcome = client.cancel_outcome(CancelHandle())
    assert outcome.changed_count == 1

    class FailingCancel(CancelHandle):
        def cancel(self):
            raise RuntimeError("cancel failed")

    assert client.cancel_outcome(FailingCancel()).errors == ["cancel failed"]


def test_client_scheduler_identity_and_digest_helpers(monkeypatch) -> None:
    coordinator = _ClientCoordinator()
    client = SSHConfFlowClient(coordinator, "server")
    record = coordinator.service.record
    scheduler_type, resources, scripts = client._launcher_scheduler_details(record, {"cpus": 3})
    assert scheduler_type == "slurm" and resources.cpus == 3 and scripts == []
    assert coordinator.service.updated_runs
    configured = SimpleNamespace(confflow_executable="/configured/confflow")
    coordinator.server = configured
    assert client._launcher_executable(record, {}, []) == "/configured/confflow"
    coordinator.server = SimpleNamespace(confflow_executable="")
    assert (
        client._launcher_executable(record, {"capability": {"executable": {"path": "/cap/confflow"}}}, [])
        == "/cap/confflow"
    )
    assert (
        client._launcher_executable(record, {}, [SimpleNamespace(confflow_executable="/task/confflow")])
        == "/task/confflow"
    )
    assert client._launcher_executable(record, {}, []) == "confflow"

    class SSH:
        def run(self, command: str, timeout: int):
            if "printf" not in command:
                return SimpleNamespace(exit_code=0, stdout="A" * 64 + "\n", stderr="")
            return SimpleNamespace(
                exit_code=0,
                stdout="/opt/confflow\n1|2|3|4\n" + "B" * 64 + "\n",
                stderr="",
            )

    @contextmanager
    def session(server_id: str, *, need_sftp: bool):
        del server_id, need_sftp
        yield SSH(), None

    coordinator.server = SimpleNamespace(env_init_scripts=[])
    coordinator.session = session
    capability = ConfFlowCapabilities(4, "2.0.0", True, True, True, executable={"python": "/usr/bin/python"})
    assert client._measure_control_identity(capability)["sha256"] == ("b" * 64)
    assert client._remote_digest("run-1", "/state", "/remote/workflow.yaml") == "a" * 64
    with pytest.raises(ConfFlowClientError, match="no Python"):
        client._measure_control_identity(ConfFlowCapabilities(4, "2.0.0", True, True, True, executable={}))

    class BadSSH:
        def run(self, command: str, timeout: int):
            return SimpleNamespace(exit_code=1, stdout="", stderr="permission denied")

    @contextmanager
    def bad_session(server_id: str, *, need_sftp: bool):
        del server_id, need_sftp
        yield BadSSH(), None

    coordinator.session = bad_session
    with pytest.raises(ConfFlowClientError, match="identity probe failed"):
        client._measure_control_identity(capability)
    with pytest.raises(ConfFlowClientError, match="digest failed"):
        client._remote_digest("run-1", "/state", "/remote/workflow.yaml")
    with pytest.raises(ConfFlowClientError, match="path must be absolute"):
        client._remote_digest("run-1", "/state", "relative")

    raw = {
        "schema_version": 4,
        "version": "2.0.0",
        "capabilities": {"workflow_state": True, "resume": True, "dag": True, "control_worker": True},
    }
    assert _capability_from_state({"capability": raw}).control_worker is True
    assert _capability_from_state({"capability": {"schema_version": "bad"}}) is None
    assert _capability_from_state({}) is None
