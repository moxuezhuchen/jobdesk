"""Reusable QWidget classes embedded by the Submit page.

After Phase 10.6 cleanup the only widget left here is :class:`InputSourcePanel`.
The Phase 14A ``CalculationWidget`` / ``WorkflowWidget`` /
``InputBuilderWidget`` were retired — the Submit page is now driven entirely
by the ``WorkflowGraphEditor`` under :mod:`jobdesk_app.gui.nodegraph`.
"""
from .input_source_panel import InputSourcePanel

__all__ = ["InputSourcePanel"]
