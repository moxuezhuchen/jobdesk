from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config.runtime import RuntimeBindingStore
from ..config.schema import RuntimeBinding


@dataclass(frozen=True)
class WizardDiscoverySpec:
    name: str
    mode: str
    entry_glob: str
    execution_profile: str
    task_id_prefix: str = ""
    task_id_from: str = "stem"
    directory_glob: str | None = None
    associated_globs: list[str] = field(default_factory=list)
    include: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WizardProfileSpec:
    name: str
    label: str
    command: str
    max_parallel: int = 4
    tags: list[str] = field(default_factory=lambda: ["cpu"])


@dataclass(frozen=True)
class WizardBindingSpec:
    execution_profile: str
    server_id: str
    remote_work_dir: str
    max_parallel: int | None = None


@dataclass(frozen=True)
class WizardProjectSpec:
    project_id: str
    project_name: str
    project_root: Path
    input_dir: str = "inputs"
    result_dir: str = "results"
    discoveries: list[WizardDiscoverySpec] = field(default_factory=list)
    profiles: list[WizardProfileSpec] = field(default_factory=list)
    bindings: list[WizardBindingSpec] = field(default_factory=list)
    runtime_bindings_path: Path | None = None


@dataclass(frozen=True)
class WizardCreateResult:
    project_root: Path
    project_yaml_path: Path
    runtime_bindings_path: Path | None


def create_project_from_wizard(spec: WizardProjectSpec) -> WizardCreateResult:
    project_root = spec.project_root.resolve()
    project_yaml_path = project_root / "project.yaml"
    if project_yaml_path.exists():
        raise FileExistsError(f"project.yaml already exists: {project_yaml_path}")
    _validate_spec(spec)

    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / spec.input_dir).mkdir(parents=True, exist_ok=True)
    (project_root / spec.result_dir).mkdir(parents=True, exist_ok=True)

    project_yaml_path.write_text(
        yaml.safe_dump(_project_yaml_data(spec), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    runtime_path = spec.runtime_bindings_path
    if spec.bindings:
        store = RuntimeBindingStore(runtime_path)
        for binding in spec.bindings:
            store.save_binding(
                spec.project_id,
                binding.execution_profile,
                RuntimeBinding(
                    server_id=binding.server_id,
                    remote_work_dir=binding.remote_work_dir,
                    max_parallel=binding.max_parallel,
                ),
            )
        runtime_path = store.path

    return WizardCreateResult(
        project_root=project_root,
        project_yaml_path=project_yaml_path,
        runtime_bindings_path=runtime_path,
    )


def _validate_spec(spec: WizardProjectSpec) -> None:
    if not spec.project_id.strip():
        raise ValueError("project_id is required")
    if not spec.project_name.strip():
        raise ValueError("project_name is required")
    if not spec.discoveries:
        raise ValueError("at least one task discovery is required")
    if not spec.profiles:
        raise ValueError("at least one execution profile is required")
    profile_names = {profile.name for profile in spec.profiles}
    if len(profile_names) != len(spec.profiles):
        raise ValueError("execution profile names must be unique")
    discovery_names = {discovery.name for discovery in spec.discoveries}
    if len(discovery_names) != len(spec.discoveries):
        raise ValueError("task discovery names must be unique")
    for discovery in spec.discoveries:
        if discovery.execution_profile not in profile_names:
            raise ValueError(
                f"discovery {discovery.name!r} references missing execution_profile "
                f"{discovery.execution_profile!r}"
            )
    for binding in spec.bindings:
        if binding.execution_profile not in profile_names:
            raise ValueError(
                f"binding references missing execution_profile {binding.execution_profile!r}"
            )


def _project_yaml_data(spec: WizardProjectSpec) -> dict:
    return {
        "project_id": spec.project_id,
        "project": {"name": spec.project_name},
        "local_paths": {
            "input_dir": f"./{spec.input_dir}",
            "result_dir": f"./{spec.result_dir}",
        },
        "task_discoveries": [_discovery_data(discovery) for discovery in spec.discoveries],
        "execution_profiles": {
            profile.name: {
                "label": profile.label,
                "command": profile.command,
                "requirements": {"tags": profile.tags},
                "defaults": {"max_parallel": profile.max_parallel},
            }
            for profile in spec.profiles
        },
        "upload": {
            "task_files": {
                "include": [],
                "exclude": [],
                "require_entry_file": True,
                "on_missing": "error",
            }
        },
        "download": {"patterns": []},
        "extract": {"results": []},
    }


def _discovery_data(discovery: WizardDiscoverySpec) -> dict:
    data = {
        "name": discovery.name,
        "mode": discovery.mode,
        "entry_glob": discovery.entry_glob,
        "task_id_prefix": discovery.task_id_prefix,
        "task_id_from": discovery.task_id_from,
        "execution_profile": discovery.execution_profile,
    }
    if discovery.directory_glob:
        data["directory_glob"] = discovery.directory_glob
    if discovery.associated_globs:
        data["associated_globs"] = discovery.associated_globs
    if discovery.include:
        data["include"] = discovery.include
    return data
