"""RunService GUI entry point.

Re-exports ``main`` from the GUI module so that the ``jobdesk-gui``
script entry point can import from a canonical ``run_service_gui`` module.
"""
from __future__ import annotations

from ..gui.app import main

__all__ = ["main"]
