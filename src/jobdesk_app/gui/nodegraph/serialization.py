"""JSON round-trip for :class:`NodeGraph` and the QUndoStack command set.

This module owns the JSON schema used by the ``Save template…`` /
``Load template…`` toolbar actions on :class:`WorkflowGraphEditor`. The
schema is intentionally narrow:

::

    {
      "nodes": [
        {
          "id": "<uuid-hex>",
          "kind": "opt" | "preopt" | ...,
          "title": "...",
          "inputs": [{"name": "...", "type": "structure", ...}, ...],
          "outputs": [...],
          "params": {...},
          "position": [x, y],
        },
        ...
      ],
      "edges": [
        {
          "id": "<uuid-hex>",
          "src_node": "<id>", "src_port": "...",
          "dst_node": "<id>", "dst_port": "...",
        },
        ...
      ],
    }

Notes
-----
* The schema is round-trippable: ``NodeGraph`` instances produced by
  ``from_json(to_json(g))`` compare field-by-field equal to ``g`` aside
  from any node IDs that ``from_json`` regenerates on collision. We
  do not regenerate IDs in the happy path; the round-trip preserves
  them verbatim.
* ``undo()`` / ``redo()`` mutate the model in place. The owning
  :class:`GraphScene` is expected to refresh its visual registry
  between calls (see :meth:`GraphScene._sync_from_model`).
"""
from __future__ import annotations

from typing import Any

from PySide6.QtGui import QUndoCommand

from jobdesk_app.gui.nodegraph.model import (
    Edge,
    Node,
    NodeGraph,
    NodeKind,
    Port,
    PortType,
)

# ── JSON round-trip ────────────────────────────────────────────────────


def to_json(graph: NodeGraph) -> dict[str, Any]:
    """Serialize ``graph`` to a JSON-compatible dict."""
    nodes: list[dict[str, Any]] = []
    for node in graph.nodes.values():
        nodes.append(_node_to_dict(node))
    edges: list[dict[str, Any]] = []
    for edge in graph.edges.values():
        edges.append(_edge_to_dict(edge))
    return {"nodes": nodes, "edges": edges}


def from_json(d: dict[str, Any]) -> NodeGraph:
    """Build a :class:`NodeGraph` from a :func:`to_json` round-trip.

    Raises :class:`ValueError` for unknown node kinds / port types or
    missing required keys so the GUI can surface a clear error.
    """
    graph = NodeGraph()
    for raw_node in d.get("nodes", []):
        graph.add_node(_node_from_dict(raw_node))
    for raw_edge in d.get("edges", []):
        graph.add_edge(_edge_from_dict(graph, raw_edge))
    return graph


def _node_to_dict(node: Node) -> dict[str, Any]:
    return {
        "id": node.id,
        "kind": node.kind.value,
        "title": node.title,
        "inputs": [_port_to_dict(p) for p in node.inputs],
        "outputs": [_port_to_dict(p) for p in node.outputs],
        "params": dict(node.params),
        "position": [float(node.position[0]), float(node.position[1])],
    }


def _port_to_dict(port: Port) -> dict[str, Any]:
    return {
        "name": port.name,
        "type": port.type.value,
        "direction": port.direction,
        "label": port.label,
        "required": port.required,
    }


def _edge_to_dict(edge: Edge) -> dict[str, Any]:
    return {
        "id": edge.id,
        "src_node": edge.src_node,
        "src_port": edge.src_port,
        "dst_node": edge.dst_node,
        "dst_port": edge.dst_port,
    }


def _node_from_dict(raw: dict[str, Any]) -> Node:
    try:
        kind = NodeKind(raw["kind"])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"unknown node kind in template: {raw.get('kind')!r}") from exc
    return Node(
        id=str(raw["id"]),
        kind=kind,
        title=str(raw.get("title", kind.value)),
        inputs=tuple(_port_from_dict(p) for p in raw.get("inputs", [])),
        outputs=tuple(_port_from_dict(p) for p in raw.get("outputs", [])),
        params=dict(raw.get("params", {})),
        position=_position_from_raw(raw.get("position", (0.0, 0.0))),
    )


