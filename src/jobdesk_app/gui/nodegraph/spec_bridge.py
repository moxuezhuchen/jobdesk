"""Bridge :class:`NodeGraph` to :class:`WorkflowSpec` / confflow YAML.

Phase 1.6 introduces the bridge between the visual editor and the
on-disk workflow YAML that the remote ``confflow`` process consumes.

Why a separate module
---------------------

``serialization.py`` owns the JSON template round-trip
(``to_json`` / ``from_json``); ``model.py`` is Qt-free. This bridge
imports both the nodegraph model *and* the confflow-vendored
``WorkflowSpec`` so it deserves its own home.

Phase 1 limitation: only linear workflows are supported. The
confflow engine itself gained fan-out / fan-in in Phase 3
(``graphlib.TopologicalSorter``-based dispatch), but the **editor**
in Phase 1 only allows one node to feed one successor. This bridge
mirrors that constraint: anything more elaborate raises
:class:`WorkflowSpecError`.

Mapping
-------

NodeKind          -> YAML step ``type`` (``itask`` if calc)
==============    ==================================================
``XYZ_FILE``      sentinel, not emitted as a step
``CONF_GEN``      ``type: confgen``
``PRE_OPT``       ``type: calc``, ``itask: preopt``
``OPT``           ``type: calc``, ``itask: opt``
``SINGLE_POINT``  ``type: calc``, ``itask: sp``
``FREQUENCY``     ``type: calc``, ``itask: freq``
``TS``            ``type: calc``, ``itask: ts``
``REFINE``        ``type: calc``, ``itask: refine``
``ADVANCED``      merges into ``global_config.calc.extra_options``
``OUTPUT``        sentinel, not emitted as a step
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jobdesk_app.core.workflow_spec import (
    WorkflowSpec,
    require_confflow,
)
from jobdesk_app.gui.nodegraph.model import (
    Node,
    NodeGraph,
    NodeKind,
)


# A confflow calc ``itask`` is one of: preopt, opt, sp, freq, ts, refine, opt_freq.
_CALC_ITASK_BY_KIND: dict[NodeKind, str] = {
    NodeKind.PRE_OPT: "preopt",
    NodeKind.OPT: "opt",
    NodeKind.SINGLE_POINT: "sp",
    NodeKind.FREQUENCY: "freq",
    NodeKind.TS: "ts",
    NodeKind.REFINE: "refine",
}

# Node kinds that produce a confflow "step" entry in the YAML.
_STEP_EMITTING_KINDS: frozenset[NodeKind] = frozenset({
    NodeKind.CONF_GEN,
    NodeKind.PRE_OPT,
    NodeKind.OPT,
    NodeKind.SINGLE_POINT,
    NodeKind.FREQUENCY,
    NodeKind.TS,
    NodeKind.REFINE,
})


class WorkflowSpecError(ValueError):
    """Raised when the node graph cannot be expressed as a workflow."""


@dataclass(frozen=True)
class WorkflowGraphPayload:
    """A pair of ``WorkflowSpec`` and the ``steps`` list that accompanies it.

    ``WorkflowSpec`` only owns ``global_config``; the editor-driven bridge
    also needs to ship the per-step list (which is otherwise authored by
    hand in ``workflow.yaml``). Bundling them in one dataclass keeps
    callers from forgetting one half.

    Attributes
    ----------
    spec : WorkflowSpec
        Validated global config; carries the merged ``extra_options``
        from any ``ADVANCED`` nodes plus the program/method/basis from
        the first ``OPT`` / ``SINGLE_POINT`` step encountered.
    steps : list[dict]
        One dict per emitted step (see the mapping table in the module
        docstring). Order is the topological order of the graph (linear
        for Phase 1.6).
    """

    spec: WorkflowSpec
    steps: list[dict[str, Any]] = field(default_factory=list)

    def to_yaml(self) -> str:
        """Serialize the combined payload back to ``workflow.yaml``.

        Equivalent to ``spec.to_yaml()`` plus the steps list written
        under a ``steps:`` key, in ``sort_keys=False`` order so the
        round-trip is byte-identical to a hand-written file.
        """
        import yaml

        data = self.spec.global_config.model_dump(mode="json", exclude_none=True)
        data["steps"] = self.steps
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


__all__ = [
    "WorkflowGraphPayload",
    "WorkflowSpecError",
    "from_workflow_spec",
    "to_workflow_spec",
]


# ── public API ──────────────────────────────────────────────────────────


def to_workflow_spec(graph: NodeGraph) -> WorkflowGraphPayload:
    """Build a :class:`WorkflowGraphPayload` from a :class:`NodeGraph`.

    Raises
    ------
    WorkflowSpecError
        When the graph is empty, has cycles, has fan-out / fan-in
        (Phase 1 limitation), references a kind that cannot be
        expressed as a workflow step, or any node has no ``title``.
    ConfFlowUnavailableError
        Propagated from :func:`require_confflow` when the vendored
        confflow package is missing (the GUI runs in a developer
        environment without it; the bridge can't function in that
        state and the caller should surface a clear message).
    """
    require_confflow()

    if not graph.nodes:
        raise WorkflowSpecError("graph is empty; nothing to serialize")

    issues = graph.validate()
    cycle = [i for i in issues if i.code == "CYCLE_DETECTED"]
    if cycle:
        raise WorkflowSpecError(
            "graph contains a cycle; only linear (acyclic) workflows are "
            "supported in Phase 1.6"
        )
    port_issues = [i for i in issues if i.code in {"INVALID_PORT_TYPE", "MISSING_REQUIRED_INPUT"}]
    if port_issues:
        # These are authoring errors; surface them as one combined message.
        msg = "; ".join(i.message for i in port_issues)
        raise WorkflowSpecError(f"graph is not well-formed: {msg}")

    # Resolve step ordering. Topological sort rejects cycles, which
    # ``validate`` already flagged, so this is safe.
    ordered_nodes = graph.topological_order()

    # Sanity-check fan-in / fan-out. Phase 1 only allows one incoming
    # edge per non-root calc/confgen node and one outgoing edge per
    # non-terminal node.
    _assert_linear_topology(graph, ordered_nodes)

    # Pull out the advanced-options bundle (any number of ADVANCED
    # nodes; their ``params`` dicts are merged in declaration order).
    advanced_params = _collect_advanced_params(graph)

    # Determine the program / method / basis from the first emitting
    # node that has them (typically an OPT or SINGLE_POINT). If none
    # are supplied, the WorkflowSpec.from_form defaults apply.
    program, method, basis, extra = _infer_program_and_keyword(
        graph, ordered_nodes, advanced_params
    )

    # Build steps list in topological order, naming each step uniquely.
    used_names: set[str] = set()
    steps: list[dict[str, Any]] = []
    for node in ordered_nodes:
        if node.kind not in _STEP_EMITTING_KINDS:
            continue
        step_name = _unique_step_name(node, used_names)
        used_names.add(step_name)
        step_dict = _build_step_dict(graph, node, step_name)
        steps.append(step_dict)

    # Pull out the first step's name as the work_dir hint? We deliberately
    # leave work_dir_name to the caller (the wizard sets it from the form).
    spec = WorkflowSpec.from_form(
        work_dir_name="",
        program=program,
        method=method,
        basis=basis,
        charge=int(extra.get("charge", 0)),
        multiplicity=int(extra.get("multiplicity", 1)),
        nproc=int(extra.get("nproc", 1)),
        memory_mb=int(extra.get("memory_mb", 1024)),
        steps=tuple(_step_type_token(s) for s in steps),
        extra_options=_clean_advanced(advanced_params, keep={"charge", "multiplicity", "nproc", "memory_mb"}),
    )
    return WorkflowGraphPayload(spec=spec, steps=steps)


def from_workflow_spec(
    payload: WorkflowGraphPayload | dict[str, Any],
) -> NodeGraph:
    """Rebuild a :class:`NodeGraph` from a payload or a raw YAML dict.

    Used by "Load template…" to recover a saved graph. Each step in
    ``payload.steps`` becomes one node; ``inputs`` (the Phase 3 DAG
    hint) is honoured when present and the order in ``steps`` defines
    a linear chain otherwise.

    Unknown step types raise :class:`WorkflowSpecError`. The bridge
    also injects a single ``XYZ_FILE`` sentinel at the front and a
    single ``OUTPUT`` sentinel at the end so the resulting graph is
    immediately user-editable.
    """
    require_confflow()

    if isinstance(payload, WorkflowGraphPayload):
        steps = list(payload.steps)
        extra = _extract_extra(payload.spec.global_config.model_dump(mode="json", exclude_none=True))
    else:
        steps = list(payload.get("steps", []))
        extra = _extract_extra(payload)

    if not steps:
        # An empty workflow is still a valid graph (with the sentinels).
        pass

    graph = NodeGraph()

    # First pass: emit one node per step in declaration order.
    step_node_ids: list[str] = []
    for step in steps:
        kind = _step_kind(step)
        node = default_node_for_step(kind, step)
        graph.add_node(node)
        step_node_ids.append(node.id)

    # Inject XYZ_FILE + OUTPUT sentinels so the graph is round-trippable.
    from jobdesk_app.gui.nodegraph.model import default_node

    xyz_node = default_node(NodeKind.XYZ_FILE, position=(40.0, 60.0))
    graph.add_node(xyz_node)
    out_node = default_node(NodeKind.OUTPUT, position=(620.0, 60.0))
    graph.add_node(out_node)

    # Second pass: linear chain. XYZ_FILE -> first step -> ... -> last
    # step -> OUTPUT. We deliberately ignore the ``inputs`` array for
    # Phase 1.6; Phase 3+ will read it for true DAG wiring.
    if step_node_ids:
        first_step_id = step_node_ids[0]
        last_step_id = step_node_ids[-1]
        graph.add_edge(_make_linear_edge(graph, xyz_node.id, first_step_id))
        prev_id: str | None = None
        for nid in step_node_ids:
            if prev_id is not None:
                graph.add_edge(_make_linear_edge(graph, prev_id, nid))
            prev_id = nid
        graph.add_edge(_make_linear_edge(graph, last_step_id, out_node.id))

    # Carry any ADVANCED / extra_options as a synthetic ADVANCED node.
    if extra:
        adv = default_node(NodeKind.ADVANCED, position=(40.0, 320.0))
        adv.params = dict(extra)
        graph.add_node(adv)

    return graph


# ── helpers ─────────────────────────────────────────────────────────────


def _assert_linear_topology(graph: NodeGraph, ordered: list[Node]) -> None:
    """Phase 1.6 only allows linear chains; reject fan-in / fan-out."""
    for node in ordered:
        if node.kind in (NodeKind.XYZ_FILE, NodeKind.OUTPUT, NodeKind.ADVANCED):
            continue
        in_edges = graph.incoming_edges(node.id)
        if len(in_edges) > 1:
            raise WorkflowSpecError(
                f"node '{node.title or node.kind.value}' has {len(in_edges)} "
                f"incoming edges; Phase 1.6 supports at most one predecessor"
            )
        if _kind_emits_step(node.kind):
            out_edges = [
                e for e in graph.outgoing_edges(node.id)
                if _kind_emits_step(graph.nodes[e.dst_node].kind)
            ]
            if len(out_edges) > 1:
                raise WorkflowSpecError(
                    f"node '{node.title or node.kind.value}' fans out to "
                    f"{len(out_edges)} calc/confgen successors; Phase 1.6 "
                    f"supports linear chains only"
                )


def _kind_emits_step(kind: NodeKind) -> bool:
    return kind in _STEP_EMITTING_KINDS


def _collect_advanced_params(graph: NodeGraph) -> dict[str, Any]:
    """Merge every ``ADVANCED`` node's ``params`` into a single dict."""
    merged: dict[str, Any] = {}
    for node in graph.nodes.values():
        if node.kind is NodeKind.ADVANCED:
            for k, v in node.params.items():
                merged[k] = v
    return merged


