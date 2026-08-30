from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest
from scripts.verify_jobdesk_distributions import EXPECTED_EXAMPLES, verify_distributions


def _member(name: str) -> str:
    return f"jobdesk_app/resources/workflow_examples/{name}"


def _write_wheel(path: Path, names: set[str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in sorted(names):
            archive.writestr(_member(name), "{}")


def _write_sdist(path: Path, names: set[str]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name in sorted(names):
            payload = b"{}"
            info = tarfile.TarInfo(f"jobdesk-0.7.9/src/{_member(name)}")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def test_distribution_verifier_accepts_exact_workflow_example_set(tmp_path: Path) -> None:
    wheel = tmp_path / "jobdesk-0.7.9-py3-none-any.whl"
    sdist = tmp_path / "jobdesk-0.7.9.tar.gz"
    _write_wheel(wheel, EXPECTED_EXAMPLES)
    _write_sdist(sdist, EXPECTED_EXAMPLES)

    verify_distributions(wheel, sdist)


@pytest.mark.parametrize("archive", ["wheel", "sdist"])
@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_distribution_verifier_rejects_each_archive_mismatch(tmp_path: Path, archive: str, mutation: str) -> None:
    wheel = tmp_path / "jobdesk-0.7.9-py3-none-any.whl"
    sdist = tmp_path / "jobdesk-0.7.9.tar.gz"
    wheel_names = set(EXPECTED_EXAMPLES)
    sdist_names = set(EXPECTED_EXAMPLES)
    selected = wheel_names if archive == "wheel" else sdist_names
    if mutation == "missing":
        selected.remove("linear_opt_freq.json")
    else:
        selected.add("unexpected.json")
    _write_wheel(wheel, wheel_names)
    _write_sdist(sdist, sdist_names)

    with pytest.raises(ValueError, match=archive):
        verify_distributions(wheel, sdist)
