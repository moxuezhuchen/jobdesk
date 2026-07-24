"""Regression tests for the GUI upload-time ConfFlow gate."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jobdesk_app.core.run import RunMode, RunSource, RunSpec, WorkflowKind
from jobdesk_app.gui import main_window
from jobdesk_app.remote.confflow_probe import ConfFlowCapabilityPreflightError
from jobdesk_app.services.submit_use_case import PreparedBatch


def test_capability_failure_before_upload_does_not_upload_or_create_run(monkeypatch, tmp_path: Path):
    ssh = Mock()
    server = SimpleNamespace(env_init_scripts=[])
    monkeypatch.setattr(main_window, "load_servers", lambda: SimpleNamespace(servers={"srv": server}))
    monkeypatch.setattr(main_window, "create_ssh_client", lambda _server: ssh)

    def fail_probe(*_args, **_kwargs):
        raise ConfFlowCapabilityPreflightError("ConfFlow capability preflight failed: incompatible")

    monkeypatch.setattr(main_window, "probe_confflow_capabilities", fail_probe)

    source = RunSource(path="/remote/a.xyz")
    spec = RunSpec(
        server_id="srv",
        remote_dir="/remote",
        command_template="confflow workflow.yaml",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[source],
        workflow_kind=WorkflowKind.confflow,
    )
    batch = PreparedBatch(
        local_paths=[tmp_path / "a.xyz"],
        upload_targets=["/remote/a.xyz"],
        specs=[spec],
    )
    service = Mock()
    payload = SimpleNamespace(server_id="srv")

    with pytest.raises(ConfFlowCapabilityPreflightError):
        main_window._upload_prepared_batch(batch, payload, service)

    service.upload_path.assert_not_called()
    ssh.run.assert_not_called()
    ssh.connect.assert_called_once()
    ssh.close.assert_called_once()
