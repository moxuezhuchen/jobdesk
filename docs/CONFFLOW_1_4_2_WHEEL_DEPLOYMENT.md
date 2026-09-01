> **DO NOT USE FOR CURRENT DEPLOYMENT AS THE CANONICAL ENTRY.** This retained
> historical filename is a compatibility reference only. The canonical current
> Windows setup is [README.md — Windows chemistry environment](../README.md#windows-chemistry-environment);
> contributor setup is [CONTRIBUTING.md — Development environment](../CONTRIBUTING.md#development-environment).
> The 2.1.6 facts and checks below remain for old links and auditability; follow
> the canonical entries for current instructions.

# ConfFlow 2.1.6 Wheel 构建与部署参考（历史文件名）

This page records the release identity and verification contract for the
legacy `1.4.2` filename; it is not a separate current deployment procedure.
JobDesk `v0.7.10` keeps the compatibility window
`confflow>=2.0,<3.0`, but its formal released producer reference is ConfFlow
`v2.1.6`. Use only the released wheel
`confflow-2.1.6-py3-none-any.whl` with SHA-256
`d8fe44611ec128fece79309f42792b716c1f2f59871b5aab4024f3d136f75548`.

The authoritative ConfFlow source repository is
`Ubuntu-24.04:/opt/ConfFlow`. Do not rebuild or substitute a development wheel
for the released artifact used by the formal control path.

## Windows JobDesk environment (reference only)

Put the exact released wheel at
`.matrix-artifacts\confflow-2.1.6-py3-none-any.whl`, then use the Python 3.13
chemistry lock. The lock records the wheel digest and the complete dependency
set; the editable JobDesk install is deliberately performed without resolving
the broad `confflow>=2.0,<3.0` range again.

```powershell
$venvPython = ".venv\Scripts\python.exe"
$wheel = ".matrix-artifacts\confflow-2.1.6-py3-none-any.whl"
$expectedSha256 = "d8fe44611ec128fece79309f42792b716c1f2f59871b5aab4024f3d136f75548"
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $wheel).Hash.ToLowerInvariant() -ne $expectedSha256) {
    throw "The ConfFlow wheel digest does not match the approved v2.1.6 artifact."
}
uv pip sync --python $venvPython --find-links .matrix-artifacts `
  requirements\locks\jobdesk-chem-py313-win_amd64.txt
& $venvPython -m pip install --no-deps -e .
& $venvPython -m pip check
& $venvPython -c "import confflow, importlib.metadata as metadata, pathlib, sys; venv=pathlib.Path(sys.executable).resolve().parents[1]; source=pathlib.Path(confflow.__file__).resolve(); assert metadata.version('jobdesk') == '0.7.10'; assert metadata.version('confflow') == '2.1.6'; assert source.is_relative_to(venv / 'Lib' / 'site-packages'); print(confflow.__version__, source)"
```

Use the `jobdesk-chem-py311-win_amd64.txt` or
`jobdesk-chem-py312-win_amd64.txt` lock when using the corresponding Python
version. Check or regenerate all three locks with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/compile_chem_locks.ps1 -Check
```

The check must pass before a lock or wheel is used. `pip check` must report no
broken requirements, and the final import check must show both the exact
ConfFlow version and a path under the intended `.venv`; the command above
asserts that path instead of relying on an activated shell.

## Capability handshake

The installed producer must expose the current capability contract:

```powershell
& ".venv\Scripts\confflow.exe" --capabilities --json
```

The payload must include schema v4 and the declared artifact names:

```json
{
  "schema_version": 4,
  "artifacts": {
    "run_summary": "run_summary.json",
    "workflow_stats": "workflow_stats.json",
    "workflow_state": ".workflow_state.json",
    "output_manifest": "output_manifest.json",
    "run_report": "{basename}.txt",
    "min_xyz": "{basename}min.xyz"
  }
}
```

Schema v4 must also contain non-empty `producer` and `executable` provenance
blocks, including the package version, build, wheel digest, executable path,
executable digest, and Python version. The `workflow_state`, `resume`, and
`dag` capabilities must all be true.

## Linux compute node (reference only)

The remote compute node must use the same approved ConfFlow `2.1.6` release
wheel and a controlled dependency lock for its Python version:

```bash
python3 -m pip install --no-index --find-links /path/to/wheelhouse \
  --require-hashes -r /path/to/confflow-2.1.6-py312-linux-x86_64.lock
python3 -m pip install --no-deps /path/to/confflow-2.1.6-py3-none-any.whl
python3 -m pip check
python3 -c "import confflow; assert confflow.__version__ == '2.1.6'; print(confflow.__file__)"
confflow --capabilities --json
```

JobDesk performs the capability v4 preflight before input upload and again at
submission. It rejects a producer outside `>=2.0,<3.0`, a missing capability,
an artifact mismatch, unverified provenance, or a release digest mismatch.

## Release boundary

The ConfFlow release workflow creates the wheel, source distribution,
checksums, and optional SBOM, but does not publish to public PyPI. Deployment
must use a verified local or release artifact. This historical filename is kept
only so existing references remain discoverable; link current operators to the
canonical README/CONTRIBUTING entries above rather than treating this legacy
filename as current guidance.
