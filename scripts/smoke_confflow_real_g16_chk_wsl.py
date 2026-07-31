#!/usr/bin/env python3
"""Strict non-computing wrapper for the real-g16 checkpoint smoke."""

from __future__ import annotations

import pathlib
import sys

_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.confflow_real_g16_smoke_common import (  # noqa: E402
    SmokeValidationError,
    build_inner_harness,
    cleanup_remote,
    deploy_harness,
    parse_result_dir,
    pull_artifacts,
    run_inner,
    validate_smoke_bundle,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS_ROOT = REPO_ROOT / "tmp60f7j8ix"
RESULTS_DIR = RESULTS_ROOT / "m2_4b_g16_chk_smoke"
DEST_WSL = "/tmp/jobdesk-confflow-g16-checkpoint-inner.sh"

WORKFLOW_YAML = """global:
  gaussian_path: /opt/g16/g16
  cores_per_task: 1
  total_memory: "1GB"
  max_parallel_jobs: 1
  charge: 0
  multiplicity: 1
steps:
  - name: step_06_g16_opt
    type: calc
    params:
      iprog: g16
      itask: opt
      keyword: "opt(nomicro) b3lyp/6-31g(d)"
      cores_per_task: 1
      total_memory: "1GB"
      max_parallel_jobs: 1
      gaussian_write_chk: true
  - name: step_07_g16_sp_readchk
    type: calc
    params:
      iprog: g16
      itask: sp
      keyword: "sp guess=read geom=allcheck"
      cores_per_task: 1
      total_memory: "1GB"
      max_parallel_jobs: 1
      chk_from_step: step_06_g16_opt
"""

INNER_HARNESS = build_inner_harness(workflow_yaml=WORKFLOW_YAML, label="two-step checkpoint")


def main() -> int:
    print("[win] stamping strict checkpoint harness", flush=True)
    try:
        deploy_harness(INNER_HARNESS, destination=DEST_WSL)
    except SmokeValidationError as exc:
        print(f"[win] FAIL: {exc}", file=sys.stderr)
        return 1

    print("[win] running ConfFlow checkpoint smoke", flush=True)
    inner = run_inner(DEST_WSL)
    if inner.stdout:
        print(inner.stdout, end="")
    if inner.stderr:
        print(inner.stderr, end="", file=sys.stderr)

    remote_tmp = parse_result_dir(inner.stdout)
    if remote_tmp:
        try:
            pull_artifacts(remote_tmp, RESULTS_DIR, name="m2_4b_g16_chk_smoke")
            print(f"[win] diagnostics staged at {RESULTS_DIR}", flush=True)
        except (OSError, SmokeValidationError) as exc:
            print(f"[win] FAIL: cannot preserve smoke diagnostics: {exc}", file=sys.stderr)
            return 1

    if inner.returncode != 0:
        print(f"[win] FAIL: ConfFlow returned non-zero rc={inner.returncode}", file=sys.stderr)
        return 1
    if not remote_tmp:
        print("[win] FAIL: harness did not report a unique RESULT_DIR", file=sys.stderr)
        return 1

    try:
        validate_smoke_bundle(
            RESULTS_DIR,
            step_names=("step_06_g16_opt", "step_07_g16_sp_readchk"),
            checkpoint=True,
        )
        cleanup_remote(remote_tmp)
    except (OSError, SmokeValidationError) as exc:
        print(f"[win] SMOKE FAIL: {exc}", file=sys.stderr)
        return 1
    print("[win] SMOKE PASS: strict checkpoint workflow", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
