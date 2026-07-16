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
    # v6: keyword lives inside the first calc step's ``params``,
    # not at the top level.
    raw = getattr(payload.spec, "_raw", None) or {}
    steps_list = raw.get("steps", []) if isinstance(raw, dict) else []
    first_calc = next(
        (s for s in steps_list
         if isinstance(s, dict) and s.get("type") == "calc"),
        {},
    )
    keyword = (first_calc.get("params") or {}).get("keyword") or ""
    assert "GFN2" in keyword
    assert "XTB" in keyword


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
    # Round-trip through PyYAML: the file parses back into the same
    # number of steps in the same order.
    import yaml

    reparsed = yaml.safe_load(yaml_text)
    assert reparsed["steps"][0]["type"] == "confgen"
    assert [s["params"]["itask"] for s in reparsed["steps"][1:]] == ["preopt", "opt", "sp"]


def test_to_workflow_spec_advanced_merges_into_extra_options():
    g = make_canonical_linear_graph()
    adv = default_node(NodeKind.ADVANCED, position=(40.0, 320.0))
    adv.params = {"solvent": "water", "nprocshared": 8, "nproc": 16}
    g.add_node(adv)
    payload = to_workflow_spec(g)
    spec_data = payload.spec.global_config.model_dump(mode="json", exclude_none=True)
    # v6: ``nproc`` from the advanced node is promoted to the
    # canonical confflow resource name ``cores_per_task``.
    assert spec_data.get("cores_per_task") == 16
    # The remaining advanced keys survive as flat top-level fields in
    # ``global_config``.
    assert spec_data.get("solvent") == "water"
    assert spec_data.get("nprocshared") == 8


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


def test_from_workflow_spec_raw_defaults_do_not_create_advanced_node():
    """Flat engine defaults are not editor ``ADVANCED`` options.

    The DAG submit path writes ``GlobalConfigModel.model_dump()`` as a flat
    YAML mapping.  Reloading that document must not materialise every
    engine-owned default as a visible free-form node.
    """
    raw = {
        "cores_per_task": 8,
        "total_memory": "4GB",
        "rmsd_threshold": 0.25,
        "energy_tolerance": 0.05,
        "noH": False,
        "ts_rescue_scan": False,
        "scan_coarse_step": 0.1,
        "scan_fine_step": 0.02,
        "scan_uphill_limit": 10,
        "ts_bond_drift_threshold": 0.4,
        "ts_rmsd_threshold": 1.0,
        "enable_dynamic_resources": False,
        "resume_from_backups": True,
        "stop_check_interval_seconds": 1,
        "force_consistency": False,
        "steps": [{"name": "opt", "type": "calc", "params": {"itask": "opt"}}],
    }

    graph = from_workflow_spec(raw)

    assert [n for n in graph.nodes.values() if n.kind is NodeKind.ADVANCED] == []


def test_from_workflow_spec_raw_unknown_global_option_becomes_advanced_node():
    """Unknown flat global keys remain editable as ``ADVANCED`` options."""
    raw = {
        "solvent": "water",
        "steps": [{"name": "opt", "type": "calc", "params": {"itask": "opt"}}],
    }

    graph = from_workflow_spec(raw)

    advanced = [n for n in graph.nodes.values() if n.kind is NodeKind.ADVANCED]
    assert len(advanced) == 1
    assert advanced[0].params == {"solvent": "water"}


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


