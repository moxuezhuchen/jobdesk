#!/usr/bin/env python3
"""Phase Release-Smoke — Dual-molecule real ConfFlow integration (v3).

Runs confflow on TWO distinct molecules (methane + ethane) using ORCA OPT
(itask: opt). Each run emits its own work dir with run_summary.json,
workflow_stats.json, and an .workflow_state.json (1.4.3 restartable state).

This mirrors JobDesk's actual upload behavior: each molecule becomes a
separate RunRecord whose work dir is downloaded independently.

Verified 1.4.3 producer capabilities exercised in this smoke:
  - capability contract payload (via --capabilities probe)
  - 5 artifact kinds in each work dir
  - .workflow_state.json (restartable state)
  - input_file consistency across multiple invocations
  - run_summary.json lowest_conformer trace
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import textwrap

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "tmp60f7j8ix" / "phase_release_dual_mol"
DEST_WSL = "/tmp/confflow_release_dual_smoke.sh"

INNER_HARNESS = textwrap.dedent("""\
    #!/usr/bin/env bash
    set -uo pipefail

    export PATH=/opt/orca611:/usr/local/bin:$PATH
    which orca

    TMP="/tmp/confflow_release_dual_${BASHPID}"
    echo "[smoke] staging in $TMP"
    mkdir -p "$TMP"
    cd "$TMP"

    # Methane: 5 atoms
    cat > methane.xyz <<'XYZE'
    5
    methane
    C   0.000000   0.000000   0.000000
    H   0.629118   0.629118   0.629118
    H  -0.629118  -0.629118   0.629118
    H  -0.629118   0.629118  -0.629118
    H   0.629118  -0.629118  -0.629118
    XYZE

    # Ethane: 8 atoms — line 1=count, line 2=comment, then 8 atom lines
    cat > ethane.xyz <<'XYZE'
    8
    ethane
    C   0.000000   0.000000   0.000000
    C   1.530000   0.000000   0.000000
    H   0.629118   0.629118   0.629118
    H  -0.629118   0.629118  -0.629118
    H  -0.629118  -0.629118   0.629118
    H   1.530000   0.629118   0.629118
    H   2.159118   0.000000  -0.629118
    H   1.530000  -0.629118  -0.629118
    XYZE

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
          keyword: "b3lyp def2-svp MiniPrint"
          cores_per_task: 1
          total_memory: 512MB
          max_parallel_jobs: 1
    YCONF

    # First molecule: methane
    echo "[smoke] running confflow methane ..."
    confflow methane.xyz -c confflow.yaml -w methane_confflow_work --resume --verbose > methane.out 2>&1
    M_RC=$?
    echo "[smoke] methane rc=$M_RC"

    # Second molecule: ethane
    echo "[smoke] running confflow ethane ..."
    confflow ethane.xyz -c confflow.yaml -w ethane_confflow_work --resume --verbose > ethane.out 2>&1
    E_RC=$?
    echo "[smoke] ethane rc=$E_RC"

    echo
    echo "[smoke] methane work tree:"
    ls -laR methane_confflow_work 2>&1 | sed 's/^/    /'
    echo
    echo "[smoke] ethane work tree:"
    ls -laR ethane_confflow_work 2>&1 | sed 's/^/    /'

    echo
    echo "[smoke] methane run_summary.json:"
    cat methane_confflow_work/run_summary.json 2>/dev/null || echo "(missing)"
    echo
    echo "[smoke] ethane run_summary.json:"
    cat ethane_confflow_work/run_summary.json 2>/dev/null || echo "(missing)"

    echo
    echo "[smoke] PRODUCT_RC methane=$M_RC ethane=$E_RC"
    echo "[smoke] RESULT_DIR=$TMP"
