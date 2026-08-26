"""Compatibility import for the Files-page connection controller.

Connection construction and lifecycle now live in the Qt-free application
layer.  Keep this module as a stable import for downstream GUI fixtures while
preventing concrete SSH/SFTP wiring from returning to the page package.
"""

from typing import Any, Callable

from ...application.files_connections import (
    FilesConnectionController,
    FileTransferConnectionSnapshot,
)


class ConnectionsCoordinator(FilesConnectionController):
    """Backward-compatible constructor facade for older GUI callers.

    The former page-local coordinator accepted ``run_tasks_provider``.  Keep
    that contract here and turn it into the same delete-root provider used by
    the application controller; this prevents old injected coordinators from
    silently losing their safety policy while the implementation stays
    Qt-free in :mod:`jobdesk_app.application.files_connections`.
    """

    def __init__(
        self,
        *,
        status_cb: Callable[[str], None],
        log_cb: Callable[[str], None],
        create_ssh: Callable[..., Any],
        create_sftp: Callable[..., Any],
        run_tasks_provider: Callable[[], list[Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        if "allowed_delete_roots_provider" not in kwargs and run_tasks_provider is not None:
            from .file_transfer_helpers import collect_remote_delete_roots

            kwargs["allowed_delete_roots_provider"] = lambda: collect_remote_delete_roots(run_tasks_provider())
        super().__init__(
            status_cb=status_cb,
            log_cb=log_cb,
            create_ssh=create_ssh,
            create_sftp=create_sftp,
            **kwargs,
        )


__all__ = ["ConnectionsCoordinator", "FileTransferConnectionSnapshot", "FilesConnectionController"]
