"""Minimal public application facade contracts.

The protocols deliberately expose immutable DTOs rather than persistence,
transport, configuration-store, or GUI objects.  Concrete implementations are
assembled by the bootstrap layer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from ..core.run import RunSpec
from ..core.submit_payload import SubmitPayload
from .comparison import RunComparison
from .outcomes import OperationFailure, OperationOutcome


@dataclass(frozen=True, slots=True)
class RunQuery:
    text: str = ""
    status: str | None = None
    workflow_kind: str | None = None


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    server_id: str
    workflow_kind: str | None
    created_at: str
    status_counts: tuple[tuple[str, int], ...] = ()
    remote_dir: str = ""
    command_template: str = ""
    run_dir: str = ""
    local_dir: str = ""

    @property
    def status_summary(self) -> Mapping[str, int]:
        """Read-only-compatible status projection for presentation clients."""

        return dict(self.status_counts)


@dataclass(frozen=True, slots=True)
class TaskSummary:
    task_id: str
    status: str
    remote_job_id: str | None = None
    error_message: str | None = None
    remote_task_files: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunDetails:
    summary: RunSummary
    remote_dir: str
    local_dir: str
    tasks: tuple[TaskSummary, ...] = ()
    mode: str = ""
    changed_count: int = 0
    warnings: tuple[str, ...] = ()


ScalarOption = str | int | float | bool | None
PresetSource = Literal["builtin", "user"]


@dataclass(frozen=True, slots=True)
class SubmitRunRequest:
    """Transport-neutral request for the complete create-and-submit workflow."""

    server_id: str
    remote_dir: str
    workflow_kind: str
    source_paths: tuple[str, ...]
    configuration: bytes | None = None
    options: tuple[tuple[str, ScalarOption], ...] = ()


@dataclass(frozen=True, slots=True)
class SubmitRunResult:
    runs: tuple[RunDetails, ...]
    submitted_task_count: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DownloadResult:
    run: RunDetails
    local_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    changed_count: int
    run_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunEvent:
    run_id: str
    event_type: str
    revision: int | None = None
    task_id: str | None = None


class RunSubscription(Protocol):
    """Idempotently closeable handle for one run event subscription."""

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RemoteFileEntry:
    name: str
    path: str
    is_dir: bool
    size_bytes: int | None = None
    modified_at: float | None = None
    permissions: str = ""


@dataclass(frozen=True, slots=True)
class TransferResult:
    local_path: str
    remote_path: str
    transferred_bytes: int
    status: str = "transferred"
    reason: str = ""
    direction: str = ""
    dry_run: bool = False

    @property
    def size_bytes(self) -> int:
        return self.transferred_bytes


@dataclass(frozen=True, slots=True)
class TransferBatchResult:
    records: tuple[TransferResult, ...]


@dataclass(frozen=True, slots=True)
class WorkflowValidation:
    valid: bool
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowPreset:
    name: str
    document: bytes
    source: PresetSource = "user"


@dataclass(frozen=True, slots=True)
class StepPreset:
    name: str
    document: bytes
    source: PresetSource


@dataclass(frozen=True, slots=True)
class ServerSnapshot:
    server_id: str
    display_name: str
    host: str
    port: int
    username: str
    document: bytes = b""


@dataclass(frozen=True, slots=True)
class SoftwareProfileSnapshot:
    name: str
    input_extensions: str
    command_template: str
    download_patterns: str


@dataclass(frozen=True, slots=True)
class GuiPreferencesSnapshot:
    default_local_folder: str = ""
    text_editor_path: str = "notepad.exe"
    max_parallel: int = 4
    language: str = "en"
    hide_dotfiles: bool = True
    software_profiles: tuple[SoftwareProfileSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    servers: tuple[ServerSnapshot, ...] = ()
    values: tuple[tuple[str, ScalarOption], ...] = ()


@runtime_checkable
class RunApplication(Protocol):
    def create(self, spec: RunSpec, *, local_dir: str = "") -> OperationOutcome[RunDetails]: ...

    def submit_existing(
        self,
        run_id: str,
        *,
        resource_overrides: Mapping[str, ScalarOption] | None = None,
    ) -> OperationOutcome[RunDetails]: ...

    def list_runs(self, query: RunQuery | None = None) -> tuple[RunSummary, ...]: ...

    def get_run(self, run_id: str) -> RunDetails: ...

    def submit(
        self,
        payload: SubmitPayload,
        *,
        dispatch: bool = True,
    ) -> OperationOutcome[SubmitRunResult]: ...

    def refresh(self, run_id: str, *, download: bool = False) -> OperationOutcome[RunDetails]: ...

    def download(self, run_id: str, patterns: tuple[str, ...] = ()) -> OperationOutcome[DownloadResult]: ...

    def cancel(self, run_id: str) -> OperationOutcome[RunDetails]: ...

    def retry_failed(self, run_id: str) -> OperationOutcome[RunDetails]: ...

    def prepare_retry_failed(self, run_id: str) -> OperationOutcome[RunDetails]: ...

    def rerun(self, run_id: str) -> OperationOutcome[RunDetails]: ...

    def prepare_rerun(self, run_id: str) -> OperationOutcome[RunDetails]: ...

    def resolve_uncertain(
        self,
        run_id: str,
        task_ids: tuple[str, ...],
        *,
        action: Literal["confirm", "abandon"],
        remote_job_ids: Mapping[str, str] | None = None,
    ) -> OperationOutcome[RunDetails]: ...

    def delete(self, run_id: str) -> OperationOutcome[None]: ...

    def recover(self, *, include_legacy_imports: bool = False) -> OperationOutcome[RecoveryResult]: ...

    def migration_failures(self) -> tuple[OperationFailure, ...]: ...

    def verify_rollback(self) -> OperationOutcome[None]: ...

    def compare(
        self,
        workspace: str,
        run_ids: tuple[str, ...],
        *,
        energy_field: str,
        profile_name: str,
    ) -> OperationOutcome[RunComparison]: ...

    def subscribe(self, run_id: str, callback: Callable[[RunEvent], None]) -> RunSubscription: ...


@runtime_checkable
class FilesApplication(Protocol):
    def list_remote(self, server_id: str, remote_dir: str) -> OperationOutcome[tuple[RemoteFileEntry, ...]]: ...

    def upload(
        self,
        server_id: str,
        local_path: str,
        remote_path: str,
        *,
        policy: str = "skip_same_size",
        dry_run: bool = False,
        progress_callback: Callable[[int, int], object] | None = None,
    ) -> OperationOutcome[TransferBatchResult]: ...

    def download(
        self,
        server_id: str,
        remote_path: str,
        local_path: str,
        *,
        policy: str = "skip_same_size",
        dry_run: bool = False,
        progress_callback: Callable[[int, int], object] | None = None,
    ) -> OperationOutcome[TransferBatchResult]: ...

    def mkdir(self, server_id: str, remote_dir: str) -> OperationOutcome[None]: ...

    def rename(self, server_id: str, old_path: str, new_path: str) -> OperationOutcome[None]: ...

    def delete(
        self,
        server_id: str,
        remote_path: str,
        *,
        recursive: bool = False,
        allowed_roots: tuple[str, ...] = (),
    ) -> OperationOutcome[None]: ...

    def preview_text(
        self,
        server_id: str,
        remote_path: str,
        *,
        max_bytes: int = 65536,
    ) -> OperationOutcome[str]: ...


@runtime_checkable
class WorkflowApplication(Protocol):
    def validate(self, server_id: str, document: bytes) -> OperationOutcome[WorkflowValidation]: ...

    def prepare_submit(self, request: SubmitRunRequest) -> OperationOutcome[SubmitRunRequest]: ...

    def list_presets(self) -> tuple[WorkflowPreset, ...]: ...

    def get_preset(self, name: str, *, source: PresetSource = "user") -> WorkflowPreset: ...

    def save_preset(self, name: str, document: bytes) -> OperationOutcome[WorkflowPreset]: ...

    def rename_preset(self, old_name: str, new_name: str) -> OperationOutcome[WorkflowPreset]: ...

    def delete_preset(self, name: str) -> OperationOutcome[None]: ...

    def list_step_presets(self) -> tuple[StepPreset, ...]: ...

    def get_step_preset(self, name: str, *, source: PresetSource) -> StepPreset: ...

    def save_step_preset(self, name: str, document: bytes) -> OperationOutcome[StepPreset]: ...


@runtime_checkable
class SettingsApplication(Protocol):
    def snapshot(self) -> SettingsSnapshot: ...

    def save_server(
        self,
        server: ServerSnapshot,
        *,
        previous_server_id: str | None = None,
    ) -> OperationOutcome[SettingsSnapshot]: ...

    def delete_server(self, server_id: str) -> OperationOutcome[SettingsSnapshot]: ...

    def preferences(self) -> GuiPreferencesSnapshot: ...

    def save_preferences(
        self, preferences: GuiPreferencesSnapshot
    ) -> OperationOutcome[GuiPreferencesSnapshot]: ...


__all__ = [
    "DownloadResult",
    "FilesApplication",
    "GuiPreferencesSnapshot",
    "RecoveryResult",
    "RemoteFileEntry",
    "RunApplication",
    "RunDetails",
    "RunEvent",
    "RunQuery",
    "RunSubscription",
    "RunSummary",
    "ScalarOption",
    "ServerSnapshot",
    "SoftwareProfileSnapshot",
    "SettingsApplication",
    "SettingsSnapshot",
    "StepPreset",
    "SubmitRunRequest",
    "SubmitRunResult",
    "TaskSummary",
    "TransferResult",
    "TransferBatchResult",
    "WorkflowApplication",
    "WorkflowPreset",
    "WorkflowValidation",
]
