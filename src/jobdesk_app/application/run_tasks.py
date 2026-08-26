"""Application-facing lookup port for persisted run tasks.

The Files page only needs to read the task snapshot belonging to the active
batch.  Keep that dependency narrower than ``RunService`` so the Qt page does
not construct or import the service itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class RunTaskLookup(Protocol):
    """Read-only application port required by the Files page."""

    def load_tasks(self, workspace: Path, run_id: str) -> list[Any]: ...


class RunServiceTaskLookup:
    """Compatibility adapter from the narrow port to the existing service.

    The import remains lazy so importing application ports does not eagerly
    construct the persistence/service graph.  New GUI code should depend on
    :class:`RunTaskLookup`; this adapter is the composition-root default that
    preserves direct ``FileTransferPage(...)`` construction.
    """

    def load_tasks(self, workspace: Path, run_id: str) -> list[Any]:
        from ..services.run_service import RunService

        return RunService(Path(workspace)).load_tasks(run_id)


__all__ = ["RunServiceTaskLookup", "RunTaskLookup"]
