"""Pure helper functions for the Runs/Results page (no Qt widget state)."""
from __future__ import annotations


def format_energy(value) -> str:
    """Format a computed energy value as a human-readable string."""
    return f"{value:.6f} Hartree" if value is not None else "—"


def format_seconds(value) -> str:
    """Format a duration in seconds as a human-readable string."""
    if value is None:
        return "—"
    seconds = float(value)
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {int(secs)}s"
    hours, minutes = divmod(int(minutes), 60)
    return f"{int(hours)}h {int(minutes)}m {int(secs)}s"
