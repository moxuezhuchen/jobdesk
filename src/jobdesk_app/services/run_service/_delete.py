"""Delete operations for run_service."""
from __future__ import annotations

from pathlib import Path

from jobdesk_app.services.run_repository import (
    _DELETE_CLEANUP_LEADER_GRACE_SECONDS,
    OperationRecord,
    _lexical_absolute,
    _reject_reparse_chain,
)


def delete_run(service, run_id: str) -> None:
    """Journal and execute a replayable deletion.

    This is a module-level function to enable method extraction from RunService.
    The ``service`` argument must be a RunService instance.
    """
    run_dir = service._run_dir(run_id)
    results_dir = _lexical_absolute(service.workspace_dir / "results" / run_id)
    if not results_dir.is_relative_to(
        _lexical_absolute(service.workspace_dir / "results")
    ):
        raise ValueError(f"run_id escapes results dir: {run_id}")
    operation = service.repository.prepare_delete_run(
        run_id,
        run_dir=run_dir,
        results_root=service.workspace_dir / "results",
        results_dir=results_dir,
    )
    _recover_delete_operation(service, operation, raise_errors=True)


def recover_delete_operations(service) -> int:
    """Resume incomplete deletions; return operations completed by this call.

    This is a module-level function to enable method extraction from RunService.
    The ``service`` argument must be a RunService instance.
    """
    completed = 0
    for operation in service.repository.list_operations(incomplete_only=True):
        if operation.kind != "delete":
            continue
        if _recover_delete_operation(service, operation):
            completed += 1
    return completed


def recover_delete_operations_globally(service) -> tuple[int, list[str]]:
    """Recover deletion journals for every trusted recorded workspace.

    This is a module-level function to enable method extraction from RunService.
    The ``service`` argument must be a RunService instance.
    """
    from jobdesk_app.services.run_service import RunService

    workspaces: set[Path] = set()
    errors: list[str] = []
    trusted_workspaces = {
        _lexical_absolute(path)
        for path in service.repository.list_workspace_roots()
    }
    for operation in service.repository.list_operations(incomplete_only=True):
        if operation.kind != "delete":
            continue
        try:
            bound_workspace = service.repository.delete_operation_workspace(
                operation.operation_id
            )
            if bound_workspace is None:
                raise ValueError("delete operation has no trusted workspace binding")
            workspace = _lexical_absolute(bound_workspace)
            if workspace not in trusted_workspaces:
                raise ValueError(
                    f"workspace binding is not a trusted workspace: {workspace}"
                )
            raw_root = operation.payload.get("results_root")
            if not isinstance(raw_root, str) or not raw_root:
                raise ValueError("missing results_root")
            recorded_root_path = Path(raw_root)
            if not recorded_root_path.is_absolute():
                raise ValueError("results_root must be absolute")
            results_root = _lexical_absolute(recorded_root_path)
            run_snapshot = operation.payload.get("run")
            if not isinstance(run_snapshot, dict):
                raise ValueError("delete payload has no run snapshot")
            raw_local_dir = run_snapshot.get("local_dir")
            if not isinstance(raw_local_dir, str) or not raw_local_dir:
                raise ValueError("run.local_dir must be a nonempty absolute path")
            local_dir_path = Path(raw_local_dir)
            if not local_dir_path.is_absolute():
                raise ValueError("run.local_dir must be a nonempty absolute path")
            payload_workspace = _lexical_absolute(local_dir_path)
            if payload_workspace != workspace:
                raise ValueError(
                    "run.local_dir does not match delete operation workspace binding"
                )
            if results_root != _lexical_absolute(workspace / "results"):
                raise ValueError(
                    "results_root does not match run.local_dir/results"
                )
            _reject_reparse_chain(workspace, results_root)
            workspaces.add(workspace)
        except Exception as exc:
            errors.append(
                f"delete recovery rejected {operation.operation_id}: "
                f"{type(exc).__name__}: {exc}"
            )

    completed = 0
    for workspace in sorted(workspaces, key=str):
        try:
            completed += RunService(
                workspace,
                runs_dir=service.runs_dir,
            ).recover_delete_operations()
        except Exception as exc:
            errors.append(
                f"delete recovery failed for {workspace}: "
                f"{type(exc).__name__}: {exc}"
            )
    return completed, errors


