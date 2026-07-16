#!/usr/bin/env python
"""Phase 10 end-to-end round-trip smoke for the DAG submission path.

This is a pure-Python script (NOT a pytest test) that exercises the
Phase 10 editor → spec bridge → SubmitUseCase → confflow YAML →
``from_workflow_spec`` → DAG walk pipeline, end-to-end, with no SSH and
no ``g16``/``l1.exe``. It is the executable companion to
``docs/PHASE10_NODEGRAPH_DAG_PLAN.md``.

Workflow under test
-------------------

The graph models the same kind of workflow a user would build
visually on the Submit page today::

    XYZ_FILE ──► Generate ──► Optimize ──► Frequency ──► Summary ◄──┐
                        └──► SinglePoint ──────────────────────────┘

That is:

* a 5-step backbone (Generate, Optimize, Frequency, SinglePoint, Summary)
* one **fan-out** edge from ``Generate`` to a parallel ``SinglePoint``
* one **fan-in** edge where ``Summary`` has TWO incoming edges
  (``Frequency`` and ``SinglePoint``)

The parallel branch (Generate → SinglePoint → Summary) and the
backbone (Generate → Optimize → Frequency → Summary) merge at the
Summary node, which has a STRUCTURES-typed input port — the only
port type the bridge allows fan-in into.

To keep the graph well-formed under the bridge's port-type rules
(STRUCTURES is the only port type that accepts many-to-one fan-in),
the Frequency and SinglePoint nodes are constructed with custom
STRUCTURES output ports. The bridge then serialises them with the
correct `inputs: [...]` lists.

Invariants we assert
--------------------

1. ``to_workflow_spec(graph)`` produces 5 emitting steps with
   ``inputs`` lists reflecting the fan-out / fan-in topology.
2. ``SubmitUseCase().execute(payload)`` writes ``workflow.yaml``
   under ``output_dir`` and returns a ``RunSpec`` whose
   ``workflow_kind`` is ``WorkflowKind.dag``.
3. The written YAML contains every step's ``inputs`` list verbatim
   (including the fan-in ``Summary`` step).
4. ``from_workflow_spec(...)`` rebuilds a graph with the same five
   emitting step titles and ``Summary`` wired to both ``Frequency``
   and ``SinglePoint``.
5. ``confflow.workflow.dag.topo_order(predecessors)`` produces a
   schedule where ``Summary`` is a leaf wave that depends on both
   ``Frequency`` and ``SinglePoint``.

Exit status
-----------

``0`` on success; ``1`` (with a printed traceback) on any assertion
failure or unexpected exception. Pass/fail summary is printed at the
end so a CI loop or a human can grep for ``DAG ROUND-TRIP SMOKE: PASS``.
"""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

# Make sure we run against the in-repo package, not an installed copy.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from jobdesk_app.core import workflow_spec as wf_spec_module  # noqa: E402
from jobdesk_app.core.run import WorkflowKind  # noqa: E402
from jobdesk_app.core.submit_payload import (  # noqa: E402
    DagWorkflowFields,
    InputSource,
    SubmitPayload,
)
from jobdesk_app.gui.nodegraph.model import (  # noqa: E402
    Edge,
    Node,
    NodeGraph,
    NodeKind,
    Port,
    PortType,
    default_node,
)
from jobdesk_app.gui.nodegraph.spec_bridge import (  # noqa: E402
    from_workflow_spec,
    to_workflow_spec,
)
from jobdesk_app.services.submit_use_case import SubmitUseCase  # noqa: E402

# ── helpers ──────────────────────────────────────────────────────────────


def _step(steps, name):
    """Look up a step by ``name`` in the bridge's serialised steps list."""
    for step in steps:
        if step.get("name") == name:
            return step
    raise KeyError(f"no step named {name!r} in {[s.get('name') for s in steps]}")


