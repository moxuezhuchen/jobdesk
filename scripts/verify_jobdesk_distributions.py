#!/usr/bin/env python3
"""Fail closed unless built JobDesk archives contain every workflow template."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path

EXPECTED_EXAMPLES = {
    "conformer_ensemble.json",
    "fan_in_refine.json",
    "fan_out_gen_opt.json",
    "linear_opt_freq.json",
}
RESOURCE_SEGMENT = "jobdesk_app/resources/workflow_examples/"


def _example_names(member_names: list[str]) -> set[str]:
    return {
        Path(name).name
        for name in member_names
        if RESOURCE_SEGMENT in name.replace("\\", "/") and name.lower().endswith(".json")
    }


def verify_distributions(wheel: Path, sdist: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        wheel_examples = _example_names(archive.namelist())
    with tarfile.open(sdist, "r:gz") as archive:
        sdist_examples = _example_names(archive.getnames())

    if wheel_examples != EXPECTED_EXAMPLES:
        raise ValueError(f"wheel workflow examples mismatch: {sorted(wheel_examples)}")
    if sdist_examples != EXPECTED_EXAMPLES:
        raise ValueError(f"sdist workflow examples mismatch: {sorted(sdist_examples)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sdist", type=Path, required=True)
    args = parser.parse_args()
    verify_distributions(args.wheel, args.sdist)
    print("verified wheel and sdist workflow examples: " + ", ".join(sorted(EXPECTED_EXAMPLES)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
