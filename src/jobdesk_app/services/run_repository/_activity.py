"""Activity log persistence for SubmitPage."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime


def append_activity(
    connection: sqlite3.Connection,
    *,
    level: str = "info",
    message: str,
    run_id: str | None = None,
    payload: dict | None = None,
) -> int:
    """Append a single entry to the submit_activity_log table.

    Returns the auto-incremented integer id of the inserted row.
    """
    timestamp = datetime.now().isoformat()
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    cursor = connection.execute(
        """
        INSERT INTO submit_activity_log(ts, level, message, payload_json, run_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (timestamp, level, message, payload_json, run_id),
    )
    return cursor.lastrowid  # type: ignore[return-value]


def list_recent_activity(
    connection: sqlite3.Connection,
    *,
    limit: int = 50,
) -> list[dict]:
    """Return the most recent ``limit`` activity log entries, oldest first."""
    rows = connection.execute(
        """
        SELECT id, ts, level, message, payload_json, run_id
        FROM submit_activity_log
        ORDER BY ts ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "id": row["id"],
                "ts": row["ts"],
                "level": row["level"],
                "message": row["message"],
                "payload": json.loads(row["payload_json"]),
                "run_id": row["run_id"],
            }
        )
    return result
