#!/usr/bin/env python3
"""End-to-end ConFlow smoke for Phase 6.

Stamps the remote bash harness (phase6_inner.sh) into WSL via a temp helper
script, runs it, and pulls the artifacts back to Windows.

Uses real ORCA SP on methane (fastest: ~1-2 s wall-clock).
"""
from __future__ import annotations

import argparse
import base64
import pathlib
import shutil
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
INNER_SH_WIN = REPO_ROOT / "scripts" / "phase6_inner.sh"
INNER_SH_WSL = "/tmp/phase6_inner.sh"

# Bash harness — always overwritten so edits take effect immediately.
INNER_HARNESS = """\
#!/usr/bin/env bash
set -euo pipefail

TMP="/tmp/confflow_phase6_${BASHPID}"
echo "[smoke] staging in $TMP"
mkdir -p "$TMP"
cd "$TMP"

# 1. Methane XYZ (5 atoms — ORCA SP completes in <1 s)
cat > methane.xyz <<'XYZE'
5
methane
C   0.000000   0.000000   0.000000
H   0.629118   0.629118   0.629118
H  -0.629118  -0.629118   0.629118
H  -0.629118   0.629118  -0.629118
H   0.629118  -0.629118  -0.629118
XYZE

# 2. ConFlow YAML targeting real ORCA (geometry optimization — fast for methane).
cat > confflow.yaml <<'YCONF'
global:
  orca_path: /opt/orca611/orca
  cores_per_task: 1
  total_memory: 512MB
  max_parallel_jobs: 1
  charge: 0
  multiplicity: 1
steps:
  - name: quick_opt
    type: calc
    params:
      iprog: orca
      itask: opt
      keyword: "b3lyp def2-svp Opt MiniPrint"
      cores_per_task: 1
      total_memory: 512MB
      max_parallel_jobs: 1
YCONF

# 3. Run ConFlow
echo "[smoke] starting confflow in $TMP"
confflow methane.xyz -c confflow.yaml -w methane_confflow_work --resume --verbose 2>&1 | tee confflow.out
CONFFLOW_RC=${PIPESTATUS[0]}
echo "[smoke] confflow rc=$CONFFLOW_RC"

echo "[smoke] result tree:"
ls -laR methane_confflow_work | sed 's/^/    /'
echo "[smoke] RESULT_DIR=$TMP"
"""


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def stamp_remote() -> None:
    INNER_SH_WIN.write_text(INNER_HARNESS, encoding="utf-8")
    payload = INNER_SH_WIN.read_text(encoding="utf-8")
    b64_harness = _b64(payload)
    size = len(payload)
    wsl_dest = INNER_SH_WSL

    # Write the Python deployer into a WSL temp file via base64.
    # Using a WSL /tmp path so WSL can actually execute it.
    wsl_helper = "/tmp/phase6_deployer.py"
    deployer_content = (
        f"import base64, os, stat, pathlib\n"
        f"data = base64.b64decode('{b64_harness}').decode('utf-8')\n"
        f"dest = '{wsl_dest}'\n"
        f"pathlib.Path(dest).write_text(data, encoding='utf-8', newline='\\n')\n"
        f"os.chmod(dest, 0o755)\n"
        f"print('stamped', dest, '({size} bytes)')\n"
    )
    b64_deployer = _b64(deployer_content)
    # Stream deployer to WSL /tmp via stdin.
    proc = subprocess.run(
        ["wsl", "bash", "-c",
         f"python3 -u -c \"import sys,base64,os,stat,pathlib;data=base64.b64decode(sys.stdin.read().strip()).decode('utf-8');pathlib.Path('{wsl_helper}').write_text(data,encoding='utf-8',newline='\\n');os.chmod('{wsl_helper}',0o755);print('helper written')\""],
        input=b64_deployer,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)
    print(proc.stdout, end="")
    # Run the deployer to stamp the harness.
    result = subprocess.run(
        ["wsl", "bash", "-c", f"python3 {wsl_helper}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    print(result.stdout, end="")


def run_inner() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["wsl", "bash", INNER_SH_WSL],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=120,
    )


def parse_result_dir(output: str) -> str | None:
    for line in output.splitlines():
        if line.startswith("[smoke] RESULT_DIR="):
            return line.split("=", 1)[1].strip()
    return None


def pull_artifacts(remote_tmp: str, target: pathlib.Path) -> None:
    pull_dir = "/tmp/confflow_phase6_pull"
    subprocess.run(["wsl", "bash", "-c", f"rm -rf -- '{pull_dir}' || true"], check=False)
    subprocess.run(["wsl", "bash", "-c", f"mkdir -p -- '{pull_dir}' && cp -r -- '{remote_tmp}/methane_confflow_work' '{pull_dir}/'"], check=True)
    wsl_path = subprocess.run(
        ["wsl", "wslpath", "-w", pull_dir],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout.strip()
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(wsl_path, str(target), dirs_exist_ok=True)
    subprocess.run(
        ["wsl", "bash", "-c", f"rm -rf -- '{remote_tmp}' '{pull_dir}' || true"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-dir",
        default=str(REPO_ROOT / "tmp60f7j8ix" / "phase6_smoke"),
    )
    args = parser.parse_args()
    results_dir = pathlib.Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    target = results_dir

    print("[windows] stamping remote harness", flush=True)
    stamp_remote()

    print("[windows] running confflow in WSL (ORCA SP on methane, ~10s wall)...", flush=True)
    inner = run_inner()
    if inner.stdout:
        print(inner.stdout, end="")
    if inner.stderr:
        print(inner.stderr, end="", file=sys.stderr)
    if inner.returncode != 0:
        return inner.returncode

    remote_tmp = parse_result_dir(inner.stdout)
    if not remote_tmp:
        print("[windows] RESULT_DIR not found in output", file=sys.stderr)
        return 1

    print(f"[windows] pulling artifacts from {remote_tmp}", flush=True)
    pull_artifacts(remote_tmp, target)
    print(f"[windows] artifacts staged at {target / 'methane_confflow_work'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
