"""Persistence helpers for the accepted ConfFlow producer identity."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any


def record_run_provenance(
    connection: sqlite3.Connection,
    run_id: str,
    capability: dict[str, Any],
    *,
    resolved_executable: str,
    resolved_realpath: str = "",
    config_contract: dict[str, object] | None = None,
    remote_identity: dict[str, object] | None = None,
) -> None:
    """Upsert the exact capability payload accepted for a run."""
    producer = capability.get("producer")
    if not isinstance(producer, dict):
        producer = {}
    build = capability.get("build")
    if not isinstance(build, dict):
        build = {}
    version = producer.get("version", capability.get("version", ""))
    commit = producer.get("build_commit", build.get("commit"))
    dirty = producer.get("dirty", build.get("dirty"))
    wheel_sha256 = producer.get("wheel_sha256")
    stored_capability: dict[str, Any] = capability
    if config_contract is not None or remote_identity is not None:
        stored_capability = {
            "capability": capability,
            "_jobdesk_config_contract": config_contract,
            "_jobdesk_remote_identity": remote_identity,
        }
    connection.execute(
        """
        INSERT INTO run_provenance(
            run_id, capability_json, resolved_executable, resolved_realpath,
            producer_version, producer_build_commit, producer_dirty,
            wheel_sha256, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            capability_json = excluded.capability_json,
            resolved_executable = excluded.resolved_executable,
            resolved_realpath = excluded.resolved_realpath,
            producer_version = excluded.producer_version,
            producer_build_commit = excluded.producer_build_commit,
            producer_dirty = excluded.producer_dirty,
            wheel_sha256 = excluded.wheel_sha256,
            recorded_at = excluded.recorded_at
        """,
        (
            run_id,
            json.dumps(stored_capability, ensure_ascii=False, sort_keys=True),
            resolved_executable,
            resolved_realpath,
            str(version or ""),
            None if commit is None else str(commit),
            None if dirty is None else int(bool(dirty)),
            None if wheel_sha256 is None else str(wheel_sha256),
            datetime.now().isoformat(),
        ),
    )


def load_run_provenance(connection: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM run_provenance WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    stored_capability = json.loads(row["capability_json"])
    if not isinstance(stored_capability, dict):
        stored_capability = {}
    config_contract = None
    remote_identity = None
    if isinstance(stored_capability.get("capability"), dict) and (
        "_jobdesk_config_contract" in stored_capability or "_jobdesk_remote_identity" in stored_capability
    ):
        capability = stored_capability["capability"]
        config_contract = stored_capability.get("_jobdesk_config_contract")
        remote_identity = stored_capability.get("_jobdesk_remote_identity")
    else:
        capability = stored_capability
    result = {
        "capability": capability,
        "resolved_executable": str(row["resolved_executable"]),
        "resolved_realpath": str(row["resolved_realpath"]),
        "producer_version": str(row["producer_version"]),
        "producer_build_commit": row["producer_build_commit"],
        "producer_dirty": None if row["producer_dirty"] is None else bool(row["producer_dirty"]),
        "wheel_sha256": row["wheel_sha256"],
        "recorded_at": str(row["recorded_at"]),
    }
    if config_contract is not None:
        result["config_contract"] = config_contract
    if remote_identity is not None:
        result["remote_identity"] = remote_identity
    return result
