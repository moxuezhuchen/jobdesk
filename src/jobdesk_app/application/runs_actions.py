"""Application boundary for Runs-page action intent and outcomes.

The page still owns confirmation dialogs and rendering.  This module keeps
the stateful guards and worker payloads independent from Qt widgets: action
requests are immutable, and worker completion is represented by an immutable
outcome that can safely cross the worker/UI boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class RunActionIntent:
    """A validated request for one Runs-page action."""

    action: str
    run_ids: tuple[str, ...] = ()
    workspace: Path | None = None

    def __post_init__(self) -> None:
        normalized = tuple(dict.fromkeys(str(run_id) for run_id in self.run_ids if str(run_id)))
        object.__setattr__(self, "action", str(self.action).strip() or "mutation")
        object.__setattr__(self, "run_ids", normalized)
        if self.workspace is not None:
            object.__setattr__(self, "workspace", Path(self.workspace))


@dataclass(frozen=True, slots=True)
class RunActionOutcome:
    """Immutable result of executing a RunActionIntent."""

    intent: RunActionIntent
    changed_count: int = 0
    errors: tuple[str, ...] = ()
    completed_run_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "changed_count", int(self.changed_count))
        object.__setattr__(self, "errors", tuple(str(error) for error in self.errors if str(error)))
        object.__setattr__(
            self,
            "completed_run_ids",
            tuple(dict.fromkeys(str(run_id) for run_id in self.completed_run_ids if str(run_id))),
        )

    @property
    def succeeded(self) -> bool:
        return not self.errors

    @property
    def retired_watch_run_ids(self) -> frozenset[str]:
        """Runs whose successful delete permits watcher retirement."""
        if self.intent.action != "delete":
            return frozenset()
        return frozenset(self.completed_run_ids)


class RunsActionController:
    """Guard action concurrency and produce immutable action payloads."""

    def __init__(self) -> None:
        self._active_intent: RunActionIntent | None = None

    @property
    def active_intent(self) -> RunActionIntent | None:
        return self._active_intent

    def begin(
        self,
        action: str,
        run_ids: Iterable[str] = (),
        *,
        workspace: Path | None = None,
        shutting_down: bool = False,
    ) -> RunActionIntent | None:
        """Return an intent unless shutdown, no-selection, or busy blocks it."""
        intent = RunActionIntent(action=action, run_ids=tuple(run_ids), workspace=workspace)
        if shutting_down or self._active_intent is not None:
            return None
        if intent.action in {"cancel", "delete", "refresh_status", "retry"} and not intent.run_ids:
            return None
        self._active_intent = intent
        return intent

    def finish(self, intent: RunActionIntent | None = None) -> None:
        """Release the current action guard, tolerating late cleanup calls."""
        if intent is None or intent == self._active_intent:
            self._active_intent = None

    def outcome(
        self,
        intent: RunActionIntent,
        *,
        changed_count: int = 0,
        errors: Iterable[str] = (),
        completed_run_ids: Iterable[str] = (),
    ) -> RunActionOutcome:
        """Build an immutable worker outcome without mutating UI state."""
        return RunActionOutcome(
            intent=intent,
            changed_count=changed_count,
            errors=tuple(errors),
            completed_run_ids=tuple(completed_run_ids),
        )


__all__ = ["RunActionIntent", "RunActionOutcome", "RunsActionController"]
