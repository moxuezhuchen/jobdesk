"""Target dependency boundaries for the JobDesk 0.8 architecture."""

from __future__ import annotations

import ast
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "jobdesk_app"


def _absolute_import(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    relative = path.relative_to(_PACKAGE_ROOT).with_suffix("")
    package = ["jobdesk_app", *relative.parts[:-1]]
    if path.name == "__init__.py":
        package = ["jobdesk_app", *relative.parts[:-1]]
    keep = len(package) - (node.level - 1)
    resolved = package[:keep]
    if node.module:
        resolved.extend(node.module.split("."))
    return ".".join(resolved)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(_absolute_import(path, node))
    return imported


def _violations(package: str, forbidden: tuple[str, ...]) -> list[str]:
    failures: list[str] = []
    for path in sorted((_PACKAGE_ROOT / package).rglob("*.py")):
        for imported in _imports(path):
            if imported.startswith(forbidden):
                failures.append(f"{path.relative_to(_PACKAGE_ROOT)} -> {imported}")
    return failures


def test_legacy_architecture_packages_are_removed() -> None:
    assert not (_PACKAGE_ROOT / "services").exists()
    assert not (_PACKAGE_ROOT / "remote").exists()


def test_core_is_independent() -> None:
    assert (
        _violations(
            "core",
            (
                "jobdesk_app.application",
                "jobdesk_app.infrastructure",
                "jobdesk_app.gui",
                "jobdesk_app.config",
            ),
        )
        == []
    )


def test_application_depends_only_on_core_inside_jobdesk() -> None:
    assert (
        _violations(
            "application",
            (
                "jobdesk_app.infrastructure",
                "jobdesk_app.gui",
                "jobdesk_app.config",
                "jobdesk_app.bootstrap",
            ),
        )
        == []
    )


def test_infrastructure_does_not_depend_on_presentation_or_bootstrap() -> None:
    assert (
        _violations(
            "infrastructure",
            (
                "jobdesk_app.gui",
                "jobdesk_app.cli",
                "jobdesk_app.cli_prep",
                "jobdesk_app.bootstrap",
            ),
        )
        == []
    )


def test_gui_uses_application_or_bootstrap_not_infrastructure() -> None:
    assert (
        _violations(
            "gui",
            (
                "jobdesk_app.infrastructure",
                "jobdesk_app.services",
                "jobdesk_app.remote",
                "jobdesk_app.config",
            ),
        )
        == []
    )


def test_only_gui_composition_entries_import_bootstrap() -> None:
    # Runs remains a temporary composition boundary while its monitor and
    # lifecycle collaborators are migrated behind RunApplication.
    allowed = {Path("app.py"), Path("main_window.py"), Path("pages/runs_results_page.py")}
    failures: list[str] = []
    gui_root = _PACKAGE_ROOT / "gui"
    for path in sorted(gui_root.rglob("*.py")):
        relative = path.relative_to(gui_root)
        if relative in allowed:
            continue
        for imported in _imports(path):
            if imported.startswith("jobdesk_app.bootstrap"):
                failures.append(f"gui/{relative} -> {imported}")
    assert failures == []


def test_cli_uses_application_or_bootstrap_not_infrastructure() -> None:
    failures: list[str] = []
    for name in ("cli.py", "cli_prep.py"):
        path = _PACKAGE_ROOT / name
        for imported in _imports(path):
            if imported.startswith(
                (
                    "jobdesk_app.infrastructure",
                    "jobdesk_app.services",
                    "jobdesk_app.remote",
                    "jobdesk_app.config",
                )
            ):
                failures.append(f"{name} -> {imported}")
    assert failures == []