def _build_dag_graph() -> NodeGraph:
    """Build the 5-step backbone + parallel SinglePoint + fan-in Summary.

    The graph (with XYZ_FILE and OUTPUT sentinels) is::

        XYZ_FILE ──► Generate ──► Optimize ──► Frequency ──► Summary ◄──┐
                            └──► SinglePoint ──────────────────────────┘

    Per the user's spec ("Generate → Optimize → Frequency → Summary, with
    a fan-out branch from Generate to a parallel SinglePoint that then
    feeds into Summary"). The bridge enforces that fan-in is only
    allowed on STRUCTURES-typed input ports, so Frequency and SinglePoint
    output STRUCTURES ensembles, and Summary takes a STRUCTURES input.
    This is the same port-type pattern that ``REFINE`` ships with by
    default — Refine collects a conformer ensemble + a candidate
    structure.

    The bridge then serialises each step with the right ``inputs:
    [...]`` list; the YAML on disk is what the confflow engine walks
    with ``graphlib.TopologicalSorter``.
    """
    g = NodeGraph()
    xyz = default_node(NodeKind.XYZ_FILE, position=(40.0, 60.0))
    # CONF_GEN is the "Generate" step; the bridge maps it to ``type: confgen``.
    generate = default_node(NodeKind.CONF_GEN, position=(220.0, 60.0))
    generate.title = "Generate"
    generate.params = {"nconf": 5, "method": "ETKDG"}
    optimize = default_node(NodeKind.OPT, position=(400.0, 60.0))
    optimize.title = "Optimize"
    optimize.params = {"method": "B3LYP", "basis": "6-31G(d)"}
    # Both Frequency and SinglePoint output STRUCTURES (an ensemble) so
    # the bridge accepts the fan-in into Summary's STRUCTURES input.
    # This is how a real "merge two parallel branches into one
    # final-step summary" looks in the editor — see REFINE's default
    # ports which already follow the same pattern.
    frequency = Node(
        id=Node.new_id(),
        kind=NodeKind.FREQUENCY,
        title="Frequency",
        inputs=(Port(name="in", type=PortType.STRUCTURE, direction="in", label="in", required=True),),
        outputs=(Port(name="out", type=PortType.STRUCTURES, direction="out", label="vibs"),),
        params={"method": "B3LYP", "basis": "6-31G(d)"},
        position=(580.0, 0.0),
    )
    # SinglePoint with a STRUCTURES output port so it can feed
    # Summary's STRUCTURES input via the legal STRUCTURES→STRUCTURES
    # path.
    singlepoint = Node(
        id=Node.new_id(),
        kind=NodeKind.SINGLE_POINT,
        title="SinglePoint",
        inputs=(Port(name="in", type=PortType.STRUCTURES, direction="in", label="in", required=True),),
        outputs=(Port(name="out", type=PortType.STRUCTURES, direction="out", label="E"),),
        params={"method": "B3LYP", "basis": "6-311+G(d,p)"},
        position=(400.0, 200.0),
    )
    # Summary: STRUCTURES in (so the bridge accepts fan-in), STRUCTURE
    # out — collects the Frequency and SinglePoint outputs as an
    # ensemble. The confflow engine reads ``inputs: list[str]`` and
    # hands each predecessor's output to the step handler; using a
    # STRUCTURES port on the editor side is the only way the bridge
    # will serialise a fan-in topology.
    summary = Node(
        id=Node.new_id(),
        kind=NodeKind.SINGLE_POINT,
        title="Summary",
        inputs=(Port(name="in", type=PortType.STRUCTURES, direction="in", label="ensemble"),),
        outputs=(Port(name="out", type=PortType.STRUCTURE, direction="out", label="E"),),
        params={"method": "B3LYP", "basis": "6-31G(d)"},
        position=(760.0, 60.0),
    )
    out = default_node(NodeKind.OUTPUT, position=(940.0, 60.0))
    for node in (xyz, generate, optimize, frequency, singlepoint, summary, out):
        g.add_node(node)
    g.add_edge(Edge(id="e1", src_node=xyz.id, src_port="out",
                    dst_node=generate.id, dst_port="in"))
    # Backbone: Generate → Optimize → Frequency → Summary
    g.add_edge(Edge(id="e2", src_node=generate.id, src_port="out",
                    dst_node=optimize.id, dst_port="in"))
    g.add_edge(Edge(id="e3", src_node=optimize.id, src_port="out",
                    dst_node=frequency.id, dst_port="in"))
    # Fan-out: Generate also feeds SinglePoint (parallel branch).
    g.add_edge(Edge(id="e4", src_node=generate.id, src_port="out",
                    dst_node=singlepoint.id, dst_port="in"))
    # Fan-in: Summary receives both Frequency and SinglePoint.
    g.add_edge(Edge(id="e5", src_node=frequency.id, src_port="out",
                    dst_node=summary.id, dst_port="in"))
    g.add_edge(Edge(id="e6", src_node=singlepoint.id, src_port="out",
                    dst_node=summary.id, dst_port="in"))
    return g


