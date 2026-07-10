"""Model-level tests for the :class:`NodeGraph` DAG support added in Phase 10.

These tests cover the model API directly, without Qt or the editor
scene, so the assertions stay fast and don't need an offscreen
``QApplication``.
"""
from __future__ import annotations

import pytest

from jobdesk_app.gui.nodegraph.model import (
    Edge,
    Node,
    NodeGraph,
    NodeKind,
    default_node,
)


def _make_fanout_graph() -> NodeGraph:
    """CONF_GEN (STRUCTURES) -> PRE_OPT_1 + PRE_OPT_2 -> OPT_FINAL.

    XYZ_FILE -> CONF_GEN keeps the confgen node's required ``in`` port
    satisfied; the fan-out happens on the two distinct PRE_OPT
    successors.
    """
    g = NodeGraph()
    xyz = default_node(NodeKind.XYZ_FILE, position=(0.0, 60.0))
    conf = default_node(NodeKind.CONF_GEN, position=(40.0, 60.0))
    conf.params = {"nconf": 3}
    pre1 = default_node(NodeKind.PRE_OPT, position=(220.0, 0.0))
    pre2 = default_node(NodeKind.PRE_OPT, position=(220.0, 120.0))
    opt = default_node(NodeKind.OPT, position=(400.0, 60.0))
    g.add_node(xyz)
    g.add_node(conf)
    g.add_node(pre1)
    g.add_node(pre2)
    g.add_node(opt)
    g.add_edge(Edge(id="e0", src_node=xyz.id, src_port="out",
                    dst_node=conf.id, dst_port="in"))
    g.add_edge(Edge(id="e1", src_node=conf.id, src_port="out",
                    dst_node=pre1.id, dst_port="in"))
    g.add_edge(Edge(id="e2", src_node=conf.id, src_port="out",
                    dst_node=pre2.id, dst_port="in"))
    g.add_edge(Edge(id="e3", src_node=pre1.id, src_port="out",
                    dst_node=opt.id, dst_port="in"))
    g.add_edge(Edge(id="e4", src_node=pre2.id, src_port="out",
                    dst_node=opt.id, dst_port="in"))
    return g


def _node_id_of_kind(graph: NodeGraph, kind: NodeKind) -> str:
    matches = [n.id for n in graph.nodes.values() if n.kind is kind]
    assert len(matches) >= 1, f"no node of kind {kind!r} in graph"
    return matches[0]


def test_validate_4_node_fanout_graph_returns_no_errors():
    g = _make_fanout_graph()
    issues = g.validate()
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], f"unexpected errors: {[e.message for e in errors]}"


def test_topological_order_respects_fan_out_partial_order():
    g = _make_fanout_graph()
    order_ids = [n.id for n in g.topological_order()]
    order_by_id = {nid: idx for idx, nid in enumerate(order_ids)}
    conf_id = _node_id_of_kind(g, NodeKind.CONF_GEN)
    pre_ids = [nid for nid, n in g.nodes.items() if n.kind is NodeKind.PRE_OPT]
    opt_id = _node_id_of_kind(g, NodeKind.OPT)
    # CONF_GEN must precede both PRE_OPT branches.
    for pre_id in pre_ids:
        assert order_by_id[conf_id] < order_by_id[pre_id]
    # Both PRE_OPTs must precede the final OPT.
    for pre_id in pre_ids:
        assert order_by_id[pre_id] < order_by_id[opt_id]


def test_add_edge_blocks_exact_4_tuple_duplicate():
    g = NodeGraph()
    a = default_node(NodeKind.OPT, position=(0, 0))
    b = default_node(NodeKind.SINGLE_POINT, position=(200, 0))
    g.add_node(a)
    g.add_node(b)
    g.add_edge(Edge(id="e1", src_node=a.id, src_port="out",
                    dst_node=b.id, dst_port="in"))
    # Same (src, src_port, dst, dst_port) tuple with a different id
    # must still be rejected.
    with pytest.raises(ValueError, match="duplicate"):
        g.add_edge(Edge(id="e2", src_node=a.id, src_port="out",
                        dst_node=b.id, dst_port="in"))


def test_add_edge_allows_fan_out_same_src_different_dst():
    g = NodeGraph()
    src = default_node(NodeKind.OPT, position=(0, 0))
    a = default_node(NodeKind.SINGLE_POINT, position=(200, 0))
    b = default_node(NodeKind.FREQUENCY, position=(400, 0))
    g.add_node(src)
    g.add_node(a)
    g.add_node(b)
    g.add_edge(Edge(id="e1", src_node=src.id, src_port="out",
                    dst_node=a.id, dst_port="in"))
    # Same source, same source port, different destination — allowed.
    g.add_edge(Edge(id="e2", src_node=src.id, src_port="out",
                    dst_node=b.id, dst_port="in"))
    assert len(g.edges) == 2


def test_add_edge_allows_allow_duplicate_kwarg():
    """Programmatic callers (test fixtures, undo replays) can opt in."""
    g = NodeGraph()
    a = default_node(NodeKind.OPT, position=(0, 0))
    b = default_node(NodeKind.SINGLE_POINT, position=(200, 0))
    g.add_node(a)
    g.add_node(b)
    g.add_edge(Edge(id="e1", src_node=a.id, src_port="out",
                    dst_node=b.id, dst_port="in"))
    g.add_edge(Edge(id="e2", src_node=a.id, src_port="out",
                    dst_node=b.id, dst_port="in"),
               allow_duplicate=True)
    assert len(g.edges) == 2
