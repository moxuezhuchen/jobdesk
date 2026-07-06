"""Smoke-test the workflow_spec wrapper against the real ConfFlow Pydantic models.

This script is intentionally NOT part of the regular pytest run because it
requires the ``confflow`` package and a working numpy install on the
developer machine (the JobDesk Win GUI is shipped to users who do not have
confflow installed locally). It exercises the same code paths the wizard
uses end-to-end so we can verify the round-trip on machines that do have
the dependency.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the ConfFlow checkout importable without installing it.
CONFFLOW_SRC = Path(r"C:\dft\confflow")
if not (CONFFLOW_SRC / "confflow" / "core" / "models.py").exists():
    print(f"SKIP: {CONFFLOW_SRC} does not contain confflow source")
    sys.exit(0)
sys.path.insert(0, str(CONFFLOW_SRC))

# Re-import workflow_spec now that confflow is on sys.path so the
# try/except at module load picks up the real Pydantic models.
import importlib  # noqa: E402

import jobdesk_app.core.workflow_spec as wsm  # noqa: E402

importlib.reload(wsm)
assert wsm._CONFFLOW_AVAILABLE, "confflow import should succeed now"

# Round-trip from form -> YAML -> form
spec_a = wsm.WorkflowSpec.from_form(
    work_dir_name="hexane_work",
    program="gaussian",
    method="B3LYP",
    basis="6-31G(d)",
    charge=0,
    multiplicity=1,
    nproc=8,
    memory_mb=4096,
    steps=("confgen", "preopt", "opt", "refine", "sp"),
    extra_options={"solvent": "water"},
)
yaml_a = spec_a.to_yaml()
print("YAML preview:")
print(yaml_a)

spec_b = wsm.WorkflowSpec.from_yaml(yaml_a)
form_b = spec_b.to_form()
assert form_b["program"] == "gaussian"
assert form_b["method"] == "B3LYP"
assert form_b["nproc"] == 8
assert form_b["steps"] == ["confgen", "preopt", "opt", "refine", "sp"]
print("Round-trip OK")

# Dry-run
report = spec_a.dry_run()
assert report.ok, f"dry_run failed: {report.error}"
print(f"dry_run: ok={report.ok}, preview_lines={len(report.preview_lines)}")

# Atomic write
out = Path("smoke_workflow.yaml")
wsm.write_workflow_yaml(spec_a, out)
assert out.exists()
assert not out.with_suffix(out.suffix + ".tmp").exists()
out.unlink()
print("write_workflow_yaml OK")

print("ALL SMOKE TESTS PASSED")
