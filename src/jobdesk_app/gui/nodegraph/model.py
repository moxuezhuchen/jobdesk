"""Core dataclasses for the workflow node-graph editor.

These types are deliberately decoupled from Qt. They live in pure
Python so the round-trip with :class:`jobdesk_app.core.workflow_spec.WorkflowSpec`
can be unit-tested without a running ``QApplication``.

Concepts
--------

- :class:`PortType` is an enum of the data kinds that flow along an
  edge. We currently support ``STRUCTURE`` (one XYZ-like geometry),
  ``STRUCTURES`` (a list of geometries, e.g. from a conformer
  generator), ``ENERGY`` (single number with optional metadata) and
  ``CONFIG`` (a free-form key/value bundle, e.g. advanced options).
- :class:`NodeKind` enumerates the concrete node types we ship. The
  string values match confflow's ``type`` field where applicable.
- :class:`Port` describes an input or output socket on a node.
- :class:`Node` is a node in the graph; it owns its ports and an
  arbitrary JSON-serializable ``params`` dict.
- :class:`Edge` connects one output port of one node to one input port
  of another node.
- :class:`NodeGraph` is the graph itself: a set of nodes, a set of
  edges, plus topology-level helpers (validate, topological order,
  serialization to/from ``WorkflowSpec``).

Connection rules
----------------

The default :meth:`NodeGraph.validate` enforces:

- An edge's source ``PortType`` must equal the target's, unless the
  source is ``STRUCTURES`` and the target is ``STRUCTURE`` (we
  implicitly fan-out from a conformer ensemble to a single downstream
  optimizer, which picks the lowest-energy conformer). Anything else
  is flagged as an ``INVALID_PORT_TYPE`` issue.
- A node's required input ports must be connected. We do this by
  walking each node's ``Port.required`` flag and checking that at
  least one edge ends on it.
- The graph must be acyclic. We detect cycles with Kahn's algorithm.

Phase 1 ships only the **linear** subset: in confflow 1.0.10 every
step consumes exactly one predecessor's output. ``STRUCTURES`` →
``STRUCTURE`` is the only legal downcast, used by ``Refine`` which
must pick the lowest-energy conformer. Fan-out and fan-in are NOT
supported yet — those land in Phase 3.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator


class PortType(str, enum.Enum):
    """Kinds of payloads that flow along an edge."""

    STRUCTURE = "structure"
    STRUCTURES = "structures"  # noqa: SC200 - multi-conformer ensemble
    ENERGY = "energy"
    CONFIG = "config"
    ANY = "any"  # visual terminal only; used by OUTPUT


class NodeKind(str, enum.Enum):
    """Concrete node types shipped with the editor.

    String values mirror confflow's ``type`` field so that
    :func:`jobdesk_app.gui.nodegraph.to_workflow_spec` can serialize
    without a mapping table.
    """

    XYZ_FILE = "xyz_file"  # input node; carries a single STRUCTURE
    CONF_GEN = "confgen"  # confflow step type="confgen"
    PRE_OPT = "preopt"  # confflow calc step, itask=preopt
    OPT = "opt"  # confflow calc step, itask=opt
    REFINE = "refine"  # confflow calc step, itask=refine
    SINGLE_POINT = "sp"  # confflow calc step, itask=sp
    FREQUENCY = "freq"  # confflow calc step, itask=freq
    TS = "ts"  # confflow calc step, itask=ts
    ADVANCED = "advanced"  # arbitrary key=value overrides
    OUTPUT = "output"  # terminal node; emits workflow.yaml


@dataclass(frozen=True)
class Port:
    """A socket on a node.

    ``name`` is a stable identifier inside the node (e.g. ``"in"`` or
    ``"conf"``). ``label`` is the human-visible string used by the
    UI. ``required`` is only meaningful for input ports.
    """

    name: str
    type: PortType
    direction: str  # "in" or "out"
    label: str = ""
    required: bool = False

    def __post_init__(self) -> None:
        if self.direction not in {"in", "out"}:
            raise ValueError(f"port direction must be 'in' or 'out', got {self.direction!r}")


@dataclass
class Node:
    """A single node in the workflow graph.

    ``id`` is a UUID4 hex; the GUI may rename it for display but the
    dataclass only knows the machine identifier. ``position`` is the
    canvas coordinate in pixels — used by serialization so a saved
    template restores the same layout. ``params`` is free-form and
    interpreted by the property panel that owns this node type.
    """

    id: str
    kind: NodeKind
    title: str
    inputs: tuple[Port, ...]
    outputs: tuple[Port, ...]
    params: dict[str, Any] = field(default_factory=dict)
    position: tuple[float, float] = (0.0, 0.0)

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex


@dataclass
class Edge:
    """A directed edge from one node's output to another's input."""

    id: str
    src_node: str
    src_port: str
    dst_node: str
    dst_port: str

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex


# Topology-validation issue severities.
_SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2}


@dataclass(frozen=True)
class GraphIssue:
    """A single finding from :meth:`NodeGraph.validate`."""

    severity: str  # "info" | "warning" | "error"
    code: str
    message: str
    node_id: str | None = None

    def __lt__(self, other: "GraphIssue") -> bool:  # for sorting
        return _SEVERITY_ORDER.get(self.severity, 99) < _SEVERITY_ORDER.get(other.severity, 99)


def _default_ports(kind: NodeKind) -> tuple[tuple[Port, ...], tuple[Port, ...]]:
    """Return ``(inputs, outputs)`` for a given :class:`NodeKind`."""
    if kind is NodeKind.XYZ_FILE:
        return ((), (Port(name="out", type=PortType.STRUCTURE, direction="out", label="structure"),))
    if kind is NodeKind.CONF_GEN:
        return (
            (Port(name="in", type=PortType.STRUCTURE, direction="in", label="seed", required=True),),
            (Port(name="out", type=PortType.STRUCTURES, direction="out", label="ensemble"),),
        )
    if kind is NodeKind.PRE_OPT:
        return (
            (Port(name="in", type=PortType.STRUCTURE, direction="in", label="in", required=True),),
            (Port(name="out", type=PortType.STRUCTURE, direction="out", label="pre-opt"),),
        )
    if kind is NodeKind.OPT:
        return (
            (Port(name="in", type=PortType.STRUCTURE, direction="in", label="in", required=True),),
            (Port(name="out", type=PortType.STRUCTURE, direction="out", label="opt"),),
        )
    if kind is NodeKind.SINGLE_POINT:
        return (
            (Port(name="in", type=PortType.STRUCTURE, direction="in", label="in", required=True),),
            (Port(name="out", type=PortType.ENERGY, direction="out", label="E"),),
        )
    if kind is NodeKind.FREQUENCY:
        return (
            (Port(name="in", type=PortType.STRUCTURE, direction="in", label="in", required=True),),
            (Port(name="out", type=PortType.STRUCTURE, direction="out", label="vibs"),),
        )
    if kind is NodeKind.TS:
        return (
            (Port(name="in", type=PortType.STRUCTURE, direction="in", label="guess", required=True),),
            (Port(name="out", type=PortType.STRUCTURE, direction="out", label="ts"),),
        )
    if kind is NodeKind.REFINE:
        # Refine consumes a STRUCTURES ensemble (post-confgen) and a
        # STRUCTURE candidate (post-opt). We accept the ensemble on the
        # "ensemble" input; the opt result goes on "candidate".
        return (
            (
                Port(name="ensemble", type=PortType.STRUCTURES, direction="in", label="ensemble"),
                Port(name="candidate", type=PortType.STRUCTURE, direction="in", label="candidate", required=True),
            ),
            (Port(name="out", type=PortType.STRUCTURE, direction="out", label="refined"),),
        )
    if kind is NodeKind.ADVANCED:
        # An Advanced node produces a CONFIG bundle (arbitrary key/value
        # overrides). It has no inputs of its own; the property panel
        # authoring model populates ``params`` directly. The node is
        # still part of the graph so the user can see it; serialization
        # later walks it and merges ``params`` into the workflow's
        # ``extra_options``.
        return (
            (),
            (Port(name="out", type=PortType.CONFIG, direction="out", label="opts"),),
        )
    if kind is NodeKind.OUTPUT:
        # Output is a visual terminal.  Its wildcard input accepts a
        # structure or energy result without participating in workflow
        # YAML dependency generation.
        return ((Port(name="in", type=PortType.ANY, direction="in", label="result"),), ())
    raise ValueError(f"unknown node kind: {kind!r}")


def default_node(kind: NodeKind, *, position: tuple[float, float] = (0.0, 0.0)) -> Node:
    """Construct a freshly-defaulted :class:`Node` for ``kind``."""
    inputs, outputs = _default_ports(kind)
    return Node(
        id=Node.new_id(),
        kind=kind,
        title=kind.value,
        inputs=inputs,
        outputs=outputs,
        params={},
        position=position,
    )


@dataclass
class NodeGraph:
    """The workflow graph: a mutable collection of nodes + edges.

    Mutations go through the helper methods so the graph stays
    internally consistent (e.g. removing a node cascades to all its
    edges).
    """

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: dict[str, Edge] = field(default_factory=dict)

    # ── mutation helpers ────────────────────────────────────────────

    def add_node(self, node: Node) -> None:
        if node.id in self.nodes:
            raise ValueError(f"node id collision: {node.id}")
        self.nodes[node.id] = node

    def remove_node(self, node_id: str) -> None:
        if node_id not in self.nodes:
            raise KeyError(node_id)
        del self.nodes[node_id]
        # Cascade-remove edges that touched the node.
        for edge_id in [eid for eid, e in self.edges.items() if e.src_node == node_id or e.dst_node == node_id]:
            del self.edges[edge_id]

    def add_edge(
        self,
        edge: Edge,
        *,
        allow_duplicate: bool = False,
    ) -> None:
        """Insert ``edge`` into the graph.

        By default the bridge rejects exact 4-tuple duplicates
        (``(src_node, src_port, dst_node, dst_port)``) so the editor
        cannot accidentally draw the same wire twice. ``fan-out`` and
        ``fan-in`` are *intentionally* allowed — only the literal
        fourth-tuple collision is blocked.

        The optional ``allow_duplicate`` flag is for callers that
        know better (e.g. an undo stack replay): pass it to insert
        an edge even if a matching tuple already exists.
        """
        if edge.id in self.edges:
            raise ValueError(f"edge id collision: {edge.id}")
        if edge.src_node not in self.nodes:
            raise KeyError(f"edge src node missing: {edge.src_node}")
        if edge.dst_node not in self.nodes:
            raise KeyError(f"edge dst node missing: {edge.dst_node}")
        if not allow_duplicate:
            for existing in self.edges.values():
                if (
                    existing.src_node == edge.src_node
                    and existing.src_port == edge.src_port
                    and existing.dst_node == edge.dst_node
                    and existing.dst_port == edge.dst_port
                ):
                    raise ValueError(
                        f"edge {edge.id} is a duplicate of {existing.id} "
                        f"({edge.src_node}:{edge.src_port} -> "
                        f"{edge.dst_node}:{edge.dst_port})"
                    )
        self.edges[edge.id] = edge

    def remove_edge(self, edge_id: str) -> None:
        if edge_id not in self.edges:
            raise KeyError(edge_id)
        del self.edges[edge_id]

    # ── query helpers ───────────────────────────────────────────────

    def nodes_in(self) -> Iterator[Node]:
        return iter(self.nodes.values())

    def edges_in(self) -> Iterator[Edge]:
        return iter(self.edges.values())

    def incoming_edges(self, node_id: str, port_name: str | None = None) -> list[Edge]:
        result: list[Edge] = []
        for edge in self.edges.values():
            if edge.dst_node != node_id:
                continue
            if port_name is not None and edge.dst_port != port_name:
                continue
            result.append(edge)
        return result

    def outgoing_edges(self, node_id: str, port_name: str | None = None) -> list[Edge]:
        result: list[Edge] = []
        for edge in self.edges.values():
            if edge.src_node != node_id:
                continue
            if port_name is not None and edge.src_port != port_name:
                continue
            result.append(edge)
        return result

    # ── validation ──────────────────────────────────────────────────

    def validate(self) -> list[GraphIssue]:
        """Return every issue the topology has, sorted by severity."""
        issues: list[GraphIssue] = []
        issues.extend(self._check_required_inputs())
        issues.extend(self._check_port_types())
        issues.extend(self._check_cycles())
        issues.extend(self._check_orphans())
        issues.sort()
        return issues

    def _check_required_inputs(self) -> Iterable[GraphIssue]:
        for node in self.nodes.values():
            for port in node.inputs:
                if not port.required:
                    continue
                if not self.incoming_edges(node.id, port.name):
                    yield GraphIssue(
                        severity="error",
                        code="MISSING_REQUIRED_INPUT",
                        message=f"Node '{node.title}' is missing required input '{port.label or port.name}'.",
                        node_id=node.id,
                    )

    def _check_port_types(self) -> Iterable[GraphIssue]:
        for edge in self.edges.values():
            src = self.nodes[edge.src_node]
            dst = self.nodes[edge.dst_node]
            src_port = _find_port(src.outputs, edge.src_port)
            dst_port = _find_port(dst.inputs, edge.dst_port)
            if src_port is None or dst_port is None:
                yield GraphIssue(
                    severity="error",
                    code="UNKNOWN_PORT",
                    message=f"Edge {edge.id} references a port that does not exist on its node.",
                    node_id=edge.src_node,
                )
                continue
            if src_port.type is dst_port.type or dst_port.type is PortType.ANY:
                continue
            # Allow STRUCTURES -> STRUCTURE downcast (Refine picks the
            # best conformer from an ensemble).
            if src_port.type is PortType.STRUCTURES and dst_port.type is PortType.STRUCTURE:
                continue
            yield GraphIssue(
                severity="error",
                code="INVALID_PORT_TYPE",
                message=(
                    f"Edge from '{src.title}.{src_port.label}' "
                    f"({src_port.type.value}) to "
                    f"'{dst.title}.{dst_port.label}' "
                    f"({dst_port.type.value}) is not type-compatible."
                ),
                node_id=edge.src_node,
            )

    def _check_cycles(self) -> Iterable[GraphIssue]:
        # Kahn's algorithm: peel off nodes with indegree 0. If anything
        # remains at the end, that's part of a cycle.
        indeg: dict[str, int] = {nid: 0 for nid in self.nodes}
        for edge in self.edges.values():
            indeg[edge.dst_node] = indeg.get(edge.dst_node, 0) + 1
        queue = [nid for nid, d in indeg.items() if d == 0]
        removed = 0
        while queue:
            nid = queue.pop()
            removed += 1
            for edge in self.outgoing_edges(nid):
                indeg[edge.dst_node] -= 1
                if indeg[edge.dst_node] == 0:
                    queue.append(edge.dst_node)
        if removed < len(self.nodes):
            for nid in indeg:
                if indeg[nid] > 0:
                    yield GraphIssue(
                        severity="error",
                        code="CYCLE_DETECTED",
                        message=("Workflow contains a cycle. Phase 1 supports only linear (acyclic) workflows."),
                        node_id=nid,
                    )
                    break

    def _check_orphans(self) -> Iterable[GraphIssue]:
        # A warning, not an error: the user might be in the middle of
        # authoring. We only flag truly disconnected nodes (no
        # edges in, no edges out) that aren't input/output terminals.
        for node in self.nodes.values():
            if node.kind is NodeKind.XYZ_FILE or node.kind is NodeKind.OUTPUT:
                continue
            if not self.incoming_edges(node.id) and not self.outgoing_edges(node.id):
                yield GraphIssue(
                    severity="warning",
                    code="ORPHAN_NODE",
                    message=f"Node '{node.title}' is not connected to anything.",
                    node_id=node.id,
                )

    # ── topological order ───────────────────────────────────────────

    def topological_order(self) -> list[Node]:
        """Return nodes in topological order (sources first).

        Raises :class:`ValueError` if the graph contains a cycle.
        """
        if any(issue.code == "CYCLE_DETECTED" for issue in self._check_cycles()):
            raise ValueError("cannot topologically sort a graph with a cycle")
        visited: set[str] = set()
        order: list[Node] = []

        def visit(nid: str) -> None:
            if nid in visited:
                return
            visited.add(nid)
            for edge in self.incoming_edges(nid):
                visit(edge.src_node)
            order.append(self.nodes[nid])

        for nid in list(self.nodes):
            visit(nid)
        return order


def _find_port(ports: tuple[Port, ...], name: str) -> Port | None:
    for port in ports:
        if port.name == name:
            return port
    return None


__all__ = [
    "Edge",
    "GraphIssue",
    "Node",
    "NodeGraph",
    "NodeKind",
    "Port",
    "PortType",
    "default_node",
]
