"""Round-trip ``to_json`` / ``from_json`` on a 3-node linear graph.

We compare field-by-field rather than relying on ``__eq__`` because
:class:`NodeGraph` is a mutable container that doesn't override
equality. The round-trip must preserve ``id``, ``kind``, port shapes,
``params``, ``position``, and edge endpoints.
"""

from __future__ import annotations

from jobdesk_app.gui.nodegraph.model import (
    Edge,
    NodeGraph,
    NodeKind,
    default_node,
)
from jobdesk_app.gui.nodegraph.serialization import from_json, to_json


def test_round_trip_3_node_linear_graph():
    graph = NodeGraph()
    xyz = default_node(NodeKind.XYZ_FILE, position=(10.0, 20.0))
    opt = default_node(NodeKind.OPT, position=(200.0, 30.0))
    opt.params = {"method": "B3LYP", "basis": "6-31G(d)", "nproc": 4}
    out = default_node(NodeKind.OUTPUT, position=(400.0, 40.0))
    graph.add_node(xyz)
    graph.add_node(opt)
    graph.add_node(out)
    graph.add_edge(Edge(id="e1", src_node=xyz.id, src_port="out", dst_node=opt.id, dst_port="in"))

    payload = to_json(graph)
    assert "nodes" in payload and "edges" in payload
    assert len(payload["nodes"]) == 3
    assert len(payload["edges"]) == 1

    rebuilt = from_json(payload)
    assert set(rebuilt.nodes) == set(graph.nodes)
    assert set(rebuilt.edges) == set(graph.edges)

    rebuilt_opt = rebuilt.nodes[opt.id]
    assert rebuilt_opt.kind is NodeKind.OPT
    assert rebuilt_opt.params == {"method": "B3LYP", "basis": "6-31G(d)", "nproc": 4}
    assert rebuilt_opt.position == (200.0, 30.0)

    edge = rebuilt.edges["e1"]
    assert (edge.src_node, edge.src_port, edge.dst_node, edge.dst_port) == (
        xyz.id,
        "out",
        opt.id,
        "in",
    )


def test_round_trip_empty_graph():
    rebuilt = from_json(to_json(NodeGraph()))
    assert rebuilt.nodes == {}
    assert rebuilt.edges == {}


def test_from_json_rejects_unknown_kind():
    bad = {
        "nodes": [
            {
                "id": "x",
                "kind": "nonsense",
                "title": "x",
                "inputs": [],
                "outputs": [],
                "params": {},
                "position": [0, 0],
            },
        ],
        "edges": [],
    }
    import pytest

    with pytest.raises(ValueError, match="unknown node kind"):
        from_json(bad)


def test_to_json_uses_serializable_types():
    """Make sure every value in the payload is JSON-native."""
    import json

    g = NodeGraph()
    g.add_node(default_node(NodeKind.OPT, position=(0, 0)))
    payload = to_json(g)
    encoded = json.dumps(payload)
    assert isinstance(encoded, str)
