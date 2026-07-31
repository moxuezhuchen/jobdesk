"""Shared strict validation for the non-computing ConfFlow g16 smokes.

The smoke harnesses execute on Windows and stage a bash script in WSL.  This
module owns the decision boundary after that harness returns: a zero process
exit code is necessary, but never sufficient, for a smoke to pass.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from jobdesk_app.core.confflow_contract import (  # noqa: E402
    CAPABILITY_SCHEMA_VERSION,
    OUTPUT_MANIFEST_FILE,
    REFERENCE_BUILD_COMMIT,
    REFERENCE_VERSION,
    REFERENCE_WHEEL_FILENAME,
    REFERENCE_WHEEL_SHA256,
    RUN_SUMMARY_FILE,
    RUN_SUMMARY_SCHEMA,
    WORKFLOW_STATE_FILE,
    WORKFLOW_STATE_SCHEMA,
    WORKFLOW_STATS_FILE,
    WORKFLOW_STATS_SCHEMA,
)
from jobdesk_app.core.confflow_output_manifest import (  # noqa: E402
    OutputManifestError,
    parse_output_manifest,
)
from jobdesk_app.core.confflow_preflight import (  # noqa: E402
    parse_confflow_capabilities,
    validate_confflow_production_capability,
)

WSL_DISTRO = "Ubuntu-24.04"
WIN_WSL = "wsl"
PROD_VENV = "/opt/confflow-1.4.6-prod-venv"
CONFFLOW_EXE = f"{PROD_VENV}/bin/confflow"
CONFFLOW_PYTHON = f"{PROD_VENV}/bin/python3.12"
G16_PATH = "/opt/g16/g16"
L1_PATH = "/opt/g16/l1.exe"
G16_PROFILE = "/opt/g16/bsd/g16.profile"
_REMOTE_TMP_PREFIX = "/tmp/jobdesk-confflow-g16."
_REMOTE_TMP_RE = re.compile(r"^/tmp/jobdesk-confflow-g16\.[A-Za-z0-9]{6}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class SmokeValidationError(ValueError):
    """A strict smoke assertion failed."""


def validate_process_returncode(returncode: int, *, stage: str) -> None:
    """Reject every non-zero process result, including the historical rc=2."""

    if returncode != 0:
        raise SmokeValidationError(f"{stage} returned non-zero exit code: {returncode}")


def validate_preflight_lines(raw: str) -> tuple[str, str, str, str]:
    """Validate the exact four-line g16 preflight contract."""

    lines = raw.splitlines()
    expected = ("2", "0", "0", G16_PROFILE)
    if tuple(lines) != expected:
        raise SmokeValidationError(
            "g16 preflight mismatch: "
            f"expected {list(expected)!r}, got {lines!r}"
        )
    return expected


def validate_capability_payload(raw: str) -> dict[str, object]:
    """Validate the exact clean Gate B 1.4.6 capability identity."""

    try:
        capabilities = parse_confflow_capabilities(raw)
        return validate_confflow_production_capability(
            capabilities,
            expected_executable=CONFFLOW_EXE,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SmokeValidationError(f"invalid ConfFlow production capability: {exc}") from exc


def validate_capability_file(path: Path) -> dict[str, object]:
    """Load and validate a capability JSON file saved by the harness."""

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SmokeValidationError(f"capability output is missing: {path}") from exc
    return validate_capability_payload(raw)


def parse_result_dir(output: str) -> str | None:
    """Return the harness-owned unique /tmp directory, if it was printed."""

    matches = [
        line.split("=", 1)[1].strip()
        for line in output.splitlines()
        if line.startswith("[smoke] RESULT_DIR=")
    ]
    if len(matches) != 1 or _REMOTE_TMP_RE.fullmatch(matches[0]) is None:
        return None
    return matches[0]


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SmokeValidationError(f"{label} is missing or invalid: {path}") from exc
    if not isinstance(raw, dict):
        raise SmokeValidationError(f"{label} must be a JSON object: {path}")
    return raw


def _require_schema(raw: dict[str, Any], *, schema: str, label: str) -> None:
    if raw.get("content_schema") != schema:
        raise SmokeValidationError(
            f"{label} schema mismatch: expected {schema!r}, got {raw.get('content_schema')!r}"
        )


def _require_completed_steps(
    raw_steps: object,
    *,
    expected_names: tuple[str, ...],
    label: str,
) -> None:
    if not isinstance(raw_steps, (dict, list)):
        raise SmokeValidationError(f"{label} steps are missing or malformed")
    if isinstance(raw_steps, dict):
        rows = list(raw_steps.values())
    else:
        rows = raw_steps
    by_name: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise SmokeValidationError(f"{label} contains a malformed step")
        by_name[row["name"]] = row
        if row.get("status") != "completed":
            raise SmokeValidationError(
                f"{label} step {row['name']!r} is not completed: {row.get('status')!r}"
            )
    missing = [name for name in expected_names if name not in by_name]
    if missing:
        raise SmokeValidationError(f"{label} is missing completed steps: {missing!r}")


def _safe_relative_artifact(path_value: object, *, label: str) -> Path:
    if not isinstance(path_value, str) or not path_value or "\\" in path_value:
        raise SmokeValidationError(f"{label} output path is not a safe relative path")
    path = Path(path_value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SmokeValidationError(f"{label} output path is not a safe relative path: {path_value!r}")
    return path


def _validate_step_manifest(work_dir: Path, step_name: str) -> None:
    step_dir = work_dir / step_name
    manifest = _load_object(step_dir / "manifest.json", label=f"{step_name} manifest")
    if manifest.get("schema_version") != 1:
        raise SmokeValidationError(f"{step_name} manifest schema_version is not 1")
    if manifest.get("step_name") != step_name or manifest.get("step_type") != "calc":
        raise SmokeValidationError(f"{step_name} manifest identifies the wrong step")
    if manifest.get("status") != "completed":
        raise SmokeValidationError(f"{step_name} manifest is not completed")
    if manifest.get("error") is not None or manifest.get("failed") is not None:
        raise SmokeValidationError(f"{step_name} manifest contains a failure terminal")
    if manifest.get("failed_count") != 0 or not isinstance(manifest.get("succeeded"), int):
        raise SmokeValidationError(f"{step_name} manifest has failed or missing task counts")
    output = _safe_relative_artifact(manifest.get("output"), label=f"{step_name} manifest")
    output_path = (step_dir / output).resolve()
    if not output_path.is_relative_to(work_dir.resolve()) or not output_path.is_file():
        raise SmokeValidationError(f"{step_name} manifest output does not exist inside the workflow")


def _validate_output_manifest(work_dir: Path) -> None:
    try:
        manifest = parse_output_manifest(
            _load_object(work_dir / OUTPUT_MANIFEST_FILE, label="output manifest"),
            work_dir=work_dir,
        )
    except OutputManifestError as exc:
        raise SmokeValidationError(f"output manifest is invalid: {exc}") from exc
    for relative in manifest.paths:
        if not (work_dir / Path(relative)).is_file():
            raise SmokeValidationError(f"output manifest points to a missing file: {relative}")


def validate_workflow_artifacts(
    work_dir: Path,
    *,
    step_names: Iterable[str],
    checkpoint: bool = False,
) -> None:
    """Validate terminal workflow state, all four artifacts, and step outputs."""

    expected_names = tuple(step_names)
    if not expected_names:
        raise SmokeValidationError("at least one workflow step is required")

    summary = _load_object(work_dir / RUN_SUMMARY_FILE, label="run summary")
    stats = _load_object(work_dir / WORKFLOW_STATS_FILE, label="workflow stats")
    state = _load_object(work_dir / WORKFLOW_STATE_FILE, label="workflow state")
    _require_schema(summary, schema=RUN_SUMMARY_SCHEMA, label="run summary")
    _require_schema(stats, schema=WORKFLOW_STATS_SCHEMA, label="workflow stats")
    _require_schema(state, schema=WORKFLOW_STATE_SCHEMA, label="workflow state")
    if state.get("final_status") != "completed":
        raise SmokeValidationError(f"workflow final_status is not completed: {state.get('final_status')!r}")
    _require_completed_steps(state.get("steps"), expected_names=expected_names, label="workflow state")
    _require_completed_steps(summary.get("steps"), expected_names=expected_names, label="run summary")
    _require_completed_steps(stats.get("steps"), expected_names=expected_names, label="workflow stats")
    counts = summary.get("step_status_counts")
    if not isinstance(counts, dict) or counts.get("completed") != len(expected_names):
        raise SmokeValidationError(f"run summary completed step count is not {len(expected_names)}")
    _validate_output_manifest(work_dir)
    for step_name in expected_names:
        _validate_step_manifest(work_dir, step_name)

    if checkpoint:
        _validate_checkpoint_artifacts(work_dir)


def _validate_log(path: Path, *, label: str) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SmokeValidationError(f"{label} Gaussian log is missing: {path}") from exc
    if "Normal termination of Gaussian 16" not in text:
        raise SmokeValidationError(f"{label} Gaussian log lacks normal termination: {path}")
    if "Error termination" in text:
        raise SmokeValidationError(f"{label} Gaussian log contains error termination: {path}")
    return text


def validate_g16_artifacts(work_dir: Path, *, checkpoint: bool = False) -> None:
    """Validate independent Gaussian logs and, for checkpoint smoke, handoff proof."""

    if checkpoint:
        _validate_checkpoint_artifacts(work_dir)
    else:
        _validate_log(work_dir / "g16_opt" / "backups" / "A000001.log", label="g16_opt")


def _validate_checkpoint_artifacts(work_dir: Path) -> None:
    log06 = work_dir / "step_06_g16_opt" / "backups" / "A000001.log"
    log07 = work_dir / "step_07_g16_sp_readchk" / "backups" / "A000001.log"
    text06 = _validate_log(log06, label="step_06_g16_opt")
    text07 = _validate_log(log07, label="step_07_g16_sp_readchk")
    if "Optimization completed" not in text06:
        raise SmokeValidationError("step_06 Gaussian log lacks optimization completion")
    for marker in (
        '%OldChk=A000001.old.chk',
        'Copying data from "A000001.old.chk"',
        "Structure from the checkpoint file",
        "Initial guess from the checkpoint file",
    ):
        if marker not in text07:
            raise SmokeValidationError(f"step_07 Gaussian log lacks checkpoint evidence: {marker}")
    chk06 = work_dir / "step_06_g16_opt" / "backups" / "A000001.chk"
    old_chk = work_dir / "step_07_g16_readchk" / "backups" / "A000001.old.chk"
    if not old_chk.exists():
        old_chk = work_dir / "step_07_g16_sp_readchk" / "backups" / "A000001.old.chk"
    if not chk06.is_file() or chk06.stat().st_size <= 0:
        raise SmokeValidationError("step_06 checkpoint is missing or empty")
    if not old_chk.is_file() or old_chk.stat().st_size <= 0:
        raise SmokeValidationError("step_07 copied checkpoint is missing or empty")
    gjf07 = work_dir / "step_07_g16_sp_readchk" / "backups" / "A000001.gjf"
    try:
        gjf_text = gjf07.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SmokeValidationError("step_07 input file is missing") from exc
    if "%OldChk=A000001.old.chk" not in gjf_text:
        raise SmokeValidationError("step_07 input lacks the %OldChk directive")


def validate_identity_snapshot(path: Path) -> dict[str, dict[str, object]]:
    """Validate the before/after g16 binary identity record."""

    raw = _load_object(path, label="g16 identity")
    expected_paths = {G16_PATH, L1_PATH}
    if set(raw) != expected_paths:
        raise SmokeValidationError(f"g16 identity paths mismatch: {sorted(raw)!r}")
    result: dict[str, dict[str, object]] = {}
    for expected_path in sorted(expected_paths):
        value = raw.get(expected_path)
        if not isinstance(value, dict):
            raise SmokeValidationError(f"g16 identity entry is malformed: {expected_path}")
        if value.get("path") != expected_path or not isinstance(value.get("realpath"), str):
            raise SmokeValidationError(f"g16 identity path record is malformed: {expected_path}")
        if not isinstance(value.get("sha256"), str) or _SHA256_RE.fullmatch(value["sha256"]) is None:
            raise SmokeValidationError(f"g16 identity digest is malformed: {expected_path}")
        for field in ("size", "mtime_ns", "device", "inode"):
            if type(value.get(field)) is not int or value[field] < 0:
                raise SmokeValidationError(f"g16 identity {field} is malformed: {expected_path}")
        result[expected_path] = dict(value)
    return result


def validate_identity_stable(before: Path, after: Path) -> None:
    before_value = validate_identity_snapshot(before)
    after_value = validate_identity_snapshot(after)
    if before_value != after_value:
        raise SmokeValidationError("g16/l1.exe identity changed during the smoke")


def validate_smoke_bundle(
    result_root: Path,
    *,
    step_names: Iterable[str],
    checkpoint: bool = False,
) -> None:
    """Validate every local artifact copied from a successful remote harness."""

    validate_preflight_lines((result_root / "preflight_4line.txt").read_text(encoding="utf-8"))
    validate_capability_file(result_root / "capability.json")
    validate_identity_stable(
        result_root / "g16_identity_before.json",
        result_root / "g16_identity_after.json",
    )
    work_dir = result_root / "methane_confflow_work"
    validate_workflow_artifacts(work_dir, step_names=step_names, checkpoint=checkpoint)
    validate_g16_artifacts(work_dir, checkpoint=checkpoint)


def _quote(value: str) -> str:
    return shlex.quote(value)


def identity_probe_script() -> str:
    """Return the controlled-Python identity recorder used by the harness."""

    return """import hashlib, json, os, sys