def _port_from_dict(raw: dict[str, Any]) -> Port:
    try:
        port_type = PortType(raw["type"])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"unknown port type in template: {raw.get('type')!r}") from exc
    return Port(
        name=str(raw["name"]),
        type=port_type,
        direction=str(raw.get("direction", "in")),
        label=str(raw.get("label", "")),
        required=bool(raw.get("required", False)),
    )


def _edge_from_dict(graph: NodeGraph, raw: dict[str, Any]) -> Edge:
    edge = Edge(
        id=str(raw.get("id", Edge.new_id())),
        src_node=str(raw["src_node"]),
        src_port=str(raw["src_port"]),
        dst_node=str(raw["dst_node"]),
        dst_port=str(raw["dst_port"]),
    )
    # Defensive: confirm both endpoints exist before insertion.
    if edge.src_node not in graph.nodes:
        raise ValueError(f"edge references missing src node: {edge.src_node}")
    if edge.dst_node not in graph.nodes:
        raise ValueError(f"edge references missing dst node: {edge.dst_node}")
    return edge


def _position_from_raw(raw: Any) -> tuple[float, float]:
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            return float(raw[0]), float(raw[1])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid node position: {raw!r}") from exc
    raise ValueError(f"invalid node position: {raw!r}")


# ── Undo / redo commands ───────────────────────────────────────────────