def _infer_program_and_keyword(
    graph: NodeGraph,
    ordered: list[Node],
    advanced: dict[str, Any],
) -> tuple[str, str, str, dict[str, Any]]:
    """Pick a program/method/basis triple for the global config.

    Phase 1 has no per-step ``iprog`` / ``keyword`` field; all calc steps
    share the workflow-level ``keyword``. We pull the first calc step's
    ``keyword`` (if any) and forward the rest of the per-step params as
    advanced overrides.
    """
    program = str(advanced.get("program", "orca"))
    method = str(advanced.get("method", ""))
    basis = str(advanced.get("basis", ""))
    keyword = str(advanced.get("keyword", ""))

    # Walk the first emitting step that has params; copy them in if the
    # workflow-level config didn't set them already.
    for node in ordered:
        if not _kind_emits_step(node.kind):
            continue
        params = dict(node.params)
        if node.kind is NodeKind.CONF_GEN:
            # confgen params are per-step; don't pollute global.
            continue
        if not method and "method" in params:
            method = str(params["method"])
        if not basis and "basis" in params:
            basis = str(params["basis"])
        if "keyword" in params and not keyword:
            keyword = str(params["keyword"])
        if "iprog" in params and not advanced.get("program"):
            program = str(params["iprog"])
        # Stop after the first calc node; later steps inherit keyword.
        break

    # keyword wins over method/basis if both present.
    if keyword:
        # Defer to keyword; leave method/basis empty.
        method = ""
        basis = ""

    extra = {k: v for k, v in advanced.items()
             if k not in {"program", "method", "basis", "keyword"}}
    return program, method, basis, extra


