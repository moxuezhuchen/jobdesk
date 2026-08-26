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
    package_parts = [
        "jobdesk_app",
        *module_path.relative_to(_SRC_ROOT).with_suffix("").parts[:-1],
    ]
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
        if path == session_pool and (imported.startswith("PySide6") or imported.startswith("jobdesk_app.gui"))
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


def test_confflow_application_facade_does_not_import_gui_or_remote_implementations() -> None:
    """The new facade may coordinate services but must not own transport or GUI code."""
    path = _SRC_ROOT / "application" / "confflow_client.py"
    imports = [imported for _path, imported in _imports_under("application") if _path == path]

    forbidden = ("jobdesk_app.gui", "PySide6", "jobdesk_app.remote")
    assert not [imported for imported in imports if imported.startswith(forbidden)]


def test_ssh_confflow_client_uses_public_service_and_coordinator_ports() -> None:
    """Remote facades must not bypass lifecycle/service ownership boundaries."""

    path = _SRC_ROOT / "services" / "ssh_confflow_client.py"
    text = path.read_text(encoding="utf-8-sig")
    assert "._server_lookup" not in text
    assert "._clients" not in text
    assert ".repository" not in text


def test_run_service_has_no_manifest_to_database_writeback() -> None:
    path = _SRC_ROOT / "services" / "run_service" / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    run_service = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RunService")

    method_names = {node.name for node in run_service.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

    assert "update_run_from_manifest" not in method_names


def test_run_repository_has_no_unjournaled_lifecycle_entry_points() -> None:
    path = _SRC_ROOT / "services" / "run_repository" / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    repository = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RunRepository")

    method_names = {node.name for node in repository.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

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
        "v3 trusted workspace binding": (r"\bv3\b.{0,180}\btrusted[- ]workspace\b.{0,120}\bbindings?\b"),
        "v4 submit ownership lease": r"\bv4\b.{0,160}\bsubmit ownership leases?\b",
        "v4 lease UTC semantics": r"(?:\bv4\b.{0,200}\butc\b|\bleases?\b.{0,100}\butc\b)",
    }

    for name, text in documents.items():
        normalized = " ".join(text.lower().split())
        current_schema = f"schema v{SCHEMA_VERSION}"
        escaped_schema = re.escape(current_schema)
        current_schema_pattern = rf"(?:{escaped_schema}.{{0,40}}\bcurrent\b|" rf"\bcurrent\b.{{0,40}}{escaped_schema})"
        assert re.search(current_schema_pattern, normalized), f"{name} does not name {current_schema} as current"
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


def test_gui_does_not_import_remote_implementations_directly() -> None:
    failures = [
        f"{path.relative_to(_SRC_ROOT)} -> {imported}"
        for path, imported in _imports_under("gui")
        if imported.startswith("jobdesk_app.remote")
    ]
    assert failures == [], f"GUI must use application clients instead of remote implementations: {failures}"


def test_gui_does_not_access_run_repository_directly() -> None:
    """GUI code must use public application/service queries, not persistence internals."""
    failures: list[str] = []
    for path in sorted((_SRC_ROOT / "gui").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "repository":
                failures.append(f"{path.relative_to(_SRC_ROOT)}:{node.lineno}")
    assert failures == [], f"GUI must query runs through RunService: {failures}"


def test_control_collaborators_do_not_reach_coordinator_or_repository() -> None:
    """JD2a collaborators receive narrow values/callbacks, never persistence owners."""
    names = (
        "confflow_control_handoff.py",
        "confflow_control_launcher.py",
        "confflow_control_artifacts.py",
        "confflow_control_reconciliation.py",
        "confflow_control_run_state.py",
    )
    failures: list[str] = []
    for name in names:
        path = _SRC_ROOT / "services" / name
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "jobdesk_app.services.run_coordinator":
                failures.append(f"{name}:{node.lineno}:RunCoordinator import")
            if isinstance(node, ast.Attribute) and node.attr == "repository":
                failures.append(f"{name}:{node.lineno}:repository access")
    assert failures == [], f"control collaborators must not own coordinator persistence: {failures}"


def test_main_window_does_not_read_files_page_private_connection_state() -> None:
    path = _SRC_ROOT / "gui" / "main_window.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    forbidden = {"_service", "_connected_server_id", "_connected_server"}
    failures: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr not in forbidden:
            continue
        owner = node.value
        if isinstance(owner, ast.Attribute) and owner.attr == "files_page":
            failures.append(f"main_window.py:{node.lineno}:{node.attr}")
    assert failures == [], f"MainWindow must use the Files-page public snapshot: {failures}"


def test_main_window_uses_public_page_actions_only() -> None:
    """The shell must not inspect widget controls or private page actions."""
    path = _SRC_ROOT / "gui" / "main_window.py"
    text = path.read_text(encoding="utf-8-sig")
    assert "_refresh_all" not in text
    assert "files_page.remote_path" not in text

    tree = ast.parse(text, filename=str(path))
    page_names = {"files_page", "workflow_page", "runs_page", "settings_page"}
    failures: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not node.attr.startswith("_"):
            continue
        owner = node.value
        if isinstance(owner, ast.Attribute) and owner.attr in page_names:
            failures.append(f"main_window.py:{node.lineno}:{owner.attr}.{node.attr}")
    assert failures == [], f"MainWindow must use page public ports/actions: {failures}"


def test_gui_ports_are_application_layer_only() -> None:
    """The shell ports cannot pull Qt widgets or concrete transport services."""
    path = _SRC_ROOT / "application" / "gui_ports.py"
    imports = [imported for imported_path, imported in _imports_under("application") if imported_path == path]
    forbidden = (
        "PySide6",
        "jobdesk_app.gui",
        "jobdesk_app.remote",
        "jobdesk_app.services",
    )
    assert not [imported for imported in imports if imported.startswith(forbidden)]


def test_connection_snapshots_do_not_store_file_transfer_service() -> None:
    """Status snapshots may cross the GUI boundary, but live services may not."""
    for relative, class_name in (
        ("application/files_connections.py", "FileTransferConnectionSnapshot"),
        ("application/gui_ports.py", "ConnectionSnapshot"),
    ):
        path = _SRC_ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        snapshot = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
        fields = {
            node.target.id
            for node in snapshot.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        assert "service" not in fields, f"{relative} stores a mutable service in {class_name}"
        assert "connected" in fields
        assert "ready" in fields


def test_files_page_does_not_write_connection_coordinator_private_state() -> None:
    path = _SRC_ROOT / "gui" / "pages" / "file_transfer_page.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    failures: list[str] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        for target in targets:
            if not isinstance(target, ast.Attribute) or not target.attr.startswith("_"):
                continue
            owner = target.value
            if (
                isinstance(owner, ast.Attribute)
                and owner.attr == "_connections"
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "self"
            ):
                failures.append(f"file_transfer_page.py:{target.lineno}:{target.attr}")
    assert failures == [], f"Files page must use coordinator public lifecycle methods: {failures}"


def test_files_page_does_not_import_or_construct_run_service() -> None:
    """Task reads cross the narrow application port, not RunService directly."""
    path = _SRC_ROOT / "gui" / "pages" / "file_transfer_page.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    failures: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_module = _absolute_import(path, node)
            if imported_module == "jobdesk_app.services.run_service":
                failures.append(f"file_transfer_page.py:{node.lineno}:RunService import")
            for alias in node.names:
                if alias.name == "RunService":
                    failures.append(f"file_transfer_page.py:{node.lineno}:RunService import")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "jobdesk_app.services.run_service":
                    failures.append(f"file_transfer_page.py:{node.lineno}:RunService import")
        elif isinstance(node, ast.Call):
            target = node.func
            if (isinstance(target, ast.Name) and target.id == "RunService") or (
                isinstance(target, ast.Attribute) and target.attr == "RunService"
            ):
                failures.append(f"file_transfer_page.py:{node.lineno}:RunService construction")
    assert failures == [], f"Files page must use RunTaskLookup: {failures}"


def test_runs_page_migrated_runtime_paths_do_not_construct_services() -> None:
    """The first Runs slice must resolve its service graph through the runtime port.

    The delete/retry/rerun, submit, and uncertain-task lifecycle slices join
    the query/monitor paths.  Keeping the allowlist explicit prevents this
    check from claiming that the whole page has already moved while still
    making each completed boundary mechanically enforceable.
    """

    path = _SRC_ROOT / "gui" / "pages" / "runs_results_page.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    page = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RunsResultsPage")
    migrated = {
        node.name: node
        for node in page.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name
        in {
            "__init__",
            "_start_monitoring",
            "_flush_task_done",
            "_start_checkpoint_progress",
            "_on_monitor_refresh_done",
            "_coordinator_for",
            "_client_for",
            "_execute_refresh_use_case",
            "_execute_download_use_case",
            "_execute_progress_use_case",
            "_load_tasks",
            "_selected_record",
            "_retry_failed",
            "_rerun_all",
            "_delete_run",
            "_stop_run",
            "_resolve_uncertain_selection",
            "_submit_record",
        }
    }
    failures: list[str] = []
    forbidden = {"RunService", "RunCoordinator", "SessionPool", "SSHConfFlowClient"}
    for method_name, method in migrated.items():
        for node in ast.walk(method):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            called_name = (
                target.id
                if isinstance(target, ast.Name)
                else target.attr if isinstance(target, ast.Attribute) else None
            )
            if called_name in forbidden:
                failures.append(f"{method_name}:{node.lineno}:{called_name}")
            runtime_owner = (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Attribute)
                and isinstance(target.value.value, ast.Name)
                and target.value.value.id == "self"
                and target.value.attr == "_runtime"
            )
            if (
                method_name == "_execute_refresh_use_case"
                and called_name
                in {
                    "attach",
                    "refresh_outcome",
                    "service",
                }
                and not runtime_owner
            ):
                failures.append(f"{method_name}:{node.lineno}:{called_name}")
            if (
                method_name == "_execute_download_use_case"
                and called_name
                in {
                    "attach",
                    "download_outcome",
                    "service",
                }
                and not runtime_owner
            ):
                failures.append(f"{method_name}:{node.lineno}:{called_name}")
            if (
                method_name == "_execute_progress_use_case"
                and called_name
                in {
                    "sync_progress",
                    "service",
                }
                and not runtime_owner
            ):
                failures.append(f"{method_name}:{node.lineno}:{called_name}")
            if method_name == "_stop_run":
                if called_name in {
                    "_client_for",
                    "attach",
                    "cancel",
                    "cancel_outcome",
                    "service",
                }:
                    if not runtime_owner:
                        failures.append(f"{method_name}:{node.lineno}:{called_name}")
            if method_name == "_start_monitoring":
                if isinstance(target, ast.Name) and target.id in {
                    "load_servers",
                    "load_state",
                }:
                    failures.append(f"{method_name}:{node.lineno}:{target.id}")
                elif isinstance(target, ast.Attribute) and target.attr == "service":
                    failures.append(f"{method_name}:{node.lineno}:service")
    assert failures == [], f"migrated Runs paths must use RunsPageRuntime: {failures}"