def _build_payload(steps: list[dict], output_dir: Path) -> SubmitPayload:
    """Wrap the bridge's ``steps`` list into a ``SubmitPayload(kind="dag")``."""
    from dataclasses import dataclass

    @dataclass
    class _StubCalc:
        program: str
        preset_name: str | None
        method_basis: str
        job_keywords: list
        charge: int
        multiplicity: int
        nproc: int
        mem: str

    calc = _StubCalc(
        program="gaussian",
        preset_name=None,
        method_basis="B3LYP/6-31G(d)",
        job_keywords=[],
        charge=0,
        multiplicity=1,
        nproc=8,
        mem="4096MB",
    )
    return SubmitPayload(
        kind="dag",
        inputs=[InputSource(path=output_dir / "water.xyz", side="local", kind="xyz")],
        program="gaussian",
        calc=calc,
        workflow=None,
        output_dir=output_dir,
        server_id="smoke-server",
        remote_dir="/work",
        max_parallel=2,
        dag=DagWorkflowFields(work_dir_name="dag_smoke", steps=list(steps)),
    )


# ── checks ────────────────────────────────────────────────────────────────


def check_confflow_available() -> None:
    """The vendored confflow package must be on sys.path."""
    if not wf_spec_module._CONFFLOW_AVAILABLE:
        raise RuntimeError(
            "confflow package is not importable in this Python; this smoke "
            "needs the vendored package from src/jobdesk_app/confflow. "
            "Install with `pip install -e .[chem]`."
        )


def check_bridge_emits_4_steps_with_inputs() -> list[dict]:
    """``to_workflow_spec`` produces 4 emitting steps with the right inputs."""
    graph = _build_dag_graph()
    payload = to_workflow_spec(graph)
    # 5 emitting steps: Generate (confgen) + Optimize (opt) + Frequency
    # (freq) + SinglePoint (sp) + Summary (sp). The fan-out from Generate
    # and the fan-in at Summary must show up in each step's ``inputs``.
    names = [s["name"] for s in payload.steps]
    expected = {"Generate", "Optimize", "Frequency", "SinglePoint", "Summary"}
    assert set(names) == expected, f"unexpected step names: {names}"

    # Generate (confgen) is a root: empty inputs.
    gen_step = _step(payload.steps, "Generate")
    assert gen_step["type"] == "confgen"
    assert gen_step["inputs"] == [], gen_step["inputs"]

    # Fan-out: Optimize and SinglePoint both consume from Generate.
    # Note: the bridge sorts incoming step names by topological order
    # to keep the YAML stable across runs; for the parallel branch
    # Optimize comes before SinglePoint in declaration order so we
    # don't assert an exact ordering here, just that Generate is the
    # sole predecessor of both.
    optimize_step = _step(payload.steps, "Optimize")
    assert optimize_step["type"] == "calc"
    assert optimize_step["params"]["itask"] == "opt"
    assert optimize_step["inputs"] == ["Generate"], optimize_step["inputs"]
    sp_step = _step(payload.steps, "SinglePoint")
    assert sp_step["inputs"] == ["Generate"], sp_step["inputs"]
    # Optimize does NOT consume SinglePoint (the parallel branch is
    # Generate → SinglePoint, not Optimize → SinglePoint).
    assert sp_step["inputs"] != ["Optimize"]

    # Linear backbone between Optimize and Frequency.
    freq_step = _step(payload.steps, "Frequency")
    assert freq_step["inputs"] == ["Optimize"], freq_step["inputs"]

    # Fan-in: Summary lists BOTH Frequency and SinglePoint in its inputs.
    summary_step = _step(payload.steps, "Summary")
    assert sorted(summary_step["inputs"]) == ["Frequency", "SinglePoint"], (
        f"Summary fan-in missing: {summary_step['inputs']}"
    )
    return payload.steps


