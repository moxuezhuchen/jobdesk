"""Lease timestamp serialization utilities for submit ownership."""

from __future__ import annotations

from datetime import datetime, timezone


def _utc_lease_timestamp(value: datetime) -> str:
    """Serialize a lease instant in one lexically stable UTC representation."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_lease_timestamp(value: str) -> datetime:
    """Parse an explicitly zoned ISO lease timestamp as a UTC instant."""
    parsed = datetime.fromisoformat(
        value[:-1] + "+00:00" if value.endswith("Z") else value
    )
    if parsed.tzinfo is None:
        raise ValueError("submit lease timestamp has no timezone")
    return parsed.astimezone(timezone.utc)