result = {}
for path in sys.argv[1:]:
    stat = os.stat(path)
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    result[path] = {
        'path': path,
        'realpath': os.path.realpath(path),
        'size': stat.st_size,
        'mtime_ns': stat.st_mtime_ns,
        'device': stat.st_dev,
        'inode': stat.st_ino,
        'sha256': digest.hexdigest(),
    }
print(json.dumps(result, sort_keys=True))
"""


def capability_probe_script() -> str:
    """Return the controlled-Python Gate B capability assertion."""

    return f"""import json, re, sys
data = json.load(open(sys.argv[1], encoding='utf-8'))
assert data.get('schema_version') == {CAPABILITY_SCHEMA_VERSION!r}, data
assert data.get('version') == {REFERENCE_VERSION!r}, data
build = data.get('build')
assert isinstance(build, dict) and build.get('commit') == {REFERENCE_BUILD_COMMIT!r} and build.get('dirty') is False, data
producer = data.get('producer')
assert isinstance(producer, dict) and producer.get('package') == 'confflow' and producer.get('version') == {REFERENCE_VERSION!r}, data
producer_build = producer.get('build')
assert isinstance(producer_build, dict) and producer_build.get('commit') == {REFERENCE_BUILD_COMMIT!r} and producer_build.get('dirty') is False, data
wheel = producer.get('wheel')
assert isinstance(wheel, dict) and wheel.get('filename') == {REFERENCE_WHEEL_FILENAME!r} and wheel.get('sha256') == {REFERENCE_WHEEL_SHA256!r}, data
install = data.get('install_provenance')
if not isinstance(install, dict):
    install = producer.get('install_provenance')
