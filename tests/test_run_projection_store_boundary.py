"""Boundary tests for the application run projection port and adapter."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from jobdesk_app.services.run_projection_store import RunProjectionStoreAdapter


class _RecordingRunService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def load_run(self, run_id: str) -> object:
        self.calls.append(("load_run", (run_id,)))
        return "run"

    def load_tasks(self, run_id: str) -> list[object]:
        self.calls.append(("load_tasks", (run_id,)))
        return ["task"]

    def load_run_provenance(self, run_id: str) -> dict[str, object]:
        self.calls.append(("load_run_provenance", (run_id,)))
        return {"run_id": run_id}

    def mutate_tasks(self, run_id: str, mutation: object) -> list[object]:
        self.calls.append(("mutate_tasks", (run_id, mutation)))
        return ["mutated"]

    def update_run(self, record: object) -> None:
        self.calls.append(("update_run", (record,)))


def test_adapter_delegates_the_complete_projection_surface() -> None:
    service = _RecordingRunService()
    adapter = RunProjectionStoreAdapter(service)
    mutation = object()
    record = object()

    assert adapter.load_run("run-1") == "run"
    assert adapter.load_tasks("run-1") == ["task"]
    assert adapter.load_run_provenance("run-1") == {"run_id": "run-1"}
    assert adapter.mutate_tasks("run-1", mutation) == ["mutated"]
    assert adapter.update_run(record) is None

    assert service.calls == [
        ("load_run", ("run-1",)),
        ("load_tasks", ("run-1",)),
        ("load_run_provenance", ("run-1",)),
        ("mutate_tasks", ("run-1", mutation)),
        ("update_run", (record,)),
    ]


def _imports(tree: ast.AST) -> list[ast.alias | ast.ImportFrom]:
    return [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]


def test_ports_keeps_gui_remote_and_service_imports_type_only() -> None:
    path = Path(__file__).parents[1] / "src" / "jobdesk_app" / "application" / "ports.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden = ("paramiko", "PySide6", "jobdesk_app.remote", "jobdesk_app.services")

    runtime_imports: list[str] = []
    for node in _imports(tree):
        if isinstance(node, ast.Import):
            runtime_imports.extend(alias.name for alias in node.names)
        elif not _inside_type_checking(tree, node):
            runtime_imports.append(node.module or "")

    assert not [name for name in runtime_imports if name == "paramiko" or name.startswith(forbidden[1:])]


def _inside_type_checking(tree: ast.AST, target: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING" and target in node.body:
            return True
    return False
