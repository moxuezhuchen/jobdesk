"""RunService CLI entry point.

Re-exports ``main`` from the CLI module so that the ``jobdesk`` script
entry point can import from a canonical ``run_service_cli`` module.
"""
from __future__ import annotations

from ..cli import main

__all__ = ["main"]
