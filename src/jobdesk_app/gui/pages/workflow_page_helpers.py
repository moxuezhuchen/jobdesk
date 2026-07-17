"""Helper utilities for the workflow page."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..nodegraph.model import Node


def flow_step_detail(node: Node) -> str:
    """Format a one-line human-readable summary of a workflow step node."""
    params = node.params
    from ..nodegraph.model import NodeKind  # local import to avoid circular refs

    if node.kind is NodeKind.CONF_GEN:
        chains = params.get("chains") or []
        chain_text = ", ".join(map(str, chains)) if isinstance(chains, list) else str(chains)
        angle = params.get("angle_step")
        return (
            " · ".join(
                part
                for part in (
                    f"chains: {chain_text}" if chain_text else "",
                    f"angle: {angle}°" if angle is not None else "",
                )
                if part
            )
            or "confgen"
        )
    return str(
        params.get("keyword")
        or " · ".join(str(params[key]) for key in ("iprog", "itask") if params.get(key))
        or node.kind.value
    )
