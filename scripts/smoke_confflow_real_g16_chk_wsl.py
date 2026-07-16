#!/usr/bin/env python3
"""Real-g16 ConFlow two-step chk-passing smoke (Phase 9H-2).

Stamps a bash harness into WSL via base64, runs it, and pulls the artifacts
back. Targets real Gaussian 16 (no mock), with a two-step workflow:

  step_06_g16_opt          : iprog g16, itask opt,  opt(nomicro) b3lyp/6-31g(d)
                              gaussian_write_chk: true
  step_07_g16_sp_readchk   : iprog g16, itask sp,   sp guess=read geom=allcheck
                              chk_from_step: step_06_g16_opt

The second step's input dir must contain ``A000001.old.chk`` (copied from
step_06's ``backups/A000001.chk`` by confflow) and the second step's
``.gjf`` must contain ``%OldChk=A000001.old.chk``.  The smoke prints both.

Pattern mirrors ``scripts/smoke_confflow_real_g16_wsl.py`` (Phase 9G).
"""
from __future__ import annotations

import base64
import pathlib
import shutil
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "tmp60f7j8ix" / "phase9h2_chk"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
DEST_WSL = "/tmp/phase9h2_chk_inner.sh"

INNER_HARNESS = """#!/usr/bin/env bash
set -euo pipefail

export g16root=/opt
export GAUSS_EXEDIR=/opt/g16/bsd:/opt/g16
export PATH=/opt/gauopen:/opt/g16/bsd:/opt/g16:$PATH
export GAUSS_SCRDIR=/opt/g16/scratch
# Skip source /opt/g16/bsd/g16.profile -- it triggers 'set -u' PERLLIB unbound
# errors when sourced under `set -euo pipefail`.  We already exported the env.

TMP="/tmp/confflow_phase9h2_${BASHPID}"
echo "[smoke] staging in $TMP"
mkdir -p "$TMP"
cd "$TMP"

cat > methane.xyz <<'XYZE'
5

C   0.000000   0.000000   0.000000
H   0.629118   0.629118   0.629118
H  -0.629118  -0.629118   0.629118
H  -0.629118   0.629118  -0.629118
H   0.629118  -0.629118  -0.629118
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
YCONF

echo "[smoke] g16 location:"
which g16
ls -la /opt/g16/g16 /opt/g16/l1.exe 2>&1 | head -5

echo
echo "[smoke] starting confflow (real g16, two-step chk-passing)"
confflow methane.xyz -c confflow.yaml -w methane_confflow_work --resume --verbose 2>&1 | tee confflow.out
CONFFLOW_RC=${PIPESTATUS[0]}
echo "[smoke] confflow rc=$CONFFLOW_RC"

echo
echo "[smoke] result tree:"
ls -laR methane_confflow_work 2>&1 | sed 's/^/    /'

echo
echo "[smoke] run_summary.json:"
cat methane_confflow_work/run_summary.json 2>&1 || echo "(missing)"
echo
echo "[smoke] workflow_stats.json:"
cat methane_confflow_work/workflow_stats.json 2>&1 || echo "(missing)"

echo
echo "[smoke] step_06 g16 .log key lines:"
LOG06=methane_confflow_work/step_06_g16_opt/backups/A000001.log
if [ -f "$LOG06" ]; then
    grep -E "SCF Done|Normal termination|Error termination|Optimization completed|Stationary point" "$LOG06" 2>&1 | head -10
else
    echo "no $LOG06"
fi

echo
echo "[smoke] step_07 g16 .log key lines:"
LOG07=methane_confflow_work/step_07_g16_sp_readchk/backups/A000001.log
if [ -f "$LOG07" ]; then
    grep -E "SCF Done|Normal termination|Error termination|Read from chkfile|OldChk" "$LOG07" 2>&1 | head -15
else
    echo "no $LOG07"
fi

echo
echo "[smoke] step_07 .gjf (expect %OldChk line):"
GJF07=methane_confflow_work/step_07_g16_sp_readchk/backups/A000001.gjf
if [ -f "$GJF07" ]; then
    cat "$GJF07"
else
    echo "no $GJF07"
fi

echo
echo "[smoke] step_07 input dir listing (expect A000001.old.chk):"
ls -la methane_confflow_work/step_07_g16_sp_readchk/ 2>&1 || echo "(no step_07 dir)"
echo
echo "[smoke] step_07 backups dir (chk / log size sanity):"
ls -la methane_confflow_work/step_07_g16_sp_readchk/backups/ 2>&1 || echo "(no step_07 backups)"

echo
echo "[smoke] step_06 chk file (must exist for chk_from_step to copy):"
ls -la methane_confflow_work/step_06_g16_opt/backups/A000001.chk 2>&1 || echo "(no step_06 chk)"

echo
echo "[smoke] RESULT_DIR=$TMP"
"""


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def stamp_remote() -> None:
    b64_harness = _b64(INNER_HARNESS)
    size = len(INNER_HARNESS)
    wsl_helper = "/tmp/phase9h2_chk_deployer.py"
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
        check=False, timeout=600,
    )


def parse_result_dir(output: str) -> str | None:
    for line in output.splitlines():
        if line.startswith("[smoke] RESULT_DIR="):
            return line.split("=", 1)[1].strip()
    return None


def pull_artifacts(remote_tmp: str, target: pathlib.Path) -> None:
    pull_dir = "/tmp/confflow_phase9h2_pull"
    subprocess.run(["wsl", "bash", "-c", f"rm -rf -- '{pull_dir}' || true"], check=False)
    subprocess.run(
        ["wsl", "bash", "-c",
         f"mkdir -p -- '{pull_dir}' && cp -r -- '{remote_tmp}/methane_confflow_work' '{pull_dir}/'"],
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
    target = RESULTS_DIR
    print("[win] stamping remote harness", flush=True)
    stamp_remote()

    print("[win] running confflow (real g16, two-step chk-passing, ~30s)...", flush=True)
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
    print(f"[win] artifacts staged at {target / 'methane_confflow_work'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