class _GraphMutation(QUndoCommand):
    """Base class that owns a back-reference to the scene/registry.

    Concrete subclasses implement :meth:`apply` and :meth:`revert`; the
    base provides a stable string identifier and a hook the scene can
    override for live registry sync.
    """

    def __init__(self, text: str) -> None:
        super().__init__(text)

    def apply(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def revert(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def redo(self) -> None:
        self.apply()

    def undo(self) -> None:
        self.revert()


class AddNodeCommand(_GraphMutation):
    """Add a pre-built :class:`Node` to the graph."""

    def __init__(self, graph: NodeGraph, node: Node) -> None:
        super().__init__(f"Add {node.kind.value}")
        self._graph = graph
        self._node = node

    def apply(self) -> None:
        # Tolerate redo() after the node was already present (e.g. caller
        # ran apply() once before pushing to the stack).
        if self._node.id not in self._graph.nodes:
            self._graph.add_node(self._node)

    def revert(self) -> None:
        if self._node.id in self._graph.nodes:
            self._graph.remove_node(self._node.id)


class RemoveNodeCommand(_GraphMutation):
    """Remove a node and cascade-remove every connected edge.

    The edges are snapshotted so an :meth:`apply` after a previous
    :meth:`revert` can re-attach the exact original edge set.
    """

    def __init__(self, graph: NodeGraph, node_id: str) -> None:
        super().__init__("Remove node")
        self._graph = graph
        self._node_id = node_id
        self._node_snapshot: Node | None = None
        self._edge_snapshots: list[Edge] = []

    def apply(self) -> None:
        node = self._graph.nodes.get(self._node_id)
        if node is None:
            return
        self._node_snapshot = node
        self._edge_snapshots = [
            edge for edge in self._graph.edges.values()
            if edge.src_node == self._node_id or edge.dst_node == self._node_id
        ]
        self._graph.remove_node(self._node_id)

    def revert(self) -> None:
        if self._node_snapshot is None:
            return
        self._graph.add_node(self._node_snapshot)
        for edge in self._edge_snapshots:
            self._graph.add_edge(edge)


class AddEdgeCommand(_GraphMutation):
    """Add an :class:`Edge` to the graph (idempotent on redo)."""

    def __init__(self, graph: NodeGraph, edge: Edge, *, allow_duplicate: bool = False) -> None:
        super().__init__("Connect ports")
        self._graph = graph
        self._edge = edge
        self._allow_duplicate = allow_duplicate

    def apply(self) -> None:
        if self._edge.id not in self._graph.edges:
            self._graph.add_edge(self._edge, allow_duplicate=self._allow_duplicate)

    def revert(self) -> None:
        if self._edge.id in self._graph.edges:
            self._graph.remove_edge(self._edge.id)


class RemoveEdgeCommand(_GraphMutation):
    """Remove an edge; remember it so undo can re-add the same id."""

    def __init__(self, graph: NodeGraph, edge_id: str) -> None:
        super().__init__("Disconnect")
        self._graph = graph
        self._edge_id = edge_id
        self._edge_snapshot: Edge | None = None

    def apply(self) -> None:
        edge = self._graph.edges.get(self._edge_id)
        if edge is None:
            return
        self._edge_snapshot = edge
        self._graph.remove_edge(self._edge_id)

    def revert(self) -> None:
        if self._edge_snapshot is None:
            return
        self._graph.add_edge(self._edge_snapshot)


class MoveNodeCommand(_GraphMutation):
    """Move a node to a new canvas position.

    The first move stores the original position; subsequent moves on
    the same node coalesce by replacing the destination only.
    """

    def __init__(
        self,
        graph: NodeGraph,
        node_id: str,
        new_position: tuple[float, float],
        *,
        old_position: tuple[float, float] | None = None,
    ) -> None:
        super().__init__("Move node")
        self._graph = graph
        self._node_id = node_id
        self._new_position = new_position
        self._old_position = old_position
        self._first_redo = old_position is None

    def apply(self) -> None:
        node = self._graph.nodes.get(self._node_id)
        if node is None:
            return
        if self._first_redo:
            self._old_position = node.position
            self._first_redo = False
        node.position = self._new_position

    def revert(self) -> None:
        if self._old_position is None:
            return
        node = self._graph.nodes.get(self._node_id)
        if node is None:
            return
        node.position = self._old_position


class SetParamsCommand(_GraphMutation):
    """Replace a node's ``params`` dict with a new value.

    Uses :func:`dataclasses.asdict`-style snapshotting so the original
    dict survives even if the live node later mutates it in place.
    """

    def __init__(self, graph: NodeGraph, node_id: str, new_params: dict[str, Any]) -> None:
        super().__init__("Edit parameters")
        self._graph = graph
        self._node_id = node_id
        self._new_params = _snapshot(new_params)
        self._old_params: dict[str, Any] | None = None
        self._first_redo = True

    def apply(self) -> None:
        node = self._graph.nodes.get(self._node_id)
        if node is None:
            return
        if self._first_redo:
            self._old_params = _snapshot(node.params)
            self._first_redo = False
        node.params = _snapshot(self._new_params)

    def revert(self) -> None:
        if self._old_params is None:
            return
        node = self._graph.nodes.get(self._node_id)
        if node is None:
            return
        node.params = _snapshot(self._old_params)


class ClearGraphCommand(_GraphMutation):
    """Remove every node (and cascade-remove every edge) from the graph."""

    def __init__(self, graph: NodeGraph) -> None:
        super().__init__("Clear graph")
        self._graph = graph
        self._node_snapshots: list[Node] = []
        self._edge_snapshots: list[Edge] = []

    def apply(self) -> None:
        self._node_snapshots = list(self._graph.nodes.values())
        self._edge_snapshots = list(self._graph.edges.values())
        # Iterate the snapshot list to avoid mutating during walk.
        for node in list(self._node_snapshots):
            self._graph.remove_node(node.id)

    def revert(self) -> None:
        for node in self._node_snapshots:
            if node.id not in self._graph.nodes:
                self._graph.add_node(node)
        for edge in self._edge_snapshots:
            if edge.id not in self._graph.edges:
                self._graph.add_edge(edge)


def _snapshot(value: Any) -> Any:
    """Defensive deep-enough copy for JSON-serializable params."""
    if isinstance(value, dict):
        return {k: _snapshot(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_snapshot(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_snapshot(v) for v in value)
    return value


__all__ = [
    "AddEdgeCommand",
    "AddNodeCommand",
    "ClearGraphCommand",
    "MoveNodeCommand",
    "RemoveEdgeCommand",
    "RemoveNodeCommand",
    "SetParamsCommand",
    "from_json",
    "to_json",
]
