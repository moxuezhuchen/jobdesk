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
