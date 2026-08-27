"""Application boundary for Runs-page queries and selection state.

The Qt page owns presentation only.  ``RunQueryController`` keeps the
``RunService`` boundary explicit and converts each returned record into a
small immutable projection used by filtering and row selection.  The raw
records are retained in a tuple solely for existing action/use-case
compatibility; they never cross into the query predicates as mutable UI
state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Protocol


class RunQueryService(Protocol):
    """The read-only RunService surface required by the Runs page."""

    def list_runs(self) -> list[Any]: ...


class RunServiceFactory(Protocol):
    def __call__(self, workspace: Path) -> RunQueryService: ...


@dataclass(frozen=True, slots=True)
class RunQuerySnapshot:
    """Immutable fields needed to display, filter, and select one run."""

    run_id: str
    server_id: str
    remote_dir: str
    command_template: str
    created_at: str
    status_summary: Mapping[str, int] = field(default_factory=dict)
    workflow_kind: Any = None

    def __post_init__(self) -> None:
        # A frozen dataclass does not freeze a nested dict.  Copy it into a
        # read-only mapping so a service-owned record cannot mutate the page's
        # query state after the query has completed.
        object.__setattr__(
            self,
            "status_summary",
            MappingProxyType({str(key): int(value) for key, value in self.status_summary.items()}),
        )

    @classmethod
    def from_record(cls, record: Any) -> "RunQuerySnapshot":
        return cls(
            run_id=str(getattr(record, "run_id", "")),
            server_id=str(getattr(record, "server_id", "")),
            remote_dir=str(getattr(record, "remote_dir", "")),
            command_template=str(getattr(record, "command_template", "")),
            created_at=str(getattr(record, "created_at", "")),
            status_summary=dict(getattr(record, "status_summary", {}) or {}),
            workflow_kind=getattr(record, "workflow_kind", None),
        )


@dataclass(frozen=True, slots=True)
class RunFilterSpec:
    """Immutable input for filtering the Runs query projection.

    The GUI may keep the widgets and their translated labels, but the
    application predicate only receives this small, normalized value object.
    ``server_id`` and ``workflow_kind`` intentionally retain their case: the
    existing combo-box semantics compare those values exactly.
    """

    search: str = ""
    status: str = "all"
    server_id: str = "all"
    workflow_kind: str = "all"
    date_range: str = "all"

    def __post_init__(self) -> None:
        object.__setattr__(self, "search", str(self.search or "").strip().casefold())
        object.__setattr__(self, "status", _filter_value(self.status).casefold())
        object.__setattr__(self, "server_id", _filter_value(self.server_id))
        object.__setattr__(self, "workflow_kind", _filter_value(self.workflow_kind))
        object.__setattr__(self, "date_range", _filter_value(self.date_range).casefold())


_ACTIVE_FILTER_STATUSES = frozenset({"local_ready", "uploaded", "submitting", "submitted", "running"})
_COMPLETED_FILTER_STATUSES = frozenset({"remote_completed", "downloaded", "analyzed"})


def _filter_value(value: object) -> str:
    normalized = str(value).strip() if value is not None else ""
    return normalized or "all"


def workflow_filter_value(workflow_kind: Any) -> str:
    """Return the stable value used by the workflow filter combo box."""
    if workflow_kind is None:
        return "Unknown"
    try:
        from ..core.run import WorkflowKind

        return WorkflowKind(getattr(workflow_kind, "value", workflow_kind)).value
    except (TypeError, ValueError):
        return "Unknown"


def _status_filter_matches(status_summary: Mapping[str, int], value: str) -> bool:
    positive = {key for key, count in status_summary.items() if count > 0}
    groups = {
        "all": positive,
        "active": _ACTIVE_FILTER_STATUSES,
        "completed": _COMPLETED_FILTER_STATUSES,
        "failed": frozenset({"failed"}),
        "uncertain": frozenset({"uncertain"}),
        "cancelled": frozenset({"cancelled"}),
    }
    return value == "all" or bool(positive & groups.get(value, frozenset()))


def _created_at_date(value: str) -> date | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        # Preserve the page's historical semantics: an offset is parsed but
        # discarded before comparing calendar dates in the local UI.
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).replace(tzinfo=None).date()
    except ValueError:
        return None


def matches_run_filter(
    snapshot: RunQuerySnapshot,
    spec: RunFilterSpec,
    *,
    today: date | None = None,
) -> bool:
    """Apply the Runs-page search/status/server/workflow/date predicate."""
    searchable = "\n".join(
        (
            snapshot.run_id,
            snapshot.server_id,
            snapshot.remote_dir,
            workflow_filter_value(snapshot.workflow_kind),
            snapshot.command_template,
        )
    ).casefold()
    if spec.search and spec.search not in searchable:
        return False
    if not _status_filter_matches(snapshot.status_summary, spec.status):
        return False
    if spec.server_id != "all" and snapshot.server_id != spec.server_id:
        return False
    if spec.workflow_kind != "all" and workflow_filter_value(snapshot.workflow_kind) != spec.workflow_kind:
        return False
    if spec.date_range == "all":
        return True

    created = _created_at_date(snapshot.created_at)
    if created is None:
        return False
    current_day = today or datetime.now().date()
    if spec.date_range == "today":
        return created == current_day
    days = 7 if spec.date_range == "7d" else 30
    return current_day - timedelta(days=days - 1) <= created <= current_day


def filter_run_snapshots(
    snapshots: Iterable[RunQuerySnapshot],
    spec: RunFilterSpec,
    *,
    today: date | None = None,
) -> tuple[RunQuerySnapshot, ...]:
    """Return matching immutable snapshots without exposing UI controls."""
    return tuple(snapshot for snapshot in snapshots if matches_run_filter(snapshot, spec, today=today))


@dataclass(frozen=True, slots=True)
class RunQueryResult:
    """One query result with immutable projections and legacy action records."""

    snapshots: tuple[RunQuerySnapshot, ...]
    records: tuple[Any, ...]

    def record_for(self, run_id: str) -> Any | None:
        for record in self.records:
            if str(getattr(record, "run_id", "")) == run_id:
                return record
        return None


class RunQueryController:
    """Read runs through an injected RunService factory, never a repository."""

    def __init__(self, service_factory: Callable[[Path], RunQueryService]) -> None:
        self._service_factory = service_factory

    def list_runs(self, workspace: Path) -> RunQueryResult:
        records = tuple(self._service_factory(Path(workspace)).list_runs())
        return RunQueryResult(
            snapshots=tuple(RunQuerySnapshot.from_record(record) for record in records),
            records=records,
        )


@dataclass(frozen=True, slots=True)
class RunSelectionSnapshot:
    """Immutable, serializable view of the Runs-page selection state."""

    selected_ids: frozenset[str]
    current_id: str | None
    applied_batch_id: str | None


class RunSelectionState:
    """Keep multi-selection and one-shot batch auto-selection deterministic."""

    def __init__(self) -> None:
        self._selected_ids: set[str] = set()
        self._current_id: str | None = None
        self._applied_batch_id: str | None = None

    def remember(self, selected_ids: Iterable[str], current_id: str | None) -> None:
        self._selected_ids.update(str(run_id) for run_id in selected_ids)
        self._current_id = str(current_id) if current_id is not None else None

    def reconcile(self, available_ids: Iterable[str], batch_id: str | None) -> str | None:
        available = {str(run_id) for run_id in available_ids}
        self._selected_ids.intersection_update(available)
        normalized_batch = str(batch_id) if batch_id is not None else None
        if (
            normalized_batch is not None
            and normalized_batch != self._applied_batch_id
            and normalized_batch in available
        ):
            self._applied_batch_id = normalized_batch
            self._current_id = normalized_batch
        elif self._current_id not in available:
            self._current_id = normalized_batch if normalized_batch in available else None
        return self._current_id

    def snapshot(self) -> RunSelectionSnapshot:
        return RunSelectionSnapshot(
            selected_ids=frozenset(self._selected_ids),
            current_id=self._current_id,
            applied_batch_id=self._applied_batch_id,
        )


__all__ = [
    "RunFilterSpec",
    "RunQueryController",
    "RunQueryResult",
    "RunQueryService",
    "RunQuerySnapshot",
    "RunSelectionSnapshot",
    "RunSelectionState",
    "filter_run_snapshots",
    "matches_run_filter",
    "workflow_filter_value",
]
