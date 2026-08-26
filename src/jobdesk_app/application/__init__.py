"""Application-facing facades for JobDesk's supported use cases."""

from .confflow_client import (
    ArtifactEntry,
    ArtifactManifest,
    ConfFlowClient,
    ConfFlowClientError,
    EventPage,
    RemoteRunHandle,
    RemoteRunReference,
    RemoteRunSnapshot,
    SubmitRequest,
    TaskSnapshot,
    UnsupportedRemoteRunOperation,
)
from .file_transfer_ports import FileTransferPort, RemoteEntryLike, TransferRecordLike
from .files_browser import (
    FileBrowserEntrySnapshot,
    FileBrowserSnapshot,
    FilesBrowserController,
)
from .files_connections import FilesConnectionController, FileTransferConnectionSnapshot
from .gui_ports import (
    ConnectionSnapshot,
    FilesPagePort,
    FileTargetSnapshot,
    PageRefreshPort,
)
from .run_tasks import RunServiceTaskLookup, RunTaskLookup
from .runs_monitor import (
    MonitorContext,
    MonitorEvent,
    MonitorPort,
    MonitorSubscription,
    RunMonitorController,
    RunsMonitorController,
    monitor_watch_id,
)
from .runs_query import (
    RunQueryController,
    RunQueryResult,
    RunQuerySnapshot,
    RunSelectionSnapshot,
    RunSelectionState,
)
from .runs_runtime import (
    MonitorRunInput,
    RunsMonitorInput,
    RunsPageRuntime,
)

__all__ = [
    "ArtifactEntry",
    "ArtifactManifest",
    "ConfFlowClient",
    "ConfFlowClientError",
    "EventPage",
    "RemoteRunHandle",
    "RemoteRunReference",
    "RemoteRunSnapshot",
    "SubmitRequest",
    "TaskSnapshot",
    "UnsupportedRemoteRunOperation",
    "ConnectionSnapshot",
    "FileTargetSnapshot",
    "FilesPagePort",
    "PageRefreshPort",
    "MonitorContext",
    "MonitorEvent",
    "MonitorPort",
    "MonitorSubscription",
    "RunMonitorController",
    "RunsMonitorController",
    "monitor_watch_id",
    "RunsPageRuntime",
    "MonitorRunInput",
    "RunsMonitorInput",
    "FileBrowserEntrySnapshot",
    "FileBrowserSnapshot",
    "FilesBrowserController",
    "FileTransferPort",
    "RemoteEntryLike",
    "TransferRecordLike",
    "FileTransferConnectionSnapshot",
    "FilesConnectionController",
    "RunQueryController",
    "RunQueryResult",
    "RunQuerySnapshot",
    "RunSelectionSnapshot",
    "RunSelectionState",
    "RunServiceTaskLookup",
    "RunTaskLookup",
]
