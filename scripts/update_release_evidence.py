#!/usr/bin/env python3
"""Atomically update stage and failure status in release evidence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def update_evidence(
    path: Path,
    *,
    stage: str,
    status: str,
    exit_code: int,
    release_created: bool | None = None,
) -> None:
    """Read, update, and atomically replace one release evidence document."""

    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        payload = {"schema": "jobdesk.release-post-verification.v1"}
    if not isinstance(payload, dict):
        payload = {"schema": "jobdesk.release-post-verification.v1"}
    payload.update({"stage": stage, "status": status, "exit_code": exit_code})
    if release_created is not None:
        payload["release_created"] = release_created
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--release-created", choices=("true", "false"))
    args = parser.parse_args(argv)
    update_evidence(
        args.path,
        stage=args.stage,
        status=args.status,
        exit_code=args.exit_code,
        release_created=None if args.release_created is None else args.release_created == "true",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
