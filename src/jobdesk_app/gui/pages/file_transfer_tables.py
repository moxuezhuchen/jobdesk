"""Remote-edit session dataclass for the Files page.

Extracted from file_transfer_page to reduce module size and support
independent testing.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class _RemoteEditSession:
    remote_path: str
    local_path: Path
    uploaded_signature: str
    uploading_signature: str | None = None
