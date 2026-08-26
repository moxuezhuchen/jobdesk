"""Tests for the Files page transfer runner's application port boundary."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jobdesk_app.core.file_transfer import OverwritePolicy
from jobdesk_app.gui.pages.file_transfer_runner import TransferRunner


class _ProgressBar:
    def setFormat(self, _value: str) -> None:
        pass

    def setMaximum(self, _value: int) -> None:
        pass

    def setValue(self, _value: int) -> None:
        pass

    def setVisible(self, _value: bool) -> None:
        pass


class _Port:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def upload_path(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("upload", args, kwargs))
        callback = kwargs.get("progress_callback")
        if callback is not None:
            callback(4, 8)
        return self.result

    def download_path(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("download", args, kwargs))
        callback = kwargs.get("progress_callback")
        if callback is not None:
            callback(8, 16)
        return self.result

    def preview_remote_text(self, remote_path: str) -> str:
        self.calls.append(("preview", (remote_path,), {}))
        return "preview text"


class _Context:
    def __init__(self) -> None:
        self.progress: list[tuple[int, int]] = []

    def emit_progress(self, done: int, total: int) -> None:
        self.progress.append((done, total))


def _runner(service: _Port, run_transfer, start_context) -> TransferRunner:
    return TransferRunner(
        owner=object(),
        progress_bar=_ProgressBar(),
        service_provider=lambda: service,
        language_provider=lambda: "en",
        worker_registry=[],
        on_status=lambda _message: None,
        on_error=lambda _title, _message: None,
        on_refresh_local=lambda: None,
        on_refresh_remote=lambda: None,
        run_transfer=run_transfer,
        start_context=start_context,
        start_tracked=lambda *_args, **_kwargs: None,
        clock=lambda: 1.0,
        show_preview=lambda *_args: None,
    )


def test_runner_preserves_scalar_list_and_progress_transfer_contract(
    tmp_path: Path,
) -> None:
    local_path = tmp_path / "input.dat"
    local_path.write_text("input", encoding="utf-8")
    scalar = SimpleNamespace(status="transferred")
    service = _Port(scalar)
    submitted: list[tuple[Any, str, Any]] = []

    def run_transfer(target, label, refresh):
        submitted.append((target, label, refresh))

    runner = _runner(service, run_transfer, lambda *_args, **_kwargs: None)
    runner.upload_selected(local_path, "/work/input.dat")

    context = _Context()
    assert submitted[0][0](context) == [scalar]
    assert submitted[0][1] == "Upload"
    assert context.progress == [(4, 8)]
    assert service.calls[0][0] == "upload"
    assert service.calls[0][1][2] == OverwritePolicy.overwrite

    records = [SimpleNamespace(status="transferred"), SimpleNamespace(status="skipped")]
    service.result = records
    runner.download_selected("/remote/output.dat", tmp_path)
    download_context = _Context()
    assert submitted[1][0](download_context) == records
    assert download_context.progress == [(8, 16)]
    assert service.calls[1][0] == "download"
    assert service.calls[1][1][2] == OverwritePolicy.overwrite


def test_runner_preview_uses_port_provider() -> None:
    service = _Port(SimpleNamespace(status="transferred"))
    started: dict[str, Any] = {}

    def start_context(_owner, **kwargs):
        started.update(kwargs)

    shown: list[tuple[Any, str, str]] = []
    runner = _runner(service, lambda *_args: None, start_context)
    runner._show_preview = lambda parent, path, text: shown.append((parent, path, text))
    runner.preview_remote("/remote/output.txt", "parent")

    assert started["target"](None) == "preview text"
    started["on_result"]("preview text")
    assert shown == [("parent", "/remote/output.txt", "preview text")]
    assert service.calls == [("preview", ("/remote/output.txt",), {})]


def test_runner_does_not_import_concrete_file_transfer_service() -> None:
    path = Path(__file__).parents[1] / "src" / "jobdesk_app" / "gui" / "pages" / "file_transfer_runner.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    imports = [node.module or "" for node in tree.body if isinstance(node, ast.ImportFrom)]
    imported_names = {
        alias.name for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names
    }
    assert "services.file_transfer_service" not in imports
    assert "FileTransferService" not in imported_names
    assert "FileTransferPort" in imported_names