assert isinstance(install, dict) and install.get('status') == 'verified', data
executable = data.get('executable')
assert isinstance(executable, dict), data
assert executable.get('path') == {CONFFLOW_EXE!r}, data
if 'realpath' in executable:
    assert executable.get('realpath') == {CONFFLOW_EXE!r}, data
assert executable.get('python') == {CONFFLOW_PYTHON!r}, data
assert isinstance(executable.get('sha256'), str) and re.fullmatch(r'[0-9a-fA-F]{{64}}', executable['sha256']), data
"""


def build_inner_harness(*, workflow_yaml: str, label: str) -> str:
    """Build the WSL harness while keeping all workflow policy in one template."""

    template = r'''#!/usr/bin/env bash
set -euo pipefail

CONFFLOW_EXE=/opt/confflow-1.4.6-prod-venv/bin/confflow
CONFFLOW_PY=/opt/confflow-1.4.6-prod-venv/bin/python3.12
G16=/opt/g16/g16
L1=/opt/g16/l1.exe

export g16root=/opt
export GAUSS_EXEDIR=/opt/g16/bsd:/opt/g16
export PATH=/opt/g16/bsd:/opt/g16:/usr/bin:/bin
export GAUSS_SCRDIR=/opt/g16/scratch

TMP=$(mktemp -d /tmp/jobdesk-confflow-g16.XXXXXX)
case "$TMP" in
  /tmp/jobdesk-confflow-g16.[A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9]) ;;
  *) echo "unsafe temporary directory: $TMP" >&2; exit 1 ;;
esac
cd "$TMP"
printf '%s\n' "[smoke] RESULT_DIR=$TMP"

cat > methane.xyz <<'XYZE'
5
methane
C   0.000000   0.000000   0.000000
H   0.629118   0.629118   0.629118
H  -0.629118  -0.629118   0.629118
H  -0.629118   0.629118  -0.629118
H   0.629118  -0.629118  -0.629118
XYZE

cat > confflow.yaml <<'YCONF'
__WORKFLOW_YAML__
YCONF

echo "[smoke] === PRE-FLIGHT: g16 identity probe ==="
{
  file "$G16" "$L1" 2>&1 | awk '/ELF 64-bit/ { count++ } END { print count + 0 }'
  if head -c 4096 "$G16" | grep -aq JOBDESK_MOCK; then printf '1\n'; else printf '0\n'; fi
  if head -c 4096 "$L1" | grep -aq JOBDESK_MOCK; then printf '1\n'; else printf '0\n'; fi
  if [ -f /opt/g16/bsd/g16.profile ]; then printf '%s\n' /opt/g16/bsd/g16.profile; else printf '%s\n' MISSING; fi
} > preflight_4line.txt
cat preflight_4line.txt
PREF_COUNT=$(wc -l < preflight_4line.txt)
PREF_L1=$(sed -n '1p' preflight_4line.txt)
PREF_L2=$(sed -n '2p' preflight_4line.txt)
PREF_L3=$(sed -n '3p' preflight_4line.txt)
PREF_L4=$(sed -n '4p' preflight_4line.txt)
if [ "$PREF_COUNT" -ne 4 ] || [ "$PREF_L1" != "2" ] || [ "$PREF_L2" != "0" ] || [ "$PREF_L3" != "0" ] || [ "$PREF_L4" != "/opt/g16/bsd/g16.profile" ]; then
  echo "[smoke] preflight failed" >&2
  exit 1
fi

echo "[smoke] === CAPABILITY PROBE: $CONFFLOW_EXE ==="
set +e
PYTHONPATH='' "$CONFFLOW_EXE" --capabilities --json > capability.json 2> capability.stderr
CAP_RC=$?
set -e
if [ "$CAP_RC" -ne 0 ]; then
  echo "[smoke] capability probe failed rc=$CAP_RC" >&2
  cat capability.stderr >&2 || true
  exit "$CAP_RC"
fi
cat capability.json
cat capability.stderr >&2 || true
"$CONFFLOW_PY" - "capability.json" <<'PY'
__CAPABILITY_VALIDATOR__
PY

"$CONFFLOW_PY" - "$G16" "$L1" > g16_identity_before.json <<'PY'
__IDENTITY_PROBE__
PY

printf '[smoke] starting ConfFlow (%s)\n' __LABEL_SHELL__
set +e
PYTHONPATH='' "$CONFFLOW_EXE" methane.xyz -c confflow.yaml -w methane_confflow_work --resume --verbose 2>&1 | tee confflow.log
CONFFLOW_RC=${PIPESTATUS[0]}
set -e
"$CONFFLOW_PY" - "$G16" "$L1" > g16_identity_after.json <<'PY'
__IDENTITY_PROBE__
PY
printf '%s\n' "[smoke] confflow rc=$CONFFLOW_RC"
find methane_confflow_work -maxdepth 4 -type f -print 2>&1 | sort || true
for artifact in run_summary.json workflow_stats.json .workflow_state.json output_manifest.json; do
  echo "[smoke] === $artifact ==="
  if [ -f "methane_confflow_work/$artifact" ]; then cat "methane_confflow_work/$artifact"; else echo '(missing)'; fi
done
exit "$CONFFLOW_RC"
'''
    return (
        template.replace("__WORKFLOW_YAML__", workflow_yaml.rstrip())
        .replace("__LABEL_SHELL__", shlex.quote(label))
        .replace("__CAPABILITY_VALIDATOR__", capability_probe_script().rstrip())
        .replace("__IDENTITY_PROBE__", identity_probe_script().rstrip())
    )


def deploy_harness(harness: str, *, destination: str) -> None:
    """Write a harness to WSL through base64 without touching any system file."""

    import base64

    payload = base64.b64encode(harness.encode("utf-8")).decode("ascii")
    deployer = (
        "import base64, os, pathlib, sys; "
        "data=base64.b64decode(sys.stdin.read().strip()).decode('utf-8'); "
        f"pathlib.Path({destination!r}).write_text(data, encoding='utf-8', newline='\\n'); "
        f"os.chmod({destination!r}, 0o755); print('harness written')"
    )
    proc = subprocess.run(
        [WIN_WSL, "-d", WSL_DISTRO, "--", "python3", "-u", "-c", deployer],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise SmokeValidationError(f"WSL harness deployment failed: {proc.stderr or proc.stdout}")
    print(proc.stdout, end="")


def run_inner(destination: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [WIN_WSL, "-d", WSL_DISTRO, "--", "bash", destination],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=900,
    )


def _validate_target(target: Path, *, name: str) -> None:
    base = target.parent.resolve()
    if base.name != "tmp60f7j8ix" or target.name != name:
        raise SmokeValidationError(f"refusing unsafe local smoke target: {target}")


def pull_artifacts(remote_tmp: str, target: Path, *, name: str) -> None:
    """Copy a harness directory to the run-owned local results directory."""

    if _REMOTE_TMP_RE.fullmatch(remote_tmp) is None:
        raise SmokeValidationError(f"refusing unsafe remote smoke directory: {remote_tmp}")
    _validate_target(target, name=name)
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    wsl_path = subprocess.run(
        [WIN_WSL, "-d", WSL_DISTRO, "--", "wslpath", "-w", remote_tmp],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout.strip()
    shutil.copytree(wsl_path, target, symlinks=True)


def cleanup_remote(remote_tmp: str) -> None:
    """Remove only a verified harness-owned /tmp directory after a full pass."""

    if _REMOTE_TMP_RE.fullmatch(remote_tmp) is None:
        raise SmokeValidationError(f"refusing unsafe remote cleanup target: {remote_tmp}")
    script = (
        "set -eu; p=$(readlink -f -- "
        + _quote(remote_tmp)
        + "); case \"$p\" in /tmp/jobdesk-confflow-g16.[A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9]) ;; *) exit 2 ;; esac; "
        + "[ -d \"$p\" ]; rm -rf -- \"$p\""
    )
    proc = subprocess.run(
        [WIN_WSL, "-d", WSL_DISTRO, "--", "bash", "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise SmokeValidationError(f"safe WSL smoke cleanup failed: {proc.stderr or proc.stdout}")
