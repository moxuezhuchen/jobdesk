from __future__ import annotations

import json
from pathlib import Path

from scripts.update_release_evidence import main, update_evidence


def test_update_evidence_preserves_payload_and_replaces_atomically(tmp_path: Path) -> None:
    evidence = tmp_path / "post-verification.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "jobdesk.release-post-verification.v1",
                "asset_verification": {"hash_result": "passed"},
            }
        ),
        encoding="utf-8",
    )

    update_evidence(
        evidence,
        stage="asset_hashes",
        status="failed",
        exit_code=23,
        release_created=True,
    )

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["schema"] == "jobdesk.release-post-verification.v1"
    assert payload["asset_verification"] == {"hash_result": "passed"}
    assert payload["stage"] == "asset_hashes"
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 23
    assert payload["release_created"] is True
    assert not list(tmp_path.glob("post-verification.json.tmp.*"))


def test_cli_initializes_missing_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "new.json"

    assert (
        main(
            [
                "--path",
                str(evidence),
                "--stage",
                "before_checkout",
                "--status",
                "workflow_started",
                "--exit-code",
                "0",
                "--release-created",
                "false",
            ]
        )
        == 0
    )

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload == {
        "schema": "jobdesk.release-post-verification.v1",
        "stage": "before_checkout",
        "status": "workflow_started",
        "exit_code": 0,
        "release_created": False,
    }
