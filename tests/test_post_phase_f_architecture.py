"""Phase F architecture fitness tests.

The initial allowlists are deliberately narrow characterization records for
the violations that the remediation plan removes.  Each owning phase must
shrink its allowlist to the empty set rather than widening it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

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


def test_post_phase_f_matrix_installs_candidate_producer_dependencies() -> None:
    """The clean base consumer row must still be able to run producer tests."""
    workflow_path = _SRC.parents[1] / ".github" / "workflows" / "post-phase-f-contract.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    # PyYAML's YAML 1.1 loader treats the GitHub Actions ``on`` key as a boolean.
    on_config = workflow.get("on", workflow.get(True))
    assert on_config["pull_request"]["types"] == ["opened", "synchronize", "reopened"]
    matrix = workflow["jobs"]["consumer-matrix"]["strategy"]["matrix"]
    assert matrix["install"] == ["base", "chem"]
    assert matrix["producer"] == ["stable", "candidate"]

    steps = workflow["jobs"]["consumer-matrix"]["steps"]
    candidate_install = next(
        step for step in steps if step["name"] == "Download and install the published candidate producer wheel"
    )
    candidate_run = candidate_install["run"]
    assert 'python -m pip install "$wheel"' in candidate_run
    assert "--no-deps" not in candidate_run
    assert "releases/download/v2.1.1/confflow-2.1.1-py3-none-any.whl" in candidate_run
    assert "gh release download" not in candidate_run
    assert "3425d97246ee6d37369ecce672dfa154643179cc3ee744eb332aee4b94dbc5f3" in candidate_run
    assert "python -m build" not in candidate_run

    candidate_tag = next(step for step in steps if step["name"] == "Verify selected published candidate tag")
    assert 'test "${{ inputs.confflow_ref || \'v2.1.1\' }}" = "v2.1.1"' in candidate_tag["run"]
    assert "338b53b3a34593271b926fc9e96010186141a386" in candidate_tag["run"]

    stable_tag = next(step for step in steps if step["name"] == "Verify selected stable tag")
    assert "69819350d340a6aeccf95aa175edfd1c3f63404b" in stable_tag["run"]

    stable_install = next(
        step for step in steps if step["name"] == "Download and install the current stable producer wheel"
    )
    assert "releases/download/v2.0.0/confflow-2.0.0-py3-none-any.whl" in stable_install["run"]

    verification = next(
        step for step in steps if step["name"] == "Verify installed wheels, package data, and dependency closure"
    )
    assert "python -m pip check" in verification["run"]
    assert "from importlib.resources import files" in verification["run"]

    for step_name in (
        "Verify producer identity and configuration contract",
        "Verify installed wheels, package data, and dependency closure",
    ):
        run = next(step for step in steps if step["name"] == step_name)["run"]
        script = run.split("python - <<'PY'\n", 1)[1].split("\nPY", 1)[0]
        ast.parse(script, filename=f"post-phase-f-contract:{step_name}")
