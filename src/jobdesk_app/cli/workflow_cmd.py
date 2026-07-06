"""``jobdesk workflow`` subcommands — build, check, presets, and run.

Generates a ConfFlow workflow YAML from a small, flat JSON/INI-style
parameter file or by stitching together flags on the command line. Used by
the GUI workflow builder for headless reproduction and by operators who want
to script workflow construction without launching the GUI.

Usage examples::

    jobdesk workflow build --output confflow.yaml --preset opt-and-freq
    jobdesk workflow build --params params.json --output confflow.yaml
    jobdesk workflow build --check confflow.yaml     # layer-1 + layer-2
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..workflow.builder import (
    BuilderError,
    FormState,
    StepState,
    ValidationError,
    default_form_state,
    form_state_to_yaml,
    validate_runtime,
    validate_state,
)
from ..workflow.config.models import ConfigurationError
from ..workflow.core.exceptions import StopRequestedError
from ..workflow.engine import run_workflow


PRESETS: dict[str, dict[str, Any]] = {
    "opt-freq-orca": {
        "global": {
            "charge": 0,
            "multiplicity": 1,
            "iprog": "orca",
            "itask": "opt_freq",
            "keyword": "B3LYP def2-SVP",
            "cores_per_task": 8,
            "total_memory": "8GB",
            "rmsd_threshold": 0.25,
            "auto_clean": True,
        },
        "steps": [
            {
                "name": "conformers",
                "type": "confgen",
                "enabled": True,
                "params": {"engine": "rdkit", "angle_step": 30.0},
            },
            {
                "name": "opt-freq",
                "type": "calc",
                "enabled": True,
                "params": {
                    "iprog": "orca",
                    "itask": "opt_freq",
                    "keyword": "B3LYP def2-SVP opt freq",
                    "cores_per_task": 8,
                },
            },
        ],
    },
    "ts-orca": {
        "global": {
            "charge": 0,
            "multiplicity": 1,
            "iprog": "orca",
            "itask": "ts",
            "keyword": "B3LYP def2-SVP",
            "cores_per_task": 8,
            "total_memory": "8GB",
            "rmsd_threshold": 0.25,
            "auto_clean": True,
            "ts_bond_atoms": [1, 2],
            "ts_rescue_scan": True,
        },
        "steps": [
            {
                "name": "ts-search",
                "type": "calc",
                "enabled": True,
                "params": {
                    "iprog": "orca",
                    "itask": "ts",
                    "keyword": "B3LYP def2-SVP optts",
                    "cores_per_task": 8,
                    "ts_bond_atoms": [1, 2],
                    "ts_rescue_scan": True,
                },
            }
        ],
    },
    "sp-g16": {
        "global": {
            "charge": 0,
            "multiplicity": 1,
            "iprog": "g16",
            "itask": "sp",
            "keyword": "B3LYP/6-31G*",
            "cores_per_task": 8,
            "total_memory": "8GB",
        },
        "steps": [
            {
                "name": "sp",
                "type": "calc",
                "enabled": True,
                "params": {
                    "iprog": "g16",
                    "itask": "sp",
                    "keyword": "B3LYP/6-31G* sp",
                    "cores_per_task": 8,
                },
            }
        ],
    },
}


def add_parser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "workflow",
        help="Build or validate ConfFlow workflow YAML configs",
        description=__doc__,
    )
    sub = p.add_subparsers(dest="workflow_command", required=True)

    # ---- build --------------------------------------------------------------
    build = sub.add_parser(
        "build",
        help="Generate a confflow.yaml from CLI flags, JSON, or a preset",
    )
    build.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("confflow.yaml"),
        help="Output YAML path (default: ./confflow.yaml)",
    )
    build.add_argument(
        "--params",
        type=Path,
        default=None,
        help="Optional JSON file with {global: {...}, steps: [...]} keys.",
    )
    build.add_argument(
        "--preset",
        choices=sorted(PRESETS.keys()),
        default=None,
        help="Apply one of the bundled presets before merging --params.",
    )
    build.add_argument(
        "--global",
        dest="global_overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Override global option. Repeatable, e.g. --global charge=0",
    )
    build.add_argument(
        "--force",
        action="store_true",
        help="Overwrite --output if it already exists.",
    )
    build.add_argument(
        "--check",
        action="store_true",
        help="Run layer-1 + layer-2 validation after writing.",
    )
    build.set_defaults(func=_cmd_build)

    # ---- check --------------------------------------------------------------
    check = sub.add_parser(
        "check",
        help="Validate an existing confflow.yaml (form-layer + model-layer).",
    )
    check.add_argument("yaml_path", type=Path, help="Path to confflow.yaml")
    check.add_argument(
        "--strict",
        action="store_true",
        help="Fail on warnings (default: warnings only printed).",
    )
    check.set_defaults(func=_cmd_check)

    # ---- presets ------------------------------------------------------------
    presets = sub.add_parser(
        "presets",
        help="List built-in workflow presets.",
    )
    presets.set_defaults(func=_cmd_presets)

    # ---- run ----------------------------------------------------------------
    run = sub.add_parser(
        "run",
        help="Execute a ConfFlow workflow on one or more XYZ structures.",
    )
    run.add_argument(
        "input_xyz",
        nargs="+",
        type=Path,
        help="One or more input XYZ file paths.",
    )
    run.add_argument(
        "--config",
        "-c",
        type=Path,
        required=True,
        help="Path to confflow.yaml.",
    )
    run.add_argument(
        "--work-dir",
        "-w",
        type=Path,
        default=None,
        help="Working directory (default: directory of the first input file).",
    )
    run.add_argument(
        "--resume",
        action="store_true",
        help="Resume from the last checkpoint.",
    )
    run.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output.",
    )
    run.set_defaults(func=_cmd_workflow_run)

    return p


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_build(args) -> int:
    if args.output.exists() and not args.force:
        print(
            f"ERROR: {args.output} already exists (use --force to overwrite)",
            file=sys.stderr,
        )
        return 2

    state = default_form_state()
    if args.preset:
        state = _apply_preset(state, args.preset)
    if args.params:
        state = _apply_params_file(state, args.params)
    if args.global_overrides:
        state = _apply_global_overrides(state, args.global_overrides)

    try:
        validate_state(state)
    except ValidationError as exc:
        for err in exc.errors:
            print(f"  validation: {err}", file=sys.stderr)
        return 2

    text = form_state_to_yaml(state)

    if args.check:
        try:
            wf = validate_runtime(state)
        except (ValidationError, ConfigurationError) as exc:
            print(f"  check failed: {exc}", file=sys.stderr)
            return 2
        print(
            f"check OK: {len(wf.steps)} step(s) — "
            f"{', '.join(s.type + ':' + (s.name or '?') for s in wf.steps)}"
        )

    args.output.write_text(text, encoding="utf-8")
    print(f"wrote {args.output} ({len(state.steps)} step(s))")
    return 0


def _cmd_check(args) -> int:
    text = args.yaml_path.read_text(encoding="utf-8")
    try:
        from ..workflow.builder import yaml_to_form_state
        state = yaml_to_form_state(text)
    except BuilderError as exc:
        print(f"  parse failed: {exc}", file=sys.stderr)
        return 2

    try:
        validate_state(state)
    except ValidationError as exc:
        for err in exc.errors:
            print(f"  layer-1: {err}", file=sys.stderr)
        return 2
    print(f"layer-1 OK ({len(state.steps)} step(s))")

    try:
        wf = validate_runtime(state)
    except ConfigurationError as exc:
        print(f"  layer-2: {exc}", file=sys.stderr)
        return 2
    print(
        f"layer-2 OK ({len(wf.steps)} step(s) — "
        f"{', '.join(s.type + ':' + (s.name or '?') for s in wf.steps)})"
    )
    return 0


def _cmd_presets(args) -> int:
    for name, body in PRESETS.items():
        steps = body.get("steps", [])
        print(f"{name}: {len(steps)} step(s)")
    return 0


def _cmd_workflow_run(args) -> int:
    # Resolve input files to absolute paths.
    input_files = [str(p.resolve()) for p in args.input_xyz]

    # Resolve config file to absolute path.
    config_path = str(args.config.resolve())

    # Resolve working directory.
    if args.work_dir is not None:
        work_dir = str(args.work_dir.resolve())
    else:
        work_dir = str(args.input_xyz[0].resolve().parent)

    try:
        result = run_workflow(
            input_xyz=input_files,
            config_file=config_path,
            work_dir=work_dir,
            resume=args.resume,
            verbose=args.verbose,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except StopRequestedError:
        print("Workflow paused.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    n_conformers = result.get("n_conformers", "?")
    print(f"Workflow complete: {n_conformers} conformer(s)")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_preset(state: FormState, name: str) -> FormState:
    preset = PRESETS.get(name)
    if preset is None:
        raise SystemExit(f"unknown preset: {name}")
    raw = {
        "global": preset.get("global", {}),
        "steps": preset.get("steps", []),
    }
    return _merge_into_state(state, raw)


def _apply_params_file(state: FormState, path: Path) -> FormState:
    if not path.exists():
        raise SystemExit(f"params file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit("params file must be a JSON object")
    return _merge_into_state(state, raw)


def _apply_global_overrides(state: FormState, items: list[str]) -> FormState:
    for raw in items:
        if "=" not in raw:
            raise SystemExit(f"--global expects KEY=VALUE, got {raw!r}")
        key, _, value = raw.partition("=")
        key = key.strip()
        value = value.strip()
        coerced = _coerce_scalar_kv(key, value)
        state.global_options[key] = coerced
    return state


def _coerce_scalar_kv(key: str, value: str) -> Any:
    """Best-effort coercion for ``--global key=value`` flags."""

    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _merge_into_state(state: FormState, raw: dict[str, Any]) -> FormState:
    g = raw.get("global") or {}
    if isinstance(g, dict):
        state.global_options.update({k: v for k, v in g.items() if k in state.global_options})

    for step_raw in raw.get("steps") or []:
        if not isinstance(step_raw, dict):
            continue
        step_type = str(step_raw.get("type", "")).strip().lower()
        if step_type == "gen":
            step_type = "confgen"
        if step_type == "task":
            step_type = "calc"
        params = dict(step_raw.get("params") or {})
        if "name" not in params and "name" in step_raw:
            params["name"] = step_raw["name"]
        state.steps.append(
            StepState(
                type=step_type,
                enabled=bool(step_raw.get("enabled", True)),
                params=params,
            )
        )
    return state