"""Pytest-facing wrapper for the Phase 9G real-g16 ConFlow smoke harness.

Exposes ``run_smoke(target_dir)`` so a session-scoped fixture can stage the
smoke output under a pytest-managed temp dir (typically ``tmp_path_factory``
basetemp) instead of the hardcoded ``tmp60f7j8ix/phase9g_real_g16`` path the
standalone script uses.

We deliberately re-use the harness machinery from
``scripts/smoke_confflow_real_g16_wsl.py`` rather than duplicating the
base64-stamp-deployer dance.  When the smoke harness evolves, this module
inherits the change for free.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys


def _load_harness():
    """Dynamically import ``scripts/smoke_confflow_real_g16_wsl.py`` as a module.

    The script is a flat executable (no package, ``REPO_ROOT`` derived from
    ``__file__``).  Loading it via importlib lets us call ``stamp_remote``,
    ``run_inner``, ``parse_result_dir`` and ``pull_artifacts`` directly.
    """
    repo = pathlib.Path(__file__).resolve().parents[2]
    harness_path = repo / "scripts" / "smoke_confflow_real_g16_wsl.py"
    spec = importlib.util.spec_from_file_location(
        "_phase9g_smoke_harness", harness_path
    )
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"Cannot load harness from {harness_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_smoke(target: pathlib.Path, *, verbose: bool = False) -> pathlib.Path:
    """Run the real-g16 smoke; pull artifacts into ``target``.

    Returns the resulting ``methane_confflow_work`` directory.
    Raises ``RuntimeError`` on inner harness failure (non-zero exit, no
    ``RESULT_DIR`` marker, or missing expected tree).
    """
    target = pathlib.Path(target)
    target.mkdir(parents=True, exist_ok=True)
    harness = _load_harness()
    if verbose:
        print("[fixture] stamping remote harness", flush=True)
    harness.stamp_remote()
    if verbose:
        print("[fixture] running confflow (real g16, methane opt)", flush=True)
    inner = harness.run_inner()
    if verbose and inner.stdout:
        print(inner.stdout, end="")
    if verbose and inner.stderr:
        print(inner.stderr, end="", file=sys.stderr)
    if inner.returncode != 0:
        raise RuntimeError(
            f"smoke inner harness exited with {inner.returncode}"
        )
    remote_tmp = harness.parse_result_dir(inner.stdout)
    if not remote_tmp:
        raise RuntimeError(
            "smoke inner harness did not emit RESULT_DIR=...; cannot pull artifacts"
        )
    if verbose:
        print(f"[fixture] pulling artifacts from {remote_tmp}", flush=True)
    # The standalone harness's ``pull_artifacts(remote, target)`` copies
    # ``remote/methane_confflow_work`` into ``target``.  When the target is
    # the directory itself (not target/methane_confflow_work), the resulting
    # tree is target/methane_confflow_work/<confflow outputs>.
    work_dir = target / "methane_confflow_work"
    harness.pull_artifacts(remote_tmp, target)
    return work_dir
