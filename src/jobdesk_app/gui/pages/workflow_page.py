"""Backward compatibility shim for workflow_page.

.. deprecated::
    Import directly from the new module location::

        from jobdesk_app.gui.pages.workflow_page import WorkflowPage, WorkflowDraft

    This file will be removed in a future version.
"""
from __future__ import annotations

import warnings

warnings.warn(
    "Importing from 'jobdesk_app.gui.pages.workflow_page' directly is deprecated. "
    "Import from 'jobdesk_app.gui.pages.workflow_page' instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from the new module location
from jobdesk_app.gui.pages.workflow_page import WorkflowDraft, WorkflowPage

__all__ = ["WorkflowDraft", "WorkflowPage"]
