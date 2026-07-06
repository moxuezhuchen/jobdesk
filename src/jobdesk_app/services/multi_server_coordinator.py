"""Cross-server run aggregation, refresh, and cancellation.

Provides a thin coordinator layer over :class:`RunCoordinator` so the GUI can
fan out refresh/cancel/list operations across every configured server in
parallel. The coordinator reads from the local run database via
:class:`RunService` and groups records by ``server_id``; remote mutations are
delegated to per-server :class:`RunCoordinator` instances that share the
caller-supplied session pool and client factories.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config.schema import ServerConfig
from .run_coordinator import RunCoordinator, RunOperationOutcome
from .run_repository import RunRecord
from .run_service import RunService


@dataclass(frozen=True)
class RefreshResult:
    """Per-server outcome of :meth:`MultiServerCoordinator.refresh_all`."""

    server_id: str
    outcome: RunOperationOutcome = field(default_factory=RunOperationOutcome)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.outcome.errors

    @property
    def total_changed(self) -> int:
        """Sum of changed_count across all collected run records."""
        return getattr(self.outcome, "changed_count", 0)

    @property
    def errors(self) -> list[str]:
        """Merged list of error strings (top-level error + outcome errors)."""
        if self.error is not None:
            return [self.error, *self.outcome.errors]
        return list(self.outcome.errors)


@dataclass(frozen=True)
class CancelResult:
    """Per-server outcome of :meth:`MultiServerCoordinator.cancel`."""

    server_id: str
    run_id: str
    outcome: RunOperationOutcome | None = None
    changed_count: int = 0
    errors: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.errors


class MultiServerCoordinator:
    """Coordinate run-lifecycle operations across every configured server.

    The coordinator keeps a single :class:`RunService` for the active workspace
    and lazily creates one :class:`RunCoordinator` per server using the supplied
    factories. Remote operations are dispatched through a small thread pool so
    that a slow server cannot block the GUI thread.
    """

    def __init__(
        self,
        workspace: str | Path,
        server_lookup: Callable[[str], ServerConfig],
        ssh_factory: Callable[[ServerConfig], Any] | None = None,
        sftp_factory: Callable[[Any], Any] | None = None,
        session_pool: Any | None = None,
        max_workers: int = 4,
        runs_dir: str | Path | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self._server_lookup = server_lookup
        self._ssh_factory = ssh_factory
        self._sftp_factory = sftp_factory
        self._session_pool = session_pool
        self._max_workers = max(1, int(max_workers))
        self._closed = False
        self._coordinators: dict[str, RunCoordinator] = {}
        if runs_dir is not None:
            self._service = RunService(self.workspace, runs_dir=runs_dir)
        else:
            self._service = RunService(self.workspace)

    # ---- queries -------------------------------------------------------------

    def list_servers(self) -> list[str]:
        """Return the sorted list of server IDs known to the lookup."""
        try:
            cfg_servers = sorted(self._server_lookup.__self__.servers.keys())  # type: ignore[union-attr]
        except AttributeError:
            cfg_servers = []
        cached_servers = sorted(self._coordinators.keys())
        db_servers = sorted({record.server_id for record in self._service.list_runs()})
        merged = list(dict.fromkeys([*cfg_servers, *cached_servers, *db_servers]))
        return merged

    def list_all_runs(self) -> list[RunRecord]:
        """Return every persisted run record grouped by ``server_id``."""
        all_runs: list[RunRecord] = []
        for server_id in self.list_servers():
            try:
                coordinator = self._coordinator_for(server_id)
            except Exception:  # noqa: BLE001
                continue
            try:
                runs = coordinator.service.list_runs()
            except Exception:  # noqa: BLE001
                continue
            for record in runs:
                if record.server_id == server_id:
                    all_runs.append(record)
        return all_runs

    def list_runs_for(self, server_id: str) -> list[RunRecord]:
        """Return persisted run records for a single server."""
        try:
            coordinator = self._coordinator_for(server_id)
        except Exception:  # noqa: BLE001
            return []
        try:
            runs = coordinator.service.list_runs()
        except Exception:  # noqa: BLE001
            return []
        return [record for record in runs if record.server_id == server_id]

    # ---- mutations ------------------------------------------------------------

    def refresh_all(self, server_ids: list[str] | None = None) -> dict[str, RefreshResult]:
        """Trigger refresh-and-download on every selected server in parallel.

        Args:
            server_ids: servers to refresh. ``None`` means every known server.

        Returns:
            Mapping ``server_id -> RefreshResult``. Servers with no runs are
            returned with an empty :class:`RunOperationOutcome`.
        """
        if self._closed:
            raise RuntimeError("MultiServerCoordinator is closed")
        targets = self._resolve_targets(server_ids)
        if not targets:
            return {}
        results: dict[str, RefreshResult] = {}
        with ThreadPoolExecutor(max_workers=min(self._max_workers, len(targets))) as pool:
            future_map = {
                pool.submit(self._refresh_one, server_id): server_id for server_id in targets
            }
            for future in as_completed(future_map):
                server_id = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001 - surface every failure as a result
                    results[server_id] = RefreshResult(server_id, error=f"{type(exc).__name__}: {exc}")
                    continue
                if result is not None:
                    results[server_id] = result
        return results

    def cancel(self, run_id: str, server_id: str) -> CancelResult:
        """Cancel a single run on a single server via the per-server coordinator."""
        if self._closed:
            raise RuntimeError("MultiServerCoordinator is closed")
        try:
            coordinator = self._coordinator_for(server_id)
        except Exception as exc:  # noqa: BLE001
            return CancelResult(server_id, run_id, error=f"{type(exc).__name__}: {exc}")
        try:
            outcome = coordinator.cancel(run_id)
        except Exception as exc:  # noqa: BLE001
            return CancelResult(server_id, run_id, error=f"{type(exc).__name__}: {exc}")
        return CancelResult(
            server_id=server_id,
            run_id=run_id,
            outcome=outcome,
            changed_count=outcome.changed_count,
            errors=list(outcome.errors),
        )

    # ---- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Mark the coordinator closed; sessions themselves are owned by the pool."""
        self._closed = True

    # ---- internals -----------------------------------------------------------

    def _resolve_targets(self, server_ids: list[str] | None) -> list[str]:
        if server_ids is None:
            targets = self.list_servers()
        else:
            valid = set(self.list_servers())
            targets = [sid for sid in server_ids if sid in valid]
        # Always also include servers that have runs in the DB but may not be
        # currently configured; otherwise their runs go silently stale.
        db_servers = {record.server_id for record in self._service.list_runs()}
        for sid in db_servers:
            if sid not in targets:
                targets.append(sid)
        return targets

    def _coordinator_for(self, server_id: str) -> RunCoordinator:
        cached = self._coordinators.get(server_id)
        if cached is not None:
            return cached
        kwargs: dict[str, Any] = {
            "service": self._service,
            "server_lookup": self._server_lookup,
        }
        if self._ssh_factory is not None:
            kwargs["ssh_factory"] = self._ssh_factory
        if self._sftp_factory is not None:
            kwargs["sftp_factory"] = self._sftp_factory
        if self._session_pool is not None:
            kwargs["session_pool"] = self._session_pool
        coordinator = RunCoordinator(**kwargs)
        self._coordinators[server_id] = coordinator
        return coordinator

    _TERMINAL_STATUSES = frozenset({"downloaded", "analyzed", "cancelled", "failed"})

    def _is_terminal_status(self, summary: dict[str, int] | None) -> bool:
        if not summary:
            return False
        if any(k in self._TERMINAL_STATUSES for k in summary.keys()):
            return True
        non_terminal = {k: v for k, v in summary.items() if k not in self._TERMINAL_STATUSES}
        return not non_terminal

    def _refresh_one(self, server_id: str) -> RefreshResult | None:
        try:
            coordinator = self._coordinator_for(server_id)
        except Exception as exc:  # noqa: BLE001
            return RefreshResult(server_id, error=f"{type(exc).__name__}: {exc}")
        try:
            runs = coordinator.service.list_runs()
        except Exception as exc:  # noqa: BLE001
            return RefreshResult(server_id, error=f"{type(exc).__name__}: {exc}")
        runs = [r for r in runs if r.server_id == server_id]
        in_progress = [r for r in runs if not self._is_terminal_status(r.status_summary)]
        if not in_progress:
            return None
        errors: list[str] = []
        records: list[RunRecord] = []
        transfers: list[Any] = []
        failures: list[tuple[str, str]] = []
        changed_total = 0
        for record in in_progress:
            try:
                outcome = coordinator.refresh(record.run_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{record.run_id}: {type(exc).__name__}: {exc}")
                continue
            records.extend(outcome.records)
            transfers.extend(outcome.transfer_records)
            failures.extend(outcome.failures)
            errors.extend(outcome.errors)
            changed_total += getattr(outcome, "changed_count", 0)
        return RefreshResult(
            server_id=server_id,
            outcome=RunOperationOutcome(
                records=records,
                transfer_records=transfers,
                failures=failures,
                errors=errors,
                changed_count=changed_total,
            ),
        )