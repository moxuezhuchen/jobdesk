from __future__ import annotations

from collections.abc import Iterable


def validate_server_id_change(existing_ids: Iterable[str], old_id: str | None, new_id: str) -> str | None:
    candidate = new_id.strip()
    if not candidate:
        return "Server ID is required"
    normalized_existing = {sid.strip() for sid in existing_ids if sid.strip()}
    if old_id is not None and candidate == old_id:
        return None
    if candidate in normalized_existing:
        return f"Server ID already exists: {candidate}"
    return None
