"""Tests for the Files page filesystem-operations port boundary."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, cast

from jobdesk_app.application.file_transfer_ports import FileTransferPort
from jobdesk_app.gui.pages.file_transfer_operations import FileOperations


class _Port:
    def __init__(self, *, raise_on_upload: bool = False) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.raise_on_upload = raise_on_upload

    def upload_path(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("upload", args, kwargs))
        if self.raise_on_upload:
            raise RuntimeError("upload failed")

    def mkdir_remote(self, remote_dir: str) -> None:
        self.calls.append(("mkdir", (remote_dir,), {}))

    def rename_remote(self, old_path: str, new_path: str) -> None:
        self.calls.append(("rename", (old_path, new_path), {}))

    def delete_remote(self, remote_path: str, **kwargs: Any) -> None:
        self.calls.append(("delete", (remote_path,), kwargs))


def _operations(
    service: _Port | None,
    *,
    prompts: dict[str, tuple[str, bool]] | None = None,
    start_worker=None,
    on_error=None,
    on_refresh_remote=None,
) -> FileOperations:
    prompts = prompts or {}
    return FileOperations(
        service_provider=lambda: cast(FileTransferPort | None, service),
        local_root_provider=lambda: None,
        language_provider=lambda: "en",
        on_status=lambda _message: None,
        on_error=on_error or (lambda _title, _message: None),
        on_refresh_local=lambda: None,
        on_refresh_remote=on_refresh_remote or (lambda: None),
        prompt_new_name=lambda _title, _label, _default: prompts.get("name", ("", False)),
        prompt_new_folder=lambda _title, _label: prompts.get("folder", ("", False)),
        prompt_text=lambda _title, _label: prompts.get("text", ("", False)),
        ask_confirm=lambda _title, _body: True,
        open_editor=lambda _path: None,
        start_worker=start_worker or (lambda *_args: None),
        remote_dir_provider=lambda: "/remote/jobs",
    )


def test_remote_operations_use_structural_port_and_preserve_safety_arguments() -> None:
    service = _Port()
    refreshed: list[str] = []
    started: list[Any] = []

    def start_worker(target, on_result, on_error):
        started.append((target, on_result, on_error))

    operations = _operations(
        service,
        prompts={"folder": ("created", True), "name": ("renamed", True)},
        start_worker=start_worker,
        on_refresh_remote=lambda: refreshed.append("remote"),
    )

    operations.mkdir_remote()
    operations.rename_remote("/remote/jobs/input.xyz")
    operations.move_remote_paths_into_directory(["/remote/jobs/input.xyz"], "/remote/archive")
    operations.delete_remote(["/remote/jobs/result.out"], "/remote/jobs")

    assert service.calls[:3] == [
        ("mkdir", ("/remote/jobs/created",), {}),
        ("rename", ("/remote/jobs/input.xyz", "/remote/jobs/renamed"), {}),
        ("rename", ("/remote/jobs/input.xyz", "/remote/archive/input.xyz"), {}),
    ]
    assert len(started) == 1
    started[0][0](None)
    assert service.calls[3] == (
        "delete",
        ("/remote/jobs/result.out",),
        {"recursive": True, "extra_allowed_roots": ["/remote/jobs"]},
    )
    started[0][1](4)
    assert refreshed == ["remote", "remote", "remote", "remote"]


def test_new_file_remote_uploads_empty_temp_file() -> None:
    service = _Port()
    errors: list[tuple[str, str]] = []
    operations = _operations(
        service,
        prompts={"text": ("new.inp", True)},
        on_error=lambda title, message: errors.append((title, message)),
    )

    operations.new_file_remote()

    assert len(service.calls) == 1
    method, args, kwargs = service.calls[0]
    assert method == "upload"
    assert Path(args[0]).exists() is False
    assert args[1] == "/remote/jobs/new.inp"
    assert kwargs == {}
    assert errors == []


def test_new_file_remote_reports_service_errors_and_cleans_up() -> None:
    service = _Port(raise_on_upload=True)
    errors: list[tuple[str, str]] = []
    operations = _operations(
        service,
        prompts={"text": ("failed.inp", True)},
        on_error=lambda title, message: errors.append((title, message)),
    )

    operations.new_file_remote()

    _, args, _ = service.calls[0]
    assert Path(args[0]).exists() is False
    assert errors == [("New File Error", "upload failed")]


def test_operations_do_not_import_concrete_file_transfer_service() -> None:
    path = Path(__file__).parents[1] / "src" / "jobdesk_app" / "gui" / "pages" / "file_transfer_operations.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    imports = [node.module or "" for node in tree.body if isinstance(node, ast.ImportFrom)]
    imported_names = {
        alias.name for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names
    }
    assert "services.file_transfer_service" not in imports
    assert "FileTransferService" not in imported_names
    assert "FileTransferPort" in imported_names
