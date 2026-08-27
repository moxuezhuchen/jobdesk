"""Run Black only for Python files changed from an approved Git base.

The repository contains historical formatting debt outside the current
remediation.  This gate keeps that debt out of the changed-file quality check
while making the approved base explicit in CI.

For local dirty-tree validation, files changed relative to ``HEAD`` and
untracked Python files are included as well.  CI checkouts are clean, so only
the base-to-HEAD diff is considered there.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _git(*args: str, cwd: Path) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _repo_root() -> Path:
    return Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )


def _resolve_commit(ref: str, *, cwd: Path) -> str:
    candidates = [ref]
    if not ref.startswith("refs/"):
        candidates.extend((f"refs/remotes/origin/{ref}", f"origin/{ref}"))

    for candidate in candidates:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{candidate}^{{commit}}"],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()

    tried = ", ".join(candidates)
    raise SystemExit(f"Unable to resolve approved Git base {ref!r}; tried: {tried}")


def _normalise(paths: list[str], *, cwd: Path) -> set[str]:
    result: set[str] = set()
    for value in paths:
        path = value.replace("\\", "/")
        if path.endswith(".py") and (cwd / Path(path)).is_file():
            result.add(path)
    return result


def _changed_from_base(*, base: str, head: str, cwd: Path) -> set[str]:
    return _normalise(
        _git(
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            base,
            head,
            "--",
            "*.py",
            cwd=cwd,
        ),
        cwd=cwd,
    )


def _dirty_python_files(*, cwd: Path) -> set[str]:
    tracked = _git(
        "diff",
        "HEAD",
        "--name-only",
        "--diff-filter=ACMR",
        "--",
        "*.py",
        cwd=cwd,
    )
    untracked = _git("ls-files", "--others", "--exclude-standard", "--", "*.py", cwd=cwd)
    return _normalise(tracked + untracked, cwd=cwd)


def _base_from_environment() -> str | None:
    explicit = os.environ.get("BLACK_BASE_REF")
    if explicit:
        return explicit

    # These are the standard GitHub variables for a PR branch and a push.
    # The workflow passes the exact event SHA through BLACK_BASE_REF, but the
    # fallbacks make the script useful in a manually reproduced CI shell.
    return os.environ.get("GITHUB_BASE_REF") or os.environ.get("GITHUB_EVENT_BEFORE")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref",
        help="approved Git ref/SHA; defaults to BLACK_BASE_REF or GitHub base variables",
    )
    parser.add_argument("--head-ref", default="HEAD", help="candidate Git ref; defaults to HEAD")
    parser.add_argument(
        "--include-working-tree",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="include dirty and untracked Python files (enabled by default for local validation)",
    )
    args = parser.parse_args()

    root = _repo_root()
    base_ref = args.base_ref or _base_from_environment()
    if not base_ref:
        parser.error("an approved base is required; pass --base-ref or set BLACK_BASE_REF")

    base = _resolve_commit(base_ref, cwd=root)
    head = _resolve_commit(args.head_ref, cwd=root)
    files = _changed_from_base(base=base, head=head, cwd=root)
    if args.include_working_tree:
        files.update(_dirty_python_files(cwd=root))

    selected = sorted(files)
    print(f"Approved base: {base}")
    print(f"Candidate head: {head}")
    if not selected:
        print("No changed Python files; Black check passed.")
        return 0

    print(f"Checking {len(selected)} changed Python file(s) with Black (line length 120, target py311):")
    print("\n".join(f"  {path}" for path in selected))
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "black",
            "--check",
            "--line-length",
            "120",
            "--target-version",
            "py311",
            *selected,
        ],
        cwd=root,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
