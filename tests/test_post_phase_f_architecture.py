"""Phase F architecture fitness tests.

The initial allowlists are deliberately narrow characterization records for
the violations that the remediation plan removes.  Each owning phase must
shrink its allowlist to the empty set rather than widening it.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).parents[1] / "src" / "jobdesk_app"


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _gui_repository_accesses() -> set[str]:
    accesses: set[str] = set()
    for path in (_SRC / "gui").rglob("*.py"):
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "repository":
                accesses.add(f"{path.relative_to(_SRC).as_posix()}:{node.lineno}")
    return accesses


def _main_window_private_child_accesses() -> set[str]:
    accesses: set[str] = set()
    path = _SRC / "gui" / "main_window.py"
    tree = _tree(path)
    child_pages = {"files_page", "workflow_page", "runs_page", "settings_page"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not node.attr.startswith("_"):
            continue
        parent = node.value
        if isinstance(parent, ast.Attribute) and isinstance(parent.value, ast.Name):
            if parent.value.id == "self" and parent.attr in child_pages:
                accesses.add(f"{path.relative_to(_SRC).as_posix()}:{node.lineno}")
    return accesses


def _ssh_client_repository_leaks() -> set[str]:
    accesses: set[str] = set()
    path = _SRC / "services" / "ssh_confflow_client.py"
    tree = _tree(path)
    def _dotted(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = _dotted(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return ""

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr != "repository":
            continue
        if ".service.repository" in _dotted(node):
            accesses.add(f"{path.relative_to(_SRC).as_posix()}:{node.lineno}")
    return accesses


def _submit_ownership_run_service_imports() -> set[str]:
    path = _SRC / "services" / "submit_ownership.py"
    tree = _tree(path)
    return {
        f"{path.relative_to(_SRC).as_posix()}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        and any(alias.name == "jobdesk_app.services.run_service" for alias in node.names)
    }


def test_phase_f_jobdesk_debt_allowlist_is_explicit_and_narrow() -> None:
    observed = {
        "gui_repository": _gui_repository_accesses(),
        "main_window_private_child": _main_window_private_child_accesses(),
        "ssh_client_repository": _ssh_client_repository_leaks(),
        "submit_ownership_cycle": _submit_ownership_run_service_imports(),
    }
    allowed = {
        "gui_repository": set(),
        "main_window_private_child": set(),
        "ssh_client_repository": set(),
        "submit_ownership_cycle": set(),
    }
    for name, paths in observed.items():
        assert paths <= allowed[name], f"new {name} violation(s): {sorted(paths - allowed[name])}"
