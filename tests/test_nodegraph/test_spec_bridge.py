"""Round-trip tests for the NodeGraph <-> WorkflowSpec bridge (Phase 1.6).

These tests don't need a running QApplication — the bridge is
deliberately Qt-free. They cover:

* the canonical 5-step linear graph (XYZ_FILE -> CONF_GEN ->
  PRE_OPT -> OPT -> SINGLE_POINT -> OUTPUT) the wizard used to
  author by default;
* the empty / orphan / fan-out / cycle error paths;
* the YAML serialisation shape (steps, calc ``itask``, confgen
  ``type``);
* the inverse direction (YAML dict -> graph) producing an
  equivalent (but not byte-identical) graph;
* the ``Advanced`` node merging into ``global_config.calc``.
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
from jobdesk_app.gui.nodegraph.spec_bridge import (
    WorkflowGraphPayload,
    WorkflowSpecError,
    from_workflow_spec,
    to_workflow_spec,
)


# ── helpers ──────────────────────────────────────────────────────────────


def make_canonical_linear_graph() -> NodeGraph:
    """XYZ_FILE -> CONF_GEN -> PRE_OPT -> OPT -> SINGLE_POINT -> OUTPUT."""
    g = NodeGraph()
    xyz = default_node(NodeKind.XYZ_FILE, position=(40.0, 60.0))
    conf = default_node(NodeKind.CONF_GEN, position=(220.0, 60.0))
    conf.params = {"nconf": 5, "method": "ETKDG"}
    preopt = default_node(NodeKind.PRE_OPT, position=(400.0, 60.0))
    preopt.params = {"method": "GFN2", "basis": "XTB"}
    opt = default_node(NodeKind.OPT, position=(580.0, 60.0))
    opt.params = {"method": "B3LYP", "basis": "6-31G(d)"}
    sp = default_node(NodeKind.SINGLE_POINT, position=(760.0, 60.0))
    sp.params = {"method": "B3LYP", "basis": "6-311+G(d,p)"}
    out = default_node(NodeKind.OUTPUT, position=(940.0, 60.0))
    for n in (xyz, conf, preopt, opt, sp, out):
        g.add_node(n)
    g.add_edge(Edge(id="e1", src_node=xyz.id, src_port="out", dst_node=conf.id, dst_port="in"))
    g.add_edge(Edge(id="e2", src_node=conf.id, src_port="out", dst_node=preopt.id, dst_port="in"))
    g.add_edge(Edge(id="e3", src_node=preopt.id, src_port="out", dst_node=opt.id, dst_port="in"))
    g.add_edge(Edge(id="e4", src_node=opt.id, src_port="out", dst_node=sp.id, dst_port="in"))
    g.add_edge(Edge(id="e5", src_node=sp.id, src_port="out", dst_node=out.id, dst_port="in"))
    return g


# ── happy-path ───────────────────────────────────────────────────────────


def test_to_workflow_spec_canonical_linear():
    g = make_canonical_linear_graph()
    payload = to_workflow_spec(g)
    assert isinstance(payload, WorkflowGraphPayload)
    assert len(payload.steps) == 4  # confgen + 3 calc steps (XYZ/OUTPUT are sentinels)
    # confgen is first
    assert payload.steps[0]["type"] == "confgen"
    # calc steps follow, in topological order, with itask
    assert [s["params"]["itask"] for s in payload.steps[1:]] == ["preopt", "opt", "sp"]
    # all calc steps are type=calc
    for s in payload.steps[1:]:
        assert s["type"] == "calc"
    # method/basis from the first emitting calc step drive the global config
    spec_data = payload.spec.global_config.model_dump(mode="json", exclude_none=True)
    assert spec_data["calc"]["method"] == "GFN2"
    assert spec_data["calc"]["basis"] == "XTB"


def test_to_workflow_spec_yaml_round_trip_is_valid():
    g = make_canonical_linear_graph()
    payload = to_workflow_spec(g)
    yaml_text = payload.to_yaml()
    # Must contain the four steps in declaration order.
    assert "steps:" in yaml_text
    assert "name: confgen" in yaml_text
    assert "type: confgen" in yaml_text
    assert "itask: preopt" in yaml_text
    assert "itask: opt" in yaml_text
    assert "itask: sp" in yaml_text
    # The global config is present.
    assert "work_dir:" in yaml_text
    assert "calc:" in yaml_text
    # Round-trip through PyYAML: the file parses back into the same
    # number of steps in the same order.
    import yaml

    reparsed = yaml.safe_load(yaml_text)
    assert reparsed["steps"][0]["type"] == "confgen"
    assert [s["params"]["itask"] for s in reparsed["steps"][1:]] == ["preopt", "opt", "sp"]
    assert "calc" in reparsed


def test_to_workflow_spec_advanced_merges_into_extra_options():
    g = make_canonical_linear_graph()
    adv = default_node(NodeKind.ADVANCED, position=(40.0, 320.0))
    adv.params = {"solvent": "water", "nprocshared": 8, "nproc": 16}
    g.add_node(adv)
    payload = to_workflow_spec(g)
    spec_data = payload.spec.global_config.model_dump(mode="json", exclude_none=True)
    # nproc is promoted to the top level (it's a well-known key).
    assert spec_data["calc"]["nproc"] == 16
    # The remaining advanced keys survive in extra_options (as the
    # GlobalConfigModel ``calc`` payload).
    assert spec_data["calc"].get("solvent") == "water"
    assert spec_data["calc"].get("nprocshared") == 8


def test_from_workflow_spec_round_trip_recovers_step_set():
    g = make_canonical_linear_graph()
    payload = to_workflow_spec(g)
    rebuilt = from_workflow_spec(payload)
    # XYZ_FILE + 4 step nodes + OUTPUT = 6 nodes. Advanced isn't there
    # because we didn't add one.
    assert len(rebuilt.nodes) == 6
    kinds = sorted(n.kind.value for n in rebuilt.nodes.values())
    assert kinds == sorted([
        "xyz_file", "confgen", "preopt", "opt", "sp", "output",
    ])
    # The chain is fully wired: 5 edges.
    assert len(rebuilt.edges) == 5


def test_from_workflow_spec_accepts_raw_dict():
    raw = {
        "work_dir": "wd",
        "calc": {
            "program": "orca",
            "method": "B3LYP",
            "basis": "6-31G(d)",
            "charge": 0,
            "multiplicity": 1,
            "nproc": 4,
            "memory_mb": 1024,
        },
        "steps": [
            {"name": "opt", "type": "calc", "params": {"itask": "opt"}},
            {"name": "sp",  "type": "calc", "params": {"itask": "sp"}},
        ],
    }
    g = from_workflow_spec(raw)
    kinds = sorted(n.kind.value for n in g.nodes.values())
    # XYZ_FILE + opt + sp + OUTPUT = 4 nodes.
    assert kinds == ["opt", "output", "sp", "xyz_file"]
    assert len(g.edges) == 3  # xyz -> opt -> sp -> output


# ── error paths ──────────────────────────────────────────────────────────


def test_to_workflow_spec_rejects_empty_graph():
    with pytest.raises(WorkflowSpecError, match="empty"):
        to_workflow_spec(NodeGraph())


def test_to_workflow_spec_rejects_cycle():
    g = NodeGraph()
    a = default_node(NodeKind.OPT, position=(0, 0))
    b = default_node(NodeKind.SINGLE_POINT, position=(200, 0))
    g.add_node(a)
    g.add_node(b)
    g.add_edge(Edge(id="e1", src_node=a.id, src_port="out", dst_node=b.id, dst_port="in"))
    g.add_edge(Edge(id="e2", src_node=b.id, src_port="out", dst_node=a.id, dst_port="in"))
    with pytest.raises(WorkflowSpecError, match="cycle"):
        to_workflow_spec(g)


def test_to_workflow_spec_rejects_fan_out():
    """Two calc steps both wired to the same OPT successor is fan-in,
    not fan-out; the real fan-out we reject is a single OPT feeding
    two successors.
    """
    g = NodeGraph()
    xyz = default_node(NodeKind.XYZ_FILE, position=(0, 0))
    opt = default_node(NodeKind.OPT, position=(200, 0))
    a = default_node(NodeKind.SINGLE_POINT, position=(400, 0))
    b = default_node(NodeKind.FREQUENCY, position=(400, 100))
    g.add_node(xyz)
    g.add_node(opt)
    g.add_node(a)
    g.add_node(b)
    g.add_edge(Edge(id="e1", src_node=xyz.id, src_port="out", dst_node=opt.id, dst_port="in"))
    g.add_edge(Edge(id="e2", src_node=opt.id, src_port="out", dst_node=a.id, dst_port="in"))
    g.add_edge(Edge(id="e3", src_node=opt.id, src_port="out", dst_node=b.id, dst_port="in"))
    with pytest.raises(WorkflowSpecError, match="fans out"):
        to_workflow_spec(g)


def test_to_workflow_spec_rejects_fan_in():
    g = NodeGraph()
    xyz1 = default_node(NodeKind.XYZ_FILE, position=(0, 0))
    xyz2 = default_node(NodeKind.XYZ_FILE, position=(0, 100))
    opt = default_node(NodeKind.OPT, position=(200, 50))
    g.add_node(xyz1)
    g.add_node(xyz2)
    g.add_node(opt)
    g.add_edge(Edge(id="e1", src_node=xyz1.id, src_port="out", dst_node=opt.id, dst_port="in"))
    g.add_edge(Edge(id="e2", src_node=xyz2.id, src_port="out", dst_node=opt.id, dst_port="in"))
    with pytest.raises(WorkflowSpecError, match="incoming edges"):
        to_workflow_spec(g)


def test_to_workflow_spec_rejects_missing_required_input():
    g = NodeGraph()
    opt = default_node(NodeKind.OPT, position=(0, 0))
    g.add_node(opt)
    # OPT has a required ``in`` port; nothing wired to it.
    with pytest.raises(WorkflowSpecError, match="not well-formed"):
        to_workflow_spec(g)


def test_from_workflow_spec_rejects_unknown_step_type():
    bad = {
        "calc": {"program": "orca", "method": "B3LYP", "basis": "6-31G(d)"},
        "steps": [{"name": "bogus", "type": "smearium", "params": {}}],
    }
    with pytest.raises(WorkflowSpecError, match="unknown step type"):
        from_workflow_spec(bad)


# ── step-name uniqueness ─────────────────────────────────────────────────


def test_unique_step_names_avoid_collision():
    g = NodeGraph()
    xyz = default_node(NodeKind.XYZ_FILE, position=(0, 0))
    # Two unnamed OPT nodes default their title to "opt".
    opt1 = default_node(NodeKind.OPT, position=(200, 0))
    opt2 = default_node(NodeKind.OPT, position=(400, 0))
    out = default_node(NodeKind.OUTPUT, position=(600, 0))
    g.add_node(xyz)
    g.add_node(opt1)
    g.add_node(opt2)
    g.add_node(out)
    g.add_edge(Edge(id="e1", src_node=xyz.id, src_port="out", dst_node=opt1.id, dst_port="in"))
    g.add_edge(Edge(id="e2", src_node=opt1.id, src_port="out", dst_node=opt2.id, dst_port="in"))
    g.add_edge(Edge(id="e3", src_node=opt2.id, src_port="out", dst_node=out.id, dst_port="in"))
    payload = to_workflow_spec(g)
    names = [s["name"] for s in payload.steps]
    assert len(names) == 2
    assert names[0] != names[1]


def test_step_dict_has_minimal_keys():
    """Per-step dicts should keep their shape minimal and well-typed."""
    g = NodeGraph()
    xyz = default_node(NodeKind.XYZ_FILE, position=(0, 0))
    opt = default_node(NodeKind.OPT, position=(200, 0))
    opt.params = {"nproc": 8}
    out = default_node(NodeKind.OUTPUT, position=(400, 0))
    g.add_node(xyz)
    g.add_node(opt)
    g.add_node(out)
    g.add_edge(Edge(id="e1", src_node=xyz.id, src_port="out", dst_node=opt.id, dst_port="in"))
    g.add_edge(Edge(id="e2", src_node=opt.id, src_port="out", dst_node=out.id, dst_port="in"))
    payload = to_workflow_spec(g)
    [step] = payload.steps
    assert set(step.keys()) >= {"name", "type", "params"}
    assert step["type"] == "calc"
    assert step["params"]["itask"] == "opt"
    assert step["params"]["nproc"] == 8
