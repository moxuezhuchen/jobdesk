"""Static dependency-direction checks for the JobDesk package."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

_SRC_ROOT = Path(__file__).parents[1] / "src" / "jobdesk_app"


def _absolute_import(module_path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package_parts = ["jobdesk_app", *module_path.relative_to(_SRC_ROOT).with_suffix("").parts[:-1]]
    keep = len(package_parts) - (node.level - 1)
    package_parts = package_parts[:keep]
    if node.module:
        package_parts.extend(node.module.split("."))
    return ".".join(package_parts)


def _imports_under(package: str) -> list[tuple[Path, str]]:
    violations: list[tuple[Path, str]] = []
    for path in sorted((_SRC_ROOT / package).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                violations.extend((path, alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                violations.append((path, _absolute_import(path, node)))
    return violations


def test_package_dependency_direction() -> None:
    forbidden = {
        "core": ("jobdesk_app.services", "jobdesk_app.gui"),
        "remote": ("jobdesk_app.services", "jobdesk_app.gui"),
        "services": ("jobdesk_app.gui", "PySide6"),
    }
    failures: list[str] = []
    for package, prefixes in forbidden.items():
        for path, imported in _imports_under(package):
            if imported.startswith(prefixes):
                failures.append(f"{path.relative_to(_SRC_ROOT)} -> {imported}")
    assert failures == []


def test_pyside6_is_confined_to_gui() -> None:
    failures: list[str] = []
    for package in ("core", "remote", "services", "config"):
        for path, imported in _imports_under(package):
            if imported.startswith("PySide6"):
                failures.append(f"{path.relative_to(_SRC_ROOT)} -> {imported}")
    assert failures == []


def test_new_architecture_modules_require_typed_definitions() -> None:
    config = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    strict_modules: set[str] = set()
    for override in config["tool"]["mypy"]["overrides"]:
        if not override.get("disallow_untyped_defs"):
            continue
        modules = override["module"]
        strict_modules.update([modules] if isinstance(modules, str) else modules)
    assert {
        "jobdesk_app.services.run_repository",
        "jobdesk_app.services.run_coordinator",
        "jobdesk_app.services.run_monitor",
        "jobdesk_app.gui.run_monitor_qt",
    } <= strict_modules