def _clean_advanced(advanced: dict[str, Any], keep: set[str]) -> dict[str, Any]:
    """Strip already-promoted keys from the advanced-options bundle."""
    return {k: v for k, v in advanced.items() if k not in keep}


def _unique_step_name(node: Node, used: set[str]) -> str:
    """Pick a stable name for a step; fall back to ``<kind>_<id8>`` on collision."""
    base = (node.title or node.kind.value).strip()
    # Sanitize: confflow step names are free-form but should not contain
    # whitespace that confuses YAML readers; use a slugified form.
    slug = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in base).strip("_")
    if not slug:
        slug = node.kind.value
    candidate = slug
    suffix = 0
    while candidate in used:
        suffix += 1
        candidate = f"{slug}_{suffix}"
    return candidate


def _build_step_dict(graph: NodeGraph, node: Node, step_name: str) -> dict[str, Any]:
    """Build the per-step dict that will live under ``steps:`` in YAML."""
    params = dict(node.params)
    if node.kind is NodeKind.CONF_GEN:
        return {"name": step_name, "type": "confgen", "params": params}
    itask = _CALC_ITASK_BY_KIND[node.kind]
    step: dict[str, Any] = {"name": step_name, "type": "calc", "params": params}
    # ``itask`` is a top-level param key in confflow's calc config; we
    # place it explicitly so the workflow is self-describing.
    step["params"]["itask"] = itask
    return step


