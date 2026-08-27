"""Application file-transfer port and browser boundary tests."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from jobdesk_app.application import (
    FilesBrowserController,
    FileTransferPort,
    RemoteEntryLike,
    TransferRecordLike,
)
from jobdesk_app.services.file_transfer_service import FileTransferService


@dataclass
class _Entry:
    name: str
    path: str
    is_dir: bool
    size_bytes: int | None
    modified_at: float | None
    permissions: str


class _BrowserService:
    def __init__(self, entries: list[RemoteEntryLike]) -> None:
        self.calls: list[str] = []
        self.entries = entries

    def list_remote(self, remote_dir: str) -> list[RemoteEntryLike]:
        self.calls.append(remote_dir)
        return self.entries


def test_browser_consumes_structural_service_and_preserves_snapshot_behavior():
    entry = _Entry("input.dat", "/work/input.dat", False, 12, 3.5, "-rw-r--r--")
    service = _BrowserService([entry])
    controller = FilesBrowserController(lambda: service)

    snapshot = controller.list_remote("work/./")

    assert service.calls == ["/work"]
    assert snapshot.remote_dir == "/work"
    assert snapshot.generation == 1
    assert controller.generation == 1
    assert snapshot.entries[0].name == "input.dat"
    assert snapshot.entries[0].path == "/work/input.dat"


def test_file_transfer_service_is_structurally_compatible_with_port():
    service = FileTransferService(lambda: None)
    assert isinstance(service, FileTransferPort)


def test_protocol_exports_are_runtime_checkable():
    entry = _Entry("a", "/a", False, None, None, "")
    assert isinstance(entry, RemoteEntryLike)

    class _Record:
        direction = "upload"
        local_path = "a"
        remote_path = "/a"
        size_bytes = None
        status = "planned"
        reason = None
        dry_run = True

    assert isinstance(_Record(), TransferRecordLike)


def test_files_browser_does_not_import_concrete_service_or_service_entry():
    path = Path(__file__).parents[1] / "src" / "jobdesk_app" / "application" / "files_browser.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))

    imported_modules = {node.module or "" for node in tree.body if isinstance(node, ast.ImportFrom)}
    assert "services.file_transfer_service" not in imported_modules
    assert "services.protocols" not in imported_modules
    assert "FileTransferService" not in {
        alias.name for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names
    }


def test_remote_viewer_uses_transfer_port_instead_of_service_facade_directly():
    """Keep the viewer action behind the page's application transfer port."""
    path = Path(__file__).parents[1] / "src" / "jobdesk_app" / "gui" / "pages" / "file_transfer_page.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    method = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_open_remote_in_viewer"
    )

    direct_service_downloads = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "download_path"
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
        and node.func.value.attr == "_service"
    ]
    port_provider_calls = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_viewer_transfer_port"
    ]

    assert direct_service_downloads == []
    assert port_provider_calls
