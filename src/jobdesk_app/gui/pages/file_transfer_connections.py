"""Compatibility names for the application-owned Files connection state."""

from ...application.files_connections import (
    ApplicationFilesConnectionController,
    FilesConnectionController,
    FileTransferConnectionSnapshot,
)

ConnectionsCoordinator = ApplicationFilesConnectionController

__all__ = ["ConnectionsCoordinator", "FileTransferConnectionSnapshot", "FilesConnectionController"]
