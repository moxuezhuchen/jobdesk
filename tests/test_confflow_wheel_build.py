from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
import zipfile
from pathlib import Path

import pytest

from jobdesk_app.core.confflow_contract import (
    REFERENCE_BUILD_COMMIT,
    REFERENCE_VERSION,
    REFERENCE_WHEEL_FILENAME,
    REFERENCE_WHEEL_SHA256,
)

WHEEL_DIR_VAR = "JOBDESK_CONFFLOW_WHEEL_DIR"
CHECKOUT_DIR_VAR = "JOBDESK_CONFFLOW_CHECKOUT_DIR"


def _parse_build_provenance(text: str) -> tuple[str | None, bool | None]:
    values: dict[str, object] = {}
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in {"COMMIT", "DIRTY"}
        ):
            try:
                values[node.target.id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                return None, None
    commit = values.get("COMMIT")
    dirty = values.get("DIRTY")
    if not isinstance(commit, str) or not isinstance(dirty, bool):
        return None, None
    return commit, dirty


def test_parse_build_provenance_annotated_assignments() -> None:
    text = "COMMIT: str | None = 'abc'\nDIRTY: bool | None = False\n"
    assert _parse_build_provenance(text) == ("abc", False)


def test_parse_build_provenance_rejects_non_constant() -> None:
    assert _parse_build_provenance("COMMIT: str | None = value\nDIRTY: bool | None = False\n") == (None, None)


def test_wheel_build_provenance() -> None:
    wheel_dir_value = os.environ.get(WHEEL_DIR_VAR)
    checkout_dir_value = os.environ.get(CHECKOUT_DIR_VAR)
    if wheel_dir_value is None and checkout_dir_value is None:
        pytest.skip(f"{WHEEL_DIR_VAR} and {CHECKOUT_DIR_VAR} are not set")
    assert (
        wheel_dir_value is not None and checkout_dir_value is not None
    ), f"{WHEEL_DIR_VAR} and {CHECKOUT_DIR_VAR} must be configured together"

    wheel_dir = Path(wheel_dir_value)
    checkout_dir = Path(checkout_dir_value)
    assert wheel_dir.is_dir(), f"Wheel directory does not exist: {wheel_dir}"
    assert checkout_dir.is_dir(), f"Checkout directory does not exist: {checkout_dir}"
    wheels = list(wheel_dir.glob(REFERENCE_WHEEL_FILENAME))
    assert len(wheels) == 1, f"Expected one ConfFlow wheel, found {wheels}"

    expected_commit = subprocess.check_output(["git", "-C", str(checkout_dir), "rev-parse", "HEAD"], text=True).strip()
    assert expected_commit == REFERENCE_BUILD_COMMIT
    assert hashlib.sha256(wheels[0].read_bytes()).hexdigest() == REFERENCE_WHEEL_SHA256
    assert REFERENCE_VERSION in wheels[0].name
    with zipfile.ZipFile(wheels[0]) as archive:
        build_files = [name for name in archive.namelist() if name == "confflow/__build__.py"]
        assert build_files == ["confflow/__build__.py"]
        commit, dirty = _parse_build_provenance(archive.read(build_files[0]).decode())

    assert commit is not None and re.fullmatch(r"[0-9a-f]{40}", commit)
    assert commit == REFERENCE_BUILD_COMMIT
    assert dirty is False
