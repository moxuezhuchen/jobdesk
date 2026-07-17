"""Server config loading helpers for the Files page.

Extracted from ``file_transfer_page`` to make the YAML read path
unit-testable without instantiating a full QWidget page.

The data-safety rule is: a ``servers.yaml`` that exists but cannot
be parsed (or whose top-level is not a mapping) is **never** overwritten
silently by the sample-import flow. We raise :class:`ConfigUnreadable`
instead so the caller can show a clear error to the user.
"""

from __future__ import annotations

from pathlib import Path


class ConfigUnreadable(Exception):
    """Raised when the user's existing config file cannot be parsed.

    The Files page "Import sample" button is the most common way a
    user recovers from a broken servers.yaml -- they hit it because
    the empty-state hint is up. The original file is therefore the
    user's best chance to repair whatever is wrong (typo, half-
    written crash, encoding glitch). Overwriting it with a sample
    turns a recoverable failure into a permanent loss, so we raise
    this exception instead and let the caller show a clear error.

    Attributes:
        path: Path to the file we refused to overwrite.
        cause: The original parse failure (yaml.YAMLError or any
            non-mapping root). Surfaced verbatim in the dialog so the
            user can act on the actual error.
    """

    def __init__(self, path: Path, cause: BaseException) -> None:
        super().__init__(f"servers.yaml at {path} could not be parsed: {cause}")
        self.path = path
        self.cause = cause


def load_existing_servers_data(path: Path) -> dict:
    """Read ``path`` and return the existing mapping root, with guards.

    Returns an empty dict when ``path`` does not exist. Raises
    :class:`ConfigUnreadable` when the file exists but cannot be
    parsed (or its top level is not a mapping) -- the caller is
    responsible for surfacing the error to the user, but the file
    on disk is NOT modified by this function.

    Review-fix: extracted from ``FileTransferPage._import_sample_servers_yaml``
    so tests can drive it without instantiating a full QWidget page.
    """
    import yaml

    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigUnreadable(path, exc) from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigUnreadable(
            path,
            ValueError(f"servers.yaml top-level is {type(loaded).__name__}, expected a mapping"),
        )
    return loaded


def _load_existing_servers_data(path: Path) -> dict:
    """Backward-compatible alias for the previously-private name."""
    return load_existing_servers_data(path)
