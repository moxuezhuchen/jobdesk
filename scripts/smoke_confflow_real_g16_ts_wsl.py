#!/usr/bin/env python3
"""Real-g16 ConFlow TS smoke (Phase 9H-1).

Stamps a bash harness into WSL via base64, runs it, and pulls the artifacts
back. Targets real Gaussian 16 (no mock), HCN -> HNC transition-state search
at b3lyp/6-31g(d). The load-bearing assertion in the coupled pytest suite
is that the .log contains exactly one imaginary frequency (the TS marker).
"""
from __future__ import annotations

import base64
import pathlib
import shutil
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "tmp60f7j8ix" / "phase9h_ts"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
DEST_WSL = "/tmp/phase9h1_inner.sh"

INNER_HARNESS = """#!/usr/bin/env bash
set -euo pipefail

export g16root=/opt
export GAUSS_EXEDIR=/opt/g16/bsd:/opt/g16
export PATH=/opt/g16/bsd:/opt/g16:$PATH
export GAUSS_SCRDIR=/opt/g16/scratch
# Skip source /opt/g16/bsd/g16.profile -- it triggers 'set -u' PERLLIB unbound
# errors when sourced under `set -euo pipefail`.  We already exported the env.

TMP="/tmp/confflow_phase9h1_${BASHPID}"
echo "[smoke] staging in $TMP"
mkdir -p "$TMP"
cd "$TMP"

# HCN -- HNC isomerization TS starting geometry.
#
# The HCN -> HNC transition state at b3lyp/6-31g(d) is *bent* (not
# colinear): the migrating H sits off the C-N axis, with R(H-C) ~ 1.2 A,
# R(C-N) ~ 1.2 A, and the H-C-N angle near 70 deg.  A colinear HCN
# starting guess (H at one end, C in the middle, N at the other end) is
# a *minimum* in the redundant internal coordinates, so the TS optimizer
# walks H *away* from C in the wrong direction for all 20 default
# maxcycles and never reaches the saddle.  Starting with a bent
# H-C-N geometry breaks the linear symmetry and lets the optimizer find
# the true saddle in <=10 steps.
#
# Coordinate system: H at origin, C at +x (1.2 A from H), N off-axis in
# (x,y) with R(C-N) = 1.2 A and H-C-N = 70 deg.  In Cartesian: N is at
# (1.2 + 1.2*cos(70), 1.2*sin(70), 0) = (1.610, 1.128, 0).
cat > hcn.xyz <<'XYZE'
3

H   0.000000   0.000000   0.000000
C   1.200000   0.000000   0.000000
N   1.610414   1.127446   0.000000
XYZE

cat > confflow.yaml <<'YCONF'
global:
  gaussian_path: /opt/g16/g16
  cores_per_task: 1
  total_memory: "1GB"
  max_parallel_jobs: 1
  charge: 0
  multiplicity: 1
steps:
  - name: g16_ts
    type: calc
    params:
      iprog: g16
      itask: ts
      keyword: "opt=(ts,calcfc,noeigen,maxcycles=50) b3lyp/6-31g(d) freq"
      ts_bond_atoms: [1, 3]
      ts_rescue_scan: false
      cores_per_task: 1
      total_memory: "1GB"
      max_parallel_jobs: 1
YCONF

echo "[smoke] g16 location:"
which g16
ls -la /opt/g16/g16 /opt/g16/l1.exe 2>&1 | head -5

echo
echo "[smoke] starting confflow (real g16, HCN TS)"
confflow hcn.xyz -c confflow.yaml -w hcn_confflow_work --resume --verbose 2>&1 | tee confflow.out
CONFFLOW_RC=${PIPESTATUS[0]}
echo "[smoke] confflow rc=$CONFFLOW_RC"

echo
echo "[smoke] result tree:"
ls -laR hcn_confflow_work 2>&1 | sed 's/^/    /'

echo
echo "[smoke] run_summary.json:"
cat hcn_confflow_work/run_summary.json 2>&1 || echo "(missing)"
echo
echo "[smoke] workflow_stats.json:"
cat hcn_confflow_work/workflow_stats.json 2>&1 || echo "(missing)"

echo
echo "[smoke] g16 backups:"
ls -la hcn_confflow_work/g16_ts/backups/ 2>&1 || echo "(missing backups)"

echo
echo "[smoke] g16 .log key lines (TS markers):"
LOGFILE=hcn_confflow_work/g16_ts/backups/A000001.log
if [ -f "$LOGFILE" ]; then
    grep -E "SCF Done|Normal termination|Error termination|Optimization completed|Stationary point found|Number of Imaginary Frequencies|Negative curvature" "$LOGFILE" 2>&1 | head -40
else
    echo "no $LOGFILE"
fi

echo
echo "[smoke] RESULT_DIR=$TMP"
"""


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def stamp_remote() -> None:
    b64_harness = _b64(INNER_HARNESS)
    size = len(INNER_HARNESS)
    wsl_helper = "/tmp/phase9h1_deployer.py"
    deployer_content = (
        "import base64, os, pathlib\n"
        f"data = base64.b64decode('{b64_harness}').decode('utf-8')\n"
        f"pathlib.Path('{DEST_WSL}').write_text(data, encoding='utf-8', newline='\\n')\n"
        f"os.chmod('{DEST_WSL}', 0o755)\n"
        f"print('helper wrote', '{DEST_WSL}', '({size} bytes)')\n"
    )
    b64_deployer = _b64(deployer_content)
    proc = subprocess.run(
        ["wsl", "bash", "-c",
         "python3 -u -c \"import sys,base64,os,pathlib;"
         "data=base64.b64decode(sys.stdin.read().strip()).decode('utf-8');"
         f"pathlib.Path('{wsl_helper}').write_text(data,encoding='utf-8',newline='\\n');"
         f"os.chmod('{wsl_helper}',0o755);print('helper written')\""],
        input=b64_deployer,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)
    print(proc.stdout, end="")
    result = subprocess.run(
        ["wsl", "bash", "-c", f"python3 {wsl_helper}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    print(result.stdout, end="")


def run_inner() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["wsl", "bash", DEST_WSL],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=900,
    )


def parse_result_dir(output: str) -> str | None:
    for line in output.splitlines():
        if line.startswith("[smoke] RESULT_DIR="):
            return line.split("=", 1)[1].strip()
    return None


def pull_artifacts(remote_tmp: str, target: pathlib.Path) -> None:
    pull_dir = "/tmp/confflow_phase9h1_pull"
    subprocess.run(["wsl", "bash", "-c", f"rm -rf -- '{pull_dir}' || true"], check=False)
    subprocess.run(
        ["wsl", "bash", "-c",
         f"mkdir -p -- '{pull_dir}' && cp -r -- '{remote_tmp}/hcn_confflow_work' '{pull_dir}/'"],
        check=True,
    )
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
    target = RESULTS_DIR / "hcn_confflow_work"
    print("[win] stamping remote harness", flush=True)
    stamp_remote()

    print("[win] running confflow (real g16, HCN TS)...", flush=True)
    inner = run_inner()
    if inner.stdout:
        print(inner.stdout, end="")
    if inner.stderr:
        print(inner.stderr, end="", file=sys.stderr)
    if inner.returncode != 0:
        print(f"[win] FAIL inner exit={inner.returncode}")
        return inner.returncode

    remote_tmp = parse_result_dir(inner.stdout)
    if not remote_tmp:
        print("[win] RESULT_DIR not found in output", file=sys.stderr)
        return 1

    print(f"[win] pulling artifacts from {remote_tmp}", flush=True)
    pull_artifacts(remote_tmp, target)
    print(f"[win] artifacts staged at {target}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