def test_to_workflow_spec_accepts_fan_out():
    """One OPT feeding two STRUCTURE successors is fan-out and is now
    allowed by Phase 10. Both successors see ``"opt"`` in their
    ``inputs`` array.
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
    payload = to_workflow_spec(g)
    names = {s["name"]: s for s in payload.steps}
    opt_name = names["opt"]["name"]
    assert "sp" in names and "freq" in names
    assert names["sp"]["inputs"] == [opt_name]
    assert names["freq"]["inputs"] == [opt_name]


def test_to_workflow_spec_rejects_fan_in_to_structure_port():
    """Fan-in to a single ``STRUCTURE`` input port is still rejected.

    ``STRUCTURES`` ports are the only port type that can absorb
    multiple incoming edges (refine's ensemble socket). Two
    ``XYZ_FILE`` outputs feeding one OPT's required ``in`` port is
    therefore not allowed.
    """
    g = NodeGraph()
    xyz1 = default_node(NodeKind.XYZ_FILE, position=(0, 0))
    xyz2 = default_node(NodeKind.XYZ_FILE, position=(0, 100))
    opt = default_node(NodeKind.OPT, position=(200, 50))
    g.add_node(xyz1)
    g.add_node(xyz2)
    g.add_node(opt)
    g.add_edge(Edge(id="e1", src_node=xyz1.id, src_port="out", dst_node=opt.id, dst_port="in"))
    g.add_edge(Edge(id="e2", src_node=xyz2.id, src_port="out", dst_node=opt.id, dst_port="in"))
    with pytest.raises(WorkflowSpecError, match="at most one predecessor"):
        to_workflow_spec(g)


def test_to_workflow_spec_root_step_has_empty_inputs():
    """The first step in the chain has no upstream steps, so its
    ``inputs`` field is the empty list.
    """
    g = make_canonical_linear_graph()
    payload = to_workflow_spec(g)
    # The first emitting step in canonical order is CONF_GEN.
    assert payload.steps[0]["name"] == "confgen"
    assert payload.steps[0]["inputs"] == []
    # Every later step must name its immediate predecessor.
    expected_pred = "confgen"
    for step in payload.steps[1:]:
        assert step["inputs"] == [expected_pred], step
        expected_pred = step["name"]


def test_to_workflow_spec_diamond_pattern_preserves_partial_order():
    """A four-node emitter chain produces a stable ``inputs`` ordering
    even when both parallel branches meet at a ``STRUCTURES`` input.

    The sink here is a REFINE node whose ``ensemble`` port is the
    one port type that absorbs multiple predecessors (two CONF_GEN
    branches that each emit a STRUCTURES payload). The REFINE also
    needs its required ``candidate`` STRUCTURE port filled; we
    attach XYZ_FILE directly to it so the graph is well-formed.
    """
    g = NodeGraph()
    xyz = default_node(NodeKind.XYZ_FILE, position=(0, 0))
    conf1 = default_node(NodeKind.CONF_GEN, position=(200, 0))
    conf1.params = {"nconf": 3}
    conf2 = default_node(NodeKind.CONF_GEN, position=(200, 200))
    conf2.params = {"nconf": 5}
    refine = default_node(NodeKind.REFINE, position=(500, 100))
    out = default_node(NodeKind.OUTPUT, position=(700, 100))
    g.add_node(xyz)
    g.add_node(conf1)
    g.add_node(conf2)
    g.add_node(refine)
    g.add_node(out)
    # XYZ_FILE feeds both confgen seeds and refine's candidate port.
    g.add_edge(Edge(id="e1", src_node=xyz.id, src_port="out",
                    dst_node=conf1.id, dst_port="in"))
    g.add_edge(Edge(id="e2", src_node=xyz.id, src_port="out",
                    dst_node=conf2.id, dst_port="in"))
    g.add_edge(Edge(id="e3", src_node=xyz.id, src_port="out",
                    dst_node=refine.id, dst_port="candidate"))
    # Two parallel STRUCTURES sources fan into refine.ensemble.
    g.add_edge(Edge(id="e4", src_node=conf1.id, src_port="out",
                    dst_node=refine.id, dst_port="ensemble"))
    g.add_edge(Edge(id="e5", src_node=conf2.id, src_port="out",
                    dst_node=refine.id, dst_port="ensemble"))
    payload = to_workflow_spec(g)
    # Both confgen roots have empty inputs.
    conf_steps = [s for s in payload.steps if s["type"] == "confgen"]
    assert all(s["inputs"] == [] for s in conf_steps)
    # The refine step must name both confgens (sorted for stability).
    refine_step = next(
        s for s in payload.steps
        if s.get("params", {}).get("itask") == "refine"
    )
    conf_names = sorted(s["name"] for s in conf_steps)
    assert refine_step["inputs"] == conf_names


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


# ── Phase 10.4: from -> to -> from byte-identical YAML round-trip ───────


def _make_fanout_graph() -> NodeGraph:
    """A 4-node graph: XYZ_FILE -> CONF_GEN -> {SP, FREQ} -> OUTPUT.

    Both SP and FREQ get CONF_GEN as their single predecessor, so the
    reconstructed graph is a small fan-out / fan-in pair.
    """
    g = NodeGraph()
    xyz = default_node(NodeKind.XYZ_FILE, position=(40.0, 60.0))
    conf = default_node(NodeKind.CONF_GEN, position=(220.0, 60.0))
    conf.params = {"nconf": 5}
    sp = default_node(NodeKind.SINGLE_POINT, position=(400.0, 0.0))
    sp.params = {"method": "B3LYP", "basis": "6-31G(d)"}
    freq = default_node(NodeKind.FREQUENCY, position=(400.0, 120.0))
    out = default_node(NodeKind.OUTPUT, position=(620.0, 60.0))
    g.add_node(xyz)
    g.add_node(conf)
    g.add_node(sp)
    g.add_node(freq)
    g.add_node(out)
    g.add_edge(Edge(id="e1", src_node=xyz.id, src_port="out",
                    dst_node=conf.id, dst_port="in"))
    g.add_edge(Edge(id="e2", src_node=conf.id, src_port="out",
                    dst_node=sp.id, dst_port="in"))
    g.add_edge(Edge(id="e3", src_node=conf.id, src_port="out",
                    dst_node=freq.id, dst_port="in"))
    return g


def test_from_workflow_spec_round_trips_fanout():
    """serialize -> deserialize -> serialize byte-identical."""
    original = _make_fanout_graph()
    payload1 = to_workflow_spec(original)
    yaml1 = payload1.to_yaml()

    rebuilt = from_workflow_spec(payload1)
    payload2 = to_workflow_spec(rebuilt)
    yaml2 = payload2.to_yaml()
    # The YAML output is byte-identical because every step name and
    # ``inputs`` list survives the round-trip. ``rebuilt.validate()``
    # can legitimately flag the synthetic ``OUTPUT`` sentinel edges
    # (``OUTPUT`` has no input ports by design, see ``NodeKind.OUTPUT``
    # in ``model.py``).
    assert yaml1 == yaml2


def test_from_workflow_spec_rebuilt_topology_matches_original():
    """The rebuilt graph has the same set of step names and inputs."""
    original = _make_fanout_graph()
    payload = to_workflow_spec(original)
    rebuilt = from_workflow_spec(payload)

    # Step names must match.
    original_names = sorted(s["name"] for s in payload.steps)
    rebuilt_step_pairs = [
        (n.kind.value, n.title)
        for n in rebuilt.nodes.values()
        if n.kind is not NodeKind.XYZ_FILE and n.kind is not NodeKind.OUTPUT
    ]
    rebuilt_titles = sorted(title for _kind, title in rebuilt_step_pairs)
    assert rebuilt_titles == original_names


def test_from_workflow_spec_recovers_fanout_topology():
    """The rebuilt graph must have a SP and FREQ both wired from CONF_GEN."""
    original = _make_fanout_graph()
    payload = to_workflow_spec(original)
    rebuilt = from_workflow_spec(payload)

    # Build a name -> node-id map for the calc nodes only.
    by_name: dict[str, str] = {
        n.title: n.id
        for n in rebuilt.nodes.values()
        if n.kind in _STEP_EMITTING_KINDS_FOR_TEST
    }
    assert set(by_name) == {"confgen", "sp", "freq"}
    # SP's only predecessor (a calc step) must be CONF_GEN.
    sp_in = [e.src_node for e in rebuilt.edges.values()
             if e.dst_node == by_name["sp"]]
    assert sp_in == [by_name["confgen"]]
    freq_in = [e.src_node for e in rebuilt.edges.values()
               if e.dst_node == by_name["freq"]]
    assert freq_in == [by_name["confgen"]]
    # Both sinks feed OUTPUT.
    out_id = next(
        n.id for n in rebuilt.nodes.values() if n.kind is NodeKind.OUTPUT
    )
    sp_to_out = any(
        e.src_node == by_name["sp"] and e.dst_node == out_id
        for e in rebuilt.edges.values()
    )
    freq_to_out = any(
        e.src_node == by_name["freq"] and e.dst_node == out_id
        for e in rebuilt.edges.values()
    )
    assert sp_to_out and freq_to_out


def test_from_workflow_spec_handles_empty_steps():
    """No steps produces just XYZ_FILE + OUTPUT sentinels with no edges."""
    graph = from_workflow_spec({"calc": {"program": "orca"}, "steps": []})
    kinds = sorted(n.kind.value for n in graph.nodes.values())
    assert kinds == ["output", "xyz_file"]
    assert graph.edges == {}


# Imported here so the round-trip tests above can use a private list of
# the kinds that emit a step. (Kept private to avoid polluting the
# module's ``__all__``.)
_STEP_EMITTING_KINDS_FOR_TEST = frozenset({
    NodeKind.CONF_GEN,
    NodeKind.PRE_OPT,
    NodeKind.OPT,
    NodeKind.SINGLE_POINT,
    NodeKind.FREQUENCY,
    NodeKind.TS,
    NodeKind.REFINE,
})


# --- Phase 10.5: SubmitPayload dag kind drop-in test ----------------------


def test_dag_kind_serialize_deserialize_round_trip():
    """The Phase 10.5 submit path consumes ``payload.steps`` verbatim.

    The bridge's ``to_yaml()`` output is the YAML ``spec_bridge`` hands
    to the submit use case.  Round-tripping through ``from_workflow_spec``
    must keep every step name and ``inputs`` entry intact, so the
    editor → bridge → use case plumbing is faithful.
    """
    original = _make_fanout_graph()
    payload1 = to_workflow_spec(original)
    # DAG payloads have non-empty ``inputs`` on at least one step; this is
    # the contract the SubmitPage auto-detect uses to choose kind="dag".
    has_dag_input = any(bool(s.get("inputs")) for s in payload1.steps)
    assert has_dag_input, "fan-out fixture must exercise non-empty inputs"

    rebuilt = from_workflow_spec(payload1)
    payload2 = to_workflow_spec(rebuilt)
    # The 4-tuple round trip in test_from_workflow_spec_round_trips_fanout
    # is byte-identical; the DAG kind piggy-backs on the same fixture.
    assert payload1.to_yaml() == payload2.to_yaml()


def test_dag_payload_has_non_empty_inputs_after_round_trip():
    """Round-tripping a DAG must preserve the ``inputs`` list on every sink."""
    original = _make_fanout_graph()
    spec_payload = to_workflow_spec(original)
    rebuilt = from_workflow_spec(spec_payload)
    payload_after = to_workflow_spec(rebuilt)
    by_name = {step["name"]: step for step in payload_after.steps}
    assert by_name["sp"]["inputs"] == ["confgen"]
    assert by_name["freq"]["inputs"] == ["confgen"]


def test_dag_payload_dag_step_count_is_stable():
    """A round-tripped DAG keeps the same number of emitting steps.

    The Phase 10.5 submit path's use case treats ``steps`` as a list of
    already-serialised dicts, so step-count stability is the most important
    invariant: the YAML the engine sees must match step-for-step what the
    editor produced.
    """
    original = _make_fanout_graph()
    payload_before = to_workflow_spec(original)
    rebuilt = from_workflow_spec(payload_before)
    payload_after = to_workflow_spec(rebuilt)
    assert len(payload_before.steps) == len(payload_after.steps)


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
