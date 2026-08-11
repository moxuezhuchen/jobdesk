import hashlib
import json
import stat
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobdesk_app.application.confflow_client import ConfFlowClientError
from jobdesk_app.services.confflow_control_handoff import (
    WorkerHandoffResult,
    _assert_path_under,
    _canonical_json,
    _ensure_worker_remote_directories,
    _stage_remote_file,
    _validate_safe_component,
    _worker_handoff,
    _worker_handoff_digest,
    build_worker_handoff_result,
)


def test_worker_handoff_result_preserves_golden_canonical_envelope() -> None:
    result = build_worker_handoff_result(
        run_id="run-1",
        task_id="methane",
        attempt_root="/attempt",
        handoff_path="/attempt/input/worker-handoff.json",
        workflow_path="/attempt/input/workflow.yaml",
        workflow_digest="b" * 64,
        input_path="/attempt/input/methane.xyz",
        input_digest="c" * 64,
        work_dir="/attempt/results/methane_confflow_work",
    )

    expected = (
        b'{"content_schema":"confflow.control.worker-handoff.v1","run_id":"run-1","tasks":[{"input_xyz":"/attempt/input/methane.xyz","sha256":"'
        + b"c" * 64
        + b'","task_id":"methane","work_dir":"/attempt/results/methane_confflow_work"}],"workflow_config":{"path":"/attempt/input/workflow.yaml","sha256":"'
        + b"b" * 64
        + b'"}}'
    )
    assert isinstance(result, WorkerHandoffResult)
    assert result.envelope_bytes == expected
    assert result.envelope_digest == hashlib.sha256(expected).hexdigest()
    assert result.envelope == json.loads(expected)
    assert result.handoff_path == "/attempt/input/worker-handoff.json"
    with pytest.raises(FrozenInstanceError):
        result.run_id = "changed"  # type: ignore[misc]


def test_worker_handoff_digest_is_lowercased_and_rejects_invalid_shapes() -> None:
    handoff = _worker_handoff(
        run_id="run-1",
        workflow_path="/attempt/input/workflow.yaml",
        workflow_digest="A" * 64,
        input_path="/attempt/input/methane.xyz",
        input_digest="b" * 64,
        work_dir="/attempt/results/methane_confflow_work",
        task_id="methane",
    )
    assert _worker_handoff_digest(handoff, "workflow_config") == "a" * 64
    assert _worker_handoff_digest(handoff, "tasks") == "b" * 64

    with pytest.raises(ConfFlowClientError, match="exactly one task"):
        _worker_handoff_digest({"tasks": []}, "tasks")
    with pytest.raises(ConfFlowClientError, match="invalid digest"):
        _worker_handoff_digest({"workflow_config": {"sha256": "not-a-digest"}}, "workflow_config")


@pytest.mark.parametrize(
    ("root", "candidate"),
    [
        ("/attempt", "/attempt/input/workflow.yaml"),
        ("/attempt", "/attempt/results/methane_confflow_work"),
    ],
)
def test_path_under_accepts_only_safe_descendants(root: str, candidate: str) -> None:
    _assert_path_under(root, candidate, "worker path")


@pytest.mark.parametrize(
    ("root", "candidate"),
    [
        ("/attempt", "/attempt"),
        ("/attempt", "/attempt/../outside"),
        ("/attempt", "/attempt\\input\\file"),
        ("relative", "/relative/file"),
    ],
)
def test_path_under_rejects_root_escape_or_malformed_paths(root: str, candidate: str) -> None:
    with pytest.raises(ConfFlowClientError, match="worker path"):
        _assert_path_under(root, candidate, "worker path")


@pytest.mark.parametrize("value", ["", ".hidden", "../escape", "name/child", "name\\child"])
def test_safe_component_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ConfFlowClientError, match="unsafe path component"):
        _validate_safe_component(value, "worker")


class _RemoteSFTP:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.directories: list[str] = []

    def mkdir_p(self, remote_dir: str) -> None:
        self.directories.append(remote_dir)

    def lstat(self, remote_path: str):
        return self.source.lstat() if remote_path == "/source/input.xyz" else None

    def download_file(self, remote_path: str, local_path: Path, **kwargs):
        del remote_path, kwargs
        local_path.write_bytes(self.source.read_bytes())
        return SimpleNamespace(status="transferred", reason="ok")


def test_remote_staging_requires_regular_file_and_creates_private_directories(tmp_path: Path) -> None:
    source = tmp_path / "input.xyz"
    source.write_bytes(b"1\n\nH 0 0 0\n")
    sftp = _RemoteSFTP(source)
    _stage_remote_file(
        sftp,
        "/source/input.xyz",
        tmp_path / "staged.xyz",
        "/attempt/input/input.xyz",
        hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    _ensure_worker_remote_directories(
        sftp,
        None,
        "/attempt",
        "/attempt/input",
        "/attempt/input",
        "/attempt/input",
        "/attempt/results",
    )
    assert (tmp_path / "staged.xyz").read_bytes() == source.read_bytes()
    assert sftp.directories == ["/attempt", "/attempt/input", "/attempt/results"]


def test_remote_staging_rejects_digest_mismatch_and_non_regular_source(tmp_path: Path) -> None:
    source = tmp_path / "input.xyz"
    source.write_bytes(b"source")
    sftp = _RemoteSFTP(source)
    with pytest.raises(ConfFlowClientError, match="digest mismatch"):
        _stage_remote_file(sftp, "/source/input.xyz", tmp_path / "staged.xyz", "/attempt/input/input.xyz", "0" * 64)

    class _DirectorySFTP(_RemoteSFTP):
        def lstat(self, remote_path: str):
            del remote_path
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700)

    with pytest.raises(ConfFlowClientError, match="not a regular file"):
        _stage_remote_file(
            _DirectorySFTP(source),
            "/source/input.xyz",
            tmp_path / "staged.xyz",
            "/attempt/input/input.xyz",
            hashlib.sha256(source.read_bytes()).hexdigest(),
        )


def test_canonical_json_matches_utf8_compact_sorted_contract() -> None:
    assert _canonical_json({"z": "中文", "a": 1}) == '{"a":1,"z":"中文"}'.encode("utf-8")
