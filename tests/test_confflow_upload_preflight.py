"""Regression tests for the GUI upload-time ConfFlow gate."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jobdesk_app.application.confflow_client import ConfFlowClientError
from jobdesk_app.core.run import RunMode, RunSource, RunSpec, WorkflowKind
from jobdesk_app.gui import main_window
from jobdesk_app.services.submit_use_case import PreparedBatch


def test_capability_failure_before_upload_does_not_upload_or_create_run(monkeypatch, tmp_path: Path):
    client = Mock()
    client.probe.side_effect = ConfFlowClientError("ConfFlow capability preflight failed: incompatible")

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

    with pytest.raises(ConfFlowClientError):
        main_window._upload_prepared_batch(batch, payload, service, client)

    service.upload_path.assert_not_called()
    client.probe.assert_called_once_with(require_dag=False)
