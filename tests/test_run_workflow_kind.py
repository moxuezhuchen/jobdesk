"""Tests for ``RunSpec.workflow_kind`` plumbing."""

from __future__ import annotations

from jobdesk_app.core.run import (
    RunMode,
    RunSpec,
    WorkflowKind,
)


def test_workflow_kind_default_is_gaussian():
    """Backwards compatibility: rows without workflow_kind default to gaussian."""
    spec = RunSpec(
        server_id="x",
        remote_dir="/tmp",
        command_template="g16 < {input} > {output}",
        max_parallel=1,
        mode=RunMode.selected_files,
    )
    assert spec.workflow_kind is WorkflowKind.gaussian


def test_workflow_kind_can_be_set_to_confflow():
    spec = RunSpec(
        server_id="x",
        remote_dir="/tmp",
        command_template="confflow mol.xyz -c wf.yaml --resume",
        max_parallel=2,
        mode=RunMode.selected_files,
        workflow_kind=WorkflowKind.confflow,
    )
    assert spec.workflow_kind is WorkflowKind.confflow


def test_workflow_kind_distinct_values():
    assert {w.value for w in WorkflowKind} == {"gaussian", "orca", "confflow", "dag"}