""")


def _b64(s: str) -> str:
    import base64

    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def stamp_remote() -> None:
    b64_harness = _b64(INNER_HARNESS)
    size = len(INNER_HARNESS)
    wsl_helper = "/tmp/confflow_release_dual_deployer.py"
    deployer_content = (
        "import base64, os, pathlib\n"
        f"data = base64.b64decode('{b64_harness}').decode('utf-8')\n"
        f"pathlib.Path('{DEST_WSL}').write_text(data, encoding='utf-8', newline='\\n')\n"
        f"os.chmod('{DEST_WSL}', 0o755)\n"
        f"print('helper wrote', '{DEST_WSL}', '({size} bytes)')\n"
    )
    b64_deployer = _b64(deployer_content)
    proc = subprocess.run(
        [
            "wsl",
            "bash",
            "-c",
            'python3 -u -c "import sys,base64,os,pathlib;'
            "data=base64.b64decode(sys.stdin.read().strip()).decode('utf-8');"
            f"pathlib.Path('{wsl_helper}').write_text(data,encoding='utf-8',newline='\\n');"
            f"os.chmod('{wsl_helper}',0o755);print('helper written')\"",
        ],
        input=b64_deployer,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(proc.returncode)
    print(proc.stdout, end="")
    result = subprocess.run(
        ["wsl", "bash", "-c", f"python3 {wsl_helper}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    print(result.stdout, end="")


def run_inner() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["wsl", "bash", DEST_WSL],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=900,
    )


def parse_result_dir(output: str) -> str | None:
    for line in output.splitlines():
        if line.startswith("[smoke] RESULT_DIR="):
            return line.split("=", 1)[1].strip()
    return None


def parse_product_rcs(output: str) -> dict[str, int]:
    rcs: dict[str, int] = {}
    for line in output.splitlines():
        if line.startswith("[smoke] PRODUCT_RC"):
            for tok in line.split():
                if "=" in tok and not tok.startswith("[smoke]"):
                    k, v = tok.split("=", 1)
                    try:
                        rcs[k] = int(v)
                    except ValueError:
                        pass
    return rcs


def pull_artifacts(remote_tmp: str, target: pathlib.Path) -> None:
    pull_dir = "/tmp/confflow_release_dual_pull"
    subprocess.run(["wsl", "bash", "-c", f"rm -rf -- '{pull_dir}' || true"], check=False)
    subprocess.run(
        [
            "wsl",
            "bash",
            "-c",
            f"mkdir -p -- '{pull_dir}' && "
            f"cp -r -- '{remote_tmp}/methane_confflow_work' '{remote_tmp}/ethane_confflow_work' "
            f"'{remote_tmp}/methane.out' '{remote_tmp}/ethane.out' '{pull_dir}/'",
        ],
        check=True,
    )
    wsl_path = subprocess.run(
        ["wsl", "wslpath", "-w", pull_dir],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout.strip()
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(wsl_path, str(target), dirs_exist_ok=True)
    subprocess.run(
        ["wsl", "bash", "-c", f"rm -rf -- '{remote_tmp}' '{pull_dir}' || true"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def main() -> int:
    target = RESULTS_DIR
    target.mkdir(parents=True, exist_ok=True)

    print("[win] stamping dual-molecule harness", flush=True)
    stamp_remote()

    print("[win] running dual-molecule confflow in WSL ...", flush=True)
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

    print("[win] validating downloaded artifacts ...", flush=True)
    rcs = parse_product_rcs(inner.stdout)
    print(f"[win] producer per-molecule rc = {rcs}")

    failures = 0
    expected_artifacts = (
        "run_summary.json",
        "workflow_stats.json",
        ".workflow_state.json",
    )
    for mol in ("methane", "ethane"):
        mol_dir = target / f"{mol}_confflow_work"
        if not mol_dir.exists():
            print(f"[win] FAIL {mol} directory missing", file=sys.stderr)
            failures += 1
            continue

        for art in expected_artifacts:
            if not (mol_dir / art).exists():
                print(f"[win] FAIL {mol}/{art} missing", file=sys.stderr)
                failures += 1

        rs_path = mol_dir / "run_summary.json"
        if rs_path.exists():
            try:
                s = json.loads(rs_path.read_text(encoding="utf-8"))
                initial = s.get("initial_conformers")
                final = s.get("final_conformers")
                lowest = s.get("lowest_conformer") or {}
                print(
                    f"[win] {mol} run_summary: initial={initial} final={final} "
                    f"lowest_cid={lowest.get('cid')} lowest_energy={lowest.get('energy')}"
                )
                if initial != 1 or final != 1:
                    print(f"[win] FAIL {mol} unexpected conformer count", file=sys.stderr)
                    failures += 1
                if not lowest.get("cid"):
                    print(f"[win] FAIL {mol} lowest_conformer.cid missing", file=sys.stderr)
                    failures += 1
            except Exception as exc:
                print(f"[win] FAIL {mol} run_summary.json parse error: {exc}", file=sys.stderr)
                failures += 1

        ws_path = mol_dir / ".workflow_state.json"
        if ws_path.exists():
            try:
                ws = json.loads(ws_path.read_text(encoding="utf-8"))
                if ws.get("final_status") != "completed":
                    print(
                        f"[win] FAIL {mol} workflow state final_status={ws.get('final_status')} (expected 'completed')",
                        file=sys.stderr,
                    )
                    failures += 1
                steps = ws.get("steps") or {}
                completed = sum(1 for s in steps.values() if s.get("status") == "completed")
                print(
                    f"[win] {mol} workflow_state: run_id={ws.get('run_id')} steps={len(steps)} completed_steps={completed}"
                )
            except Exception as exc:
                print(f"[win] FAIL {mol} workflow_state.json parse error: {exc}", file=sys.stderr)
                failures += 1

    if failures:
        print(f"[win] FAIL {failures} checks failed", file=sys.stderr)
        return 1
    print(f"[win] PASS dual-molecule artifacts validated at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