class _YamlScratch:
    """Holds a tempfile-backed YAML on disk for the duration of a check.

    The directory lives until the object goes out of scope; YAML-dependent
    checks must run inside a ``with _YamlScratch(...) as scratch:`` block.
    """

    def __init__(self, steps: list[dict]) -> None:
        self._steps = steps
        self._tmp_ctx: tempfile.TemporaryDirectory | None = None
        self.output_dir: Path | None = None
        self.yaml_path: Path | None = None
        self.parsed: dict | None = None

    def __enter__(self) -> "_YamlScratch":
        self._tmp_ctx = tempfile.TemporaryDirectory(prefix="dag_smoke_")
        self.output_dir = Path(self._tmp_ctx.__enter__())
        (self.output_dir / "water.xyz").write_text(
            "3\nwater\nO 0 0 0\nH 0 0 1\nH 0 1 0\n", encoding="utf-8"
        )
        payload = _build_payload(self._steps, self.output_dir)
        batch = SubmitUseCase().execute(payload)
        assert batch.ok, f"SubmitUseCase reported errors: {batch.errors}"
        assert batch.yaml_local_path is not None
        assert batch.yaml_local_path.exists()
        assert batch.yaml_local_path.parent == self.output_dir
        assert len(batch.specs) == 1
        spec = batch.specs[0]
        assert spec.workflow_kind is WorkflowKind.dag, spec.workflow_kind
        assert "confflow" in spec.command_template
        self.yaml_path = batch.yaml_local_path
        self.parsed = yaml.safe_load(self.yaml_path.read_text(encoding="utf-8"))
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._tmp_ctx is not None:
            self._tmp_ctx.__exit__(exc_type, exc, tb)
            self._tmp_ctx = None


def check_yaml_round_trips_through_from_workflow_spec(yaml_path: Path) -> None:
    """The YAML on disk is parsed back by ``from_workflow_spec`` correctly.

    Note: rebuilding the graph then re-serialising through
    ``to_workflow_spec`` is *not* expected to be byte-identical because
    ``from_workflow_spec`` rebuilds each step node via ``default_node``,
    which uses the editor's default port types — and those defaults
    differ from the customised STRUCTURES-port topology we used to
    build the original graph. The contract we assert here is the
    *information* round-trip: every emitting step name survives, and
    every ``inputs`` list survives at the YAML-data layer.
    """
    parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    rebuilt = from_workflow_spec(parsed)
    # The rebuilt graph carries XYZ_FILE + 5 emitting steps + OUTPUT.  Keep
    # the emitter filter explicit rather than dropping every non-sentinel
    # node: an ADVANCED node is a real editor node and must be checked below.
    emitting_kinds = {
        NodeKind.CONF_GEN,
        NodeKind.PRE_OPT,
        NodeKind.OPT,
        NodeKind.SINGLE_POINT,
        NodeKind.FREQUENCY,
        NodeKind.TS,
        NodeKind.REFINE,
    }
    rebuilt_titles = sorted(
        n.title for n in rebuilt.nodes.values() if n.kind in emitting_kinds
    )
    expected = sorted(["Generate", "Optimize", "Frequency", "SinglePoint", "Summary"])
    assert rebuilt_titles == expected, (
        f"rebuilt titles mismatch: got {rebuilt_titles}, expected {expected}"
    )
    advanced_nodes = [n for n in rebuilt.nodes.values() if n.kind is NodeKind.ADVANCED]
    assert not advanced_nodes, (
        "workflow defaults must not become a synthetic ADVANCED node: "
        f"{[n.params for n in advanced_nodes]}"
    )

    # The bridge's inverse rebuilds edges from each step's ``inputs``
    # list, so we can verify topology by walking the rebuilt graph's
    # edges: Summary should have two incoming calc edges (one from
    # Frequency, one from SinglePoint), and the rebuilt "Generate"
    # node is the only root with no calc predecessors.
    calc_ids_by_title: dict[str, str] = {
        n.title: n.id
        for n in rebuilt.nodes.values()
        if n.kind not in (NodeKind.XYZ_FILE, NodeKind.OUTPUT)
    }
    summary_id = calc_ids_by_title["Summary"]
    summary_predecessors = sorted(
        edge.src_node for edge in rebuilt.edges.values()
        if edge.dst_node == summary_id
    )
    assert summary_predecessors == sorted([
        calc_ids_by_title["Frequency"],
        calc_ids_by_title["SinglePoint"],
    ]), (
        f"Summary's rebuilt predecessors wrong: {summary_predecessors}"
    )

    # And the YAML data layer still carries the DAG: re-parse the
    # original YAML and confirm ``Summary.inputs`` still names both
    # predecessors.
    summary_step = _step(parsed["steps"], "Summary")
    assert sorted(summary_step["inputs"]) == ["Frequency", "SinglePoint"], (
        f"Summary inputs not preserved after YAML round-trip: "
        f"{summary_step['inputs']}"
    )