def _recover_delete_operation(
    service, operation: OperationRecord, *, raise_errors: bool = False
) -> bool:
    """Execute or resume a single delete operation.

    This is a module-level function to enable method extraction from RunService.
    The ``service`` argument must be a RunService instance.
    """
    import shutil

    phase = operation.phase
    isolation_done_by_us = False
    try:
        _authorized_delete_workspace(service, operation)
        if phase == "files_deleted":
            return service.repository.advance_operation(
                operation.operation_id, "files_deleted", "completed", complete=True
            )
        operation = service.repository.ensure_delete_trash_paths(
            operation.operation_id
        )
        phase = operation.phase
        run_dir, results_dir, trash_run_dir, trash_results_dir = (
            _validated_delete_paths(service, operation)
        )
        if phase == "prepared":
            if not service.repository.delete_run_metadata(operation.operation_id):
                return False
            phase = "metadata_deleted"
        if phase == "metadata_deleted":
            trash_run_dir.parent.mkdir(parents=True, exist_ok=True)
            trash_results_dir.parent.mkdir(parents=True, exist_ok=True)

            def isolate_files(stored: OperationRecord) -> None:
                paths = _validated_delete_paths(service, stored)
                for source, trash in ((paths[0], paths[2]), (paths[1], paths[3])):
                    if trash.exists():
                        if source.exists():
                            raise OSError(
                                f"Both managed and trash paths exist for {stored.run_id}"
                            )
                        continue
                    if not source.exists():
                        continue
                    source.replace(trash)

            if not service.repository.execute_delete_isolation(
                operation.operation_id, isolate_files
            ):
                return False
            isolation_done_by_us = True
            phase = "files_isolated"
        if phase == "files_isolated":
            if not isolation_done_by_us:
                if not _wait_for_files_isolated_leader(
                    service, operation.operation_id,
                    grace_seconds=_DELETE_CLEANUP_LEADER_GRACE_SECONDS,
                ):
                    return False
            for trash, label in (
                (trash_results_dir, "results"),
                (trash_run_dir, "run directory"),
            ):
                if trash.exists():
                    try:
                        shutil.rmtree(trash)
                    except OSError as exc:
                        raise OSError(
                            f"Failed to delete {label} for run {operation.run_id}: {exc}"
                        ) from exc
            if not service.repository.advance_operation(
                operation.operation_id, "files_isolated", "files_deleted"
            ):
                return False
            return service.repository.advance_operation(
                operation.operation_id, "files_deleted", "completed", complete=True
            )
        return False
    except Exception as exc:
        service.repository.advance_operation(
            operation.operation_id,
            phase,
            phase,
            last_error=str(exc),
        )
        if raise_errors:
            raise
        return False


def _wait_for_files_isolated_leader(
    service,
    operation_id: str,
    *,
    grace_seconds: float,
) -> bool:
    """Wait for the leader to finish files_isolated phase."""
    import time as _time

    deadline = _time.monotonic() + grace_seconds
    poll_interval = 0.01
    while True:
        row = next(
            (
                op for op in service.repository.list_operations()
                if op.operation_id == operation_id
            ),
            None,
        )
        if row is None:
            return False
        if row.completed_at is not None or row.phase != "files_isolated":
            return False
        if _time.monotonic() >= deadline:
            return True
        _time.sleep(poll_interval)


def _authorized_delete_workspace(service, operation: OperationRecord) -> Path:
    """Validate independent delete authorization before filesystem mutation."""
    workspace = _lexical_absolute(service.workspace_dir)
    trusted = {
        _lexical_absolute(path) for path in service.repository.list_workspace_roots()
    }
    bound = service.repository.delete_operation_workspace(operation.operation_id)
    if bound is None or _lexical_absolute(bound) != workspace:
        raise ValueError("delete operation workspace binding mismatch")
    if workspace not in trusted:
        raise ValueError("delete operation workspace is not trusted")
    raw_root = operation.payload.get("results_root")
    if not isinstance(raw_root, str) or not Path(raw_root).is_absolute():
        raise ValueError("delete operation results_root must be absolute")
    if _lexical_absolute(Path(raw_root)) != _lexical_absolute(workspace / "results"):
        raise ValueError("delete operation results_root mismatches workspace binding")
    snapshot = operation.payload.get("run")
    if not isinstance(snapshot, dict):
        raise ValueError("delete operation has no run snapshot")
    raw_local_dir = snapshot.get("local_dir")
    if (
        not isinstance(raw_local_dir, str)
        or not Path(raw_local_dir).is_absolute()
        or _lexical_absolute(Path(raw_local_dir)) != workspace
    ):
        raise ValueError("delete operation run.local_dir mismatches workspace binding")
    return workspace


def _validated_delete_paths(
    service, operation: OperationRecord
) -> tuple[Path, Path, Path, Path]:
    """Validate and return all delete operation paths."""
    runs_root = _lexical_absolute(service.runs_dir)
    run_dir = _lexical_absolute(
        Path(str(operation.payload.get("run_dir", "")))
    )
    results_dir = _lexical_absolute(
        Path(str(operation.payload.get("results_dir", "")))
    )
    expected_run_dir = service._run_dir(operation.run_id)
    expected_results_dir = _lexical_absolute(
        service.workspace_dir / "results" / operation.run_id
    )
    if run_dir != expected_run_dir:
        raise ValueError(f"unsafe delete run path: {run_dir}")
    _reject_reparse_chain(runs_root, run_dir)
    results_root = _lexical_absolute(service.workspace_dir / "results")
    if results_dir != expected_results_dir:
        raise ValueError(f"unsafe delete results path: {results_dir}")
    _reject_reparse_chain(results_root, results_dir)
    run_trash_root = (
        service.runs_dir / ".jobdesk-trash" / operation.operation_id
    )
    results_trash_root = (
        results_root / ".jobdesk-trash" / operation.operation_id
    )
    run_trash_root = _lexical_absolute(run_trash_root)
    results_trash_root = _lexical_absolute(results_trash_root)
    trash_run_dir = _lexical_absolute(
        Path(str(operation.payload.get("trash_run_dir", "")))
    )
    trash_results_dir = _lexical_absolute(
        Path(str(operation.payload.get("trash_results_dir", "")))
    )
    if (
        trash_run_dir != run_trash_root / "run"
        or trash_results_dir != results_trash_root / "results"
        or not trash_run_dir.is_relative_to(runs_root)
        or not trash_results_dir.is_relative_to(results_root)
    ):
        raise ValueError("unsafe delete trash path")
    _reject_reparse_chain(runs_root, trash_run_dir)
    _reject_reparse_chain(results_root, trash_results_dir)
    return run_dir, results_dir, trash_run_dir, trash_results_dir
