"""Reusable QWidget classes embedded by the Submit page.

After Phase 10.6 cleanup the only widget left here is :class:`InputSourcePanel`.
The Phase 14A ``CalculationWidget`` / ``WorkflowWidget`` /
``InputBuilderWidget`` were retired — the Submit page is now driven entirely
by the ``WorkflowGraphEditor`` under :mod:`jobdesk_app.gui.nodegraph`.

Phase 2.1 added :class:`EmptyStateHint` for the other 3 pages of the shell.
Phase 3.1 added :class:`InlineBanner` for non-modal warning/error feedback.
"""
from .empty_state_hint import EmptyStateHint
from .inline_banner import InlineBanner
from .input_source_panel import InputSourcePanel

__all__ = ["EmptyStateHint", "InlineBanner", "InputSourcePanel"]