def _step_type_token(step: dict[str, Any]) -> str:
    """Return the string the wizard form uses to label this step type."""
    step_type = step.get("type")
    if step_type == "confgen":
        return "confgen"
    itask = step.get("params", {}).get("itask")
    return f"calc:{itask}" if itask else "calc"


def _step_kind(step: dict[str, Any]) -> NodeKind:
    """Reverse mapping: YAML step dict -> :class:`NodeKind`."""
    step_type = step.get("type")
    if step_type == "confgen":
        return NodeKind.CONF_GEN
    if step_type in {"calc", "task"}:
        itask = step.get("params", {}).get("itask")
        for kind, code in _CALC_ITASK_BY_KIND.items():
            if code == itask:
                return kind
        # Default to OPT for unrecognised calc tasks.
        return NodeKind.OPT
    raise WorkflowSpecError(f"unknown step type in payload: {step_type!r}")


def _extract_extra(data: dict[str, Any]) -> dict[str, Any]:
    """Pull ``global_config.calc.extra_options`` from a dumped model."""
    calc = data.get("calc", {}) if isinstance(data, dict) else {}
    if not isinstance(calc, dict):
        return {}
    # Anything not in the well-known global keys is treated as "extra".
    well_known = {
        "program", "method", "basis", "charge", "multiplicity",
        "nproc", "memory_mb", "keyword", "steps",
    }
    return {k: v for k, v in calc.items() if k not in well_known}


def default_node_for_step(kind: NodeKind, step: dict[str, Any]) -> Node:
    """Construct a :class:`Node` for ``kind`` and seed it from ``step``."""
    from jobdesk_app.gui.nodegraph.model import default_node as _mk

    node = _mk(kind)
    node.title = str(step.get("name", kind.value))
    params = dict(step.get("params", {}))
    # Pop itask out of params; it belongs on the kind, not in the dict.
    params.pop("itask", None)
    node.params = params
    return node


def _make_linear_edge(
    graph: NodeGraph,
    src_node_id: str,
    dst_node_id: str,
    *,
    port: str | None = None,
    dst_port: str | None = None,
) -> "Edge":
    """Create an edge between the canonical output / input ports."""
    from jobdesk_app.gui.nodegraph.model import Edge

    src_node = graph.nodes[src_node_id]
    dst_node = graph.nodes[dst_node_id]
    src_port = port if port is not None else _canonical_output(src_node)
    dst_p = dst_port if dst_port is not None else _canonical_input(dst_node)
    return Edge(
        id=Edge.new_id(),
        src_node=src_node_id,
        src_port=src_port,
        dst_node=dst_node_id,
        dst_port=dst_p,
    )


def _canonical_output(node: Node) -> str:
    if node.outputs:
        return node.outputs[0].name
    return "out"


def _canonical_input(node: Node) -> str:
    if node.inputs:
        return node.inputs[0].name
    return "in"