def check_confflow_engine_walks_the_dag(yaml_path: Path) -> None:
    """The vendored ``confflow.workflow.dag`` module walks the YAML."""
    from jobdesk_app.confflow.confflow.workflow.dag import (
        build_step_graph,
        topo_order,
    )

    parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    predecessors, by_name, declared_inputs = build_step_graph(parsed["steps"])
    assert set(by_name) == {
        "Generate", "Optimize", "Frequency", "SinglePoint", "Summary",
    }, f"unexpected step set: {sorted(by_name)}"
    waves = topo_order(predecessors)
    # Wave 0: Generate (the only root).
    flat = [name for wave in waves for name in wave]
    assert flat.index("Generate") < flat.index("Optimize") < flat.index("Summary"), (
        f"topological order does not respect fan-out: {flat}"
    )
    # Frequency and SinglePoint both depend on Optimize, so both must
    # appear in a wave strictly after Optimize.
    opt_pos = flat.index("Optimize")
    assert flat.index("Frequency") > opt_pos
    assert flat.index("SinglePoint") > opt_pos
    # Summary depends on Frequency AND SinglePoint, so it must come
    # strictly after both.
    summary_pos = flat.index("Summary")
    assert summary_pos > flat.index("Frequency")
    assert summary_pos > flat.index("SinglePoint")
    # The declared_inputs map should record the fan-in at Summary.
    assert declared_inputs.get("Summary") == ["Frequency", "SinglePoint"]


def check_yaml_has_dag_kind_marker(parsed: dict) -> None:
    """The YAML's per-step ``inputs`` lists carry the DAG topology."""
    by_name = {step["name"]: step for step in parsed["steps"]}
    # Roots: Generate has empty inputs.
    assert by_name["Generate"]["inputs"] == []
    # Fan-out from Generate: both Optimize and SinglePoint list it.
    assert by_name["Optimize"]["inputs"] == ["Generate"]
    assert by_name["SinglePoint"]["inputs"] == ["Generate"]
    # Linear backbone: Frequency follows Optimize.
    assert by_name["Frequency"]["inputs"] == ["Optimize"]
    # Fan-in: Summary names BOTH predecessors.
    assert sorted(by_name["Summary"]["inputs"]) == ["Frequency", "SinglePoint"], (
        f"Summary fan-in missing: {by_name['Summary']['inputs']}"
    )
    # No duplicates anywhere.
    for step in parsed["steps"]:
        inputs = step["inputs"]
        assert len(inputs) == len(set(inputs)), (
            f"duplicate inputs on {step['name']}: {inputs}"
        )


# ── main ──────────────────────────────────────────────────────────────────


def main() -> int:
    failures: list[str] = []
    try:
        print("=" * 70)
        print("DAG ROUND-TRIP SMOKE — Phase 10")
        print("=" * 70)
        check_confflow_available()
        print("[1/5] confflow available: OK")
        steps = check_bridge_emits_4_steps_with_inputs()
        print("[2/5] to_workflow_spec: OK (5 steps with expected fan-out)")

        # All YAML-dependent checks run inside a single tempfile so the
        # on-disk workflow.yaml survives until we've finished every
        # assertion that needs it.
        with _YamlScratch(steps) as scratch:
            print(f"[3/5] SubmitUseCase wrote {scratch.yaml_path}")
            assert scratch.parsed is not None
            check_yaml_has_dag_kind_marker(scratch.parsed)
            print("[3b/5] YAML carries fan-out + fan-in inputs: OK")
            check_yaml_round_trips_through_from_workflow_spec(scratch.yaml_path)
            print("[4/5] from_workflow_spec round-trip: OK")
            check_confflow_engine_walks_the_dag(scratch.yaml_path)
            print("[5/5] confflow.engine topo_order: OK")
    except AssertionError as exc:
        failures.append(f"AssertionError: {exc}")
        traceback.print_exc()
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    print("=" * 70)
    if failures:
        print(f"DAG ROUND-TRIP SMOKE: FAIL ({len(failures)} failure(s))")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("DAG ROUND-TRIP SMOKE: PASS")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
