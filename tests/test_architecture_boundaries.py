"""Static dependency-direction checks for the JobDesk package."""

from __future__ import annotations

import ast
import configparser
import re
from pathlib import Path

from jobdesk_app.services.run_repository import SCHEMA_VERSION

_SRC_ROOT = Path(__file__).parents[1] / "src" / "jobdesk_app"


def _get_strict_modules_from_mypy_ini() -> set[str]:
    """Read disallow_untyped_defs modules from mypy.ini."""
    mypy_ini = Path(__file__).parents[1] / "mypy.ini"
    if not mypy_ini.exists():
        return set()
    config = configparser.ConfigParser()
    config.read(mypy_ini, encoding="utf-8")
    strict_modules: set[str] = set()
    for section in config.sections():
        if not config.getboolean(section, "disallow_untyped_defs", fallback=False):
            continue
        # Convert INI section format "mypy-jobdesk_app.services.run_repository"
        # to module format "jobdesk_app.services.run_repository"
        module = section.replace("mypy-", "")
        strict_modules.add(module)
    return strict_modules


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
    # run_service_cli.py and run_service_gui.py are thin facade wrappers
    # that re-export from cli.py / gui/app.py respectively; they live in
    # services/ only to satisfy the entry-point naming convention.
    _services_root = _SRC_ROOT / "services"
    facade_files = {
        _services_root / "run_service_cli.py",
        _services_root / "run_service_gui.py",
    }

    def _is_facade(path: Path) -> bool:
        return path in facade_files

    failures: list[str] = []
    for package, prefixes in forbidden.items():
        for path, imported in _imports_under(package):
            if imported.startswith(prefixes) and not _is_facade(path):
                failures.append(f"{path.relative_to(_SRC_ROOT)} -> {imported}")
    assert failures == []


def test_pyside6_is_confined_to_gui() -> None:
    failures: list[str] = []
    for package in ("core", "remote", "services", "config"):
        for path, imported in _imports_under(package):
            if imported.startswith("PySide6"):
                failures.append(f"{path.relative_to(_SRC_ROOT)} -> {imported}")
    assert failures == []


def test_session_pool_has_no_qt_or_gui_dependency() -> None:
    session_pool = _SRC_ROOT / "services" / "session_pool.py"
    forbidden = [
        imported
        for path, imported in _imports_under("services")
        if path == session_pool
        and (imported.startswith("PySide6") or imported.startswith("jobdesk_app.gui"))
    ]
    assert forbidden == []


def test_new_architecture_modules_require_typed_definitions() -> None:
    strict_modules = _get_strict_modules_from_mypy_ini()
    assert {
        "jobdesk_app.services.run_repository",
        "jobdesk_app.services.run_coordinator",
        "jobdesk_app.services.run_monitor",
        "jobdesk_app.gui.run_monitor_qt",
    } <= strict_modules


def test_run_service_has_no_manifest_to_database_writeback() -> None:
    path = _SRC_ROOT / "services" / "run_service" / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    run_service = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "RunService"
    )

    method_names = {
        node.name
        for node in run_service.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "update_run_from_manifest" not in method_names


def test_run_repository_has_no_unjournaled_lifecycle_entry_points() -> None:
    path = _SRC_ROOT / "services" / "run_repository" / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    repository = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "RunRepository"
    )

    method_names = {
        node.name
        for node in repository.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "claim_uploaded_tasks" not in method_names
    assert "delete_run" not in method_names
    assert "replace_tasks" not in method_names


def test_schema_documentation_describes_v2_to_v5_migration_chain() -> None:
    """Verify all docs name v5 as current and describe the full v2→v5 migration chain."""
    repository_root = Path(__file__).parents[1]
    documents = {
        name: (repository_root / name).read_text(encoding="utf-8")
        for name in ("README.md", "CHANGELOG.md", "docs/TROUBLESHOOTING.md")
    }

    required_associations = {
        "v2 operation journal": r"\bv2\b.{0,120}\boperation journal\b",
        "v3 trusted workspace binding": (
            r"\bv3\b.{0,180}\btrusted[- ]workspace\b.{0,120}\bbindings?\b"
        ),
        "v4 submit ownership lease": r"\bv4\b.{0,160}\bsubmit ownership leases?\b",
        "v4 lease UTC semantics": r"(?:\bv4\b.{0,200}\butc\b|\bleases?\b.{0,100}\butc\b)",
    }

    for name, text in documents.items():
        normalized = " ".join(text.lower().split())
        current_schema = f"schema v{SCHEMA_VERSION}"
        escaped_schema = re.escape(current_schema)
        current_schema_pattern = (
            rf"(?:{escaped_schema}.{{0,40}}\bcurrent\b|"
            rf"\bcurrent\b.{{0,40}}{escaped_schema})"
        )
        assert re.search(current_schema_pattern, normalized), (
            f"{name} does not name {current_schema} as current"
        )
        for feature, pattern in required_associations.items():
            assert re.search(pattern, normalized), f"{name} omits associated {feature} wording"


def test_services_only_import_core_public_api() -> None:
    """services must not import core internal submodules like parsers directly."""
    forbidden = {
        "jobdesk_app.core.parsers.gaussian",
        "jobdesk_app.core.parsers.orca",
        "jobdesk_app.core.manifest_ops",
    }
    failures: list[str] = []
    for path, imported in _imports_under("services"):
        for forbid in forbidden:
            if imported == forbid or imported.startswith(forbid + "."):
                failures.append(f"{path.relative_to(_SRC_ROOT)} -> {imported}")
    assert failures == [], f"services must use core's public re-exports: {failures}"


def test_gui_does_not_import_paramiko_directly() -> None:
    """GUI must go through SessionPool; direct paramiko use is a layering leak."""
    failures: list[str] = []
    for path, imported in _imports_under("gui"):
        if imported == "paramiko" or imported.startswith("paramiko."):
            failures.append(f"{path.relative_to(_SRC_ROOT)} -> {imported}")
    assert failures == [], f"GUI must not import paramiko directly; use SessionPool: {failures}"
