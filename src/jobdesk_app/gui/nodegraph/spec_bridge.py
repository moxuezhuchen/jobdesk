"""Bridge :class:`NodeGraph` to :class:`WorkflowSpec` / confflow YAML.

Phase 1.6 introduces the bridge between the visual editor and the
on-disk workflow YAML that the remote ``confflow`` process consumes.

Why a separate module
---------------------

``serialization.py`` owns the JSON template round-trip
(``to_json`` / ``from_json``); ``model.py`` is Qt-free. This bridge
imports both the nodegraph model *and* the confflow-vendored
``WorkflowSpec`` so it deserves its own home.

Phase 10 widens the bridge so the editor can author DAG workflows.
``StepConfig.inputs`` is a list of upstream step names and is now
written verbatim into each ``step`` dict. ``XYZ_FILE`` is no longer
a pre-wired edge to the first step in the dict (its ``inputs`` list
just stays empty); ``OUTPUT`` is still injected as a sentinel but is
connected to every "leaf" step (no outgoing calc edges), which for
linear chains is just the last step. The confflow engine itself has
understood ``StepConfig.inputs`` since ``1dff20f``.

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
    Edge,
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

        v6 schema: ``{global: {...}, steps: [...]}`` — matches what
        ``confflow.config.loader`` consumes. The ``spec.to_yaml()``
        path already produces this shape, so we just delegate and
        stitch in any ``steps`` overrides the editor may have added
        on top of what ``WorkflowSpec.from_form`` produced.
        """
        import yaml

        spec_yaml = self.spec.to_yaml()
        base = yaml.safe_load(spec_yaml) or {}
        if not isinstance(base, dict):
            base = {}
        # Honour any editor-supplied steps if they're richer than the
        # wizard's view (e.g. the nodegraph may add confgen params).
        if self.steps:
            base["steps"] = list(self.steps)
        return yaml.safe_dump(base, sort_keys=False, allow_unicode=True)


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
        When the graph is empty, has cycles, references a kind that
        cannot be expressed as a workflow step, has ``XYZ_FILE``
        connected to anything, has ``OUTPUT`` feeding something,
        fans into a single ``STRUCTURE`` input port of a calc /
        confgen node, or any node has no ``title``.
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
            "graph contains a cycle; only acyclic workflows are supported"
        )
    port_issues = [i for i in issues if i.code in {"INVALID_PORT_TYPE", "MISSING_REQUIRED_INPUT"}]
    if port_issues:
        # These are authoring errors; surface them as one combined message.
        msg = "; ".join(i.message for i in port_issues)
        raise WorkflowSpecError(f"graph is not well-formed: {msg}")

    # Resolve step ordering. Topological sort rejects cycles, which
    # ``validate`` already flagged, so this is safe.
    ordered_nodes = graph.topological_order()

    # Sanity-check terminal / fan-in limits beyond what ``validate``
    # already covers (cycle, port types, required inputs).
    _assert_well_formed(graph, ordered_nodes)

    # Map each emitting step to its deterministic step name; indexable
    # by node so we can resolve ``step["inputs"]`` later.
    used_names: set[str] = set()
    step_name_by_node_id: dict[str, str] = {}
    for node in ordered_nodes:
        if node.kind not in _STEP_EMITTING_KINDS:
            continue
        step_name = _unique_step_name(node, used_names)
        used_names.add(step_name)
        step_name_by_node_id[node.id] = step_name

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
    steps: list[dict[str, Any]] = []
    for node in ordered_nodes:
        if node.kind not in _STEP_EMITTING_KINDS:
            continue
        step_name = step_name_by_node_id[node.id]
        step_dict = _build_step_dict(graph, node, step_name, step_name_by_node_id)
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
    ``payload.steps`` becomes one node; its ``inputs`` field is honoured
    when present and the bridge wires back the original (DAG) topology.
    For backward compatibility the bridge still tolerates the linear
    shape produced by Phase 1.6 (no ``inputs`` field, plain list of
    calc steps).

    The bridge also injects a single ``XYZ_FILE`` sentinel at the
    front and a single ``OUTPUT`` sentinel at the end so the resulting
    graph is immediately user-editable. ``OUTPUT`` becomes the
    successor of every "leaf" step (steps with no outgoing calc
    edges); for linear graphs this is just the last step. The
    ``XYZ_FILE`` sentinel is only wired to a step that consumes the
    global XYZ input — typically a ``CONF_GEN`` root.
    """
    require_confflow()

    if isinstance(payload, WorkflowGraphPayload):
        steps = list(payload.steps)
        dump = payload.spec.global_config.model_dump(
            mode="json", exclude_none=True
        )
        # Tag the dump with which model fields the user actually wrote,
        # so ``_extract_extra`` can ignore the dozens of confflow
        # defaults that ``model_dump`` always emits.
        dump["_user_set_keys"] = set(
            payload.spec.global_config.model_fields_set
        )
        extra = _extract_extra(dump)
    else:
        steps = list(payload.get("steps", []))
        extra = _extract_extra(payload)

    graph = NodeGraph()

    # First pass: emit one node per step in declaration order. We do
    # *not* rely on the YAML order to wire edges later: the bridge
    # reads ``step.get("inputs", [])`` and reverses the names back to
    # node ids.
    step_node_by_name: dict[str, str] = {}
    for step in steps:
        kind = _step_kind(step)
        node = default_node_for_step(kind, step)
        graph.add_node(node)
        step_node_by_name[node.title] = node.id

    # Inject XYZ_FILE + OUTPUT sentinels. OUTPUT has a wildcard visual
    # terminal port; its connections are ignored by YAML serialisation.
    from jobdesk_app.gui.nodegraph.model import default_node

    xyz_node = default_node(NodeKind.XYZ_FILE, position=(40.0, 60.0))
    graph.add_node(xyz_node)
    out_node = default_node(NodeKind.OUTPUT, position=(620.0, 60.0))
    graph.add_node(out_node)

    # Second pass: re-wire edges from ``step["inputs"]``. Each step
    # that lists non-empty ``inputs`` gets an edge per upstream name;
    # the first step (or any step with empty ``inputs``) gets wired
    # to the ``XYZ_FILE`` sentinel instead.
    has_dag_wiring = False
    for step in steps:
        step_name = str(step.get("name", ""))
        dst_id = step_node_by_name.get(step_name)
        if dst_id is None:
            continue
        upstream_names = _normalize_inputs(step.get("inputs", []))
        if upstream_names:
            for upstream_name in upstream_names:
                src_id = step_node_by_name.get(upstream_name)
                if src_id is None or src_id == dst_id:
                    continue
                graph.add_edge(_make_dag_edge(graph, src_id, dst_id))
                has_dag_wiring = True
        elif step is steps[0]:
            # First step in declaration order consumes the global XYZ
            # file unless it explicitly named upstream steps above.
            graph.add_edge(_make_dag_edge(graph, xyz_node.id, dst_id, dst_port="in"))

    if has_dag_wiring:
        outgoing_by_node: dict[str, int] = {nid: 0 for nid in step_node_by_name.values()}
        for edge in graph.edges.values():
            if edge.src_node in outgoing_by_node:
                outgoing_by_node[edge.src_node] += 1
        for leaf_id, count in outgoing_by_node.items():
            if count == 0:
                graph.add_edge(_make_dag_edge(graph, leaf_id, out_node.id, src_port="out"))
    elif step_node_by_name:
        # Phase 1.6 linear path: explicit chain plus a final output
        # edge. This branch only runs when the YAML did not declare
        # any ``inputs`` list at all.
        ordered_ids = list(step_node_by_name.values())
        for prev_id, cur_id in zip(ordered_ids[:-1], ordered_ids[1:]):
            graph.add_edge(_make_dag_edge(graph, prev_id, cur_id))
        graph.add_edge(_make_dag_edge(graph, ordered_ids[-1], out_node.id, src_port="out"))

    # Carry any ADVANCED / extra_options as a synthetic ADVANCED node.
    if extra:
        adv = default_node(NodeKind.ADVANCED, position=(40.0, 320.0))
        adv.params = dict(extra)
        graph.add_node(adv)

    return graph


def _normalize_inputs(raw: Any) -> list[str]:
    """Coerce ``step["inputs"]`` into a list of unique step names."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, (list, tuple)):
        seen: set[str] = set()
        out: list[str] = []
        for value in raw:
            name = str(value).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(name)
        return out
    return []


def _make_dag_edge(
    graph: NodeGraph,
    src_node_id: str,
    dst_node_id: str,
    *,
    src_port: str | None = None,
    dst_port: str | None = None,
) -> "Edge":
    """Build an Edge using the canonical output / input ports of each node."""
    src_node = graph.nodes[src_node_id]
    dst_node = graph.nodes[dst_node_id]
    src_p = src_port if src_port is not None else _canonical_output(src_node)
    dst_p = dst_port if dst_port is not None else _canonical_input(dst_node)
    return Edge(
        id=Edge.new_id(),
        src_node=src_node_id,
        src_port=src_p,
        dst_node=dst_node_id,
        dst_port=dst_p,
    )


# ── helpers ─────────────────────────────────────────────────────────────


def _assert_well_formed(graph: NodeGraph, ordered: list[Node]) -> None:
    """Apply bridge-specific topology rules beyond :meth:`NodeGraph.validate`.

    Rules
    -----

    * ``XYZ_FILE`` has no incoming edges (it is the only place a step
      can be sourced from, and any extra wiring would mean the user
      mis-dragged).
    * ``OUTPUT`` has no outgoing edges.
    * Each ``STRUCTURE`` input port of a calc / confgen / refine
      node accepts **at most one** incoming edge. ``STRUCTURES`` is
      the only port type that allows many-to-one fan-in (e.g. two
      parallel optimizer runs feeding a ``Refine``'s ensemble
      socket).
    * Self-loops and cycles are flagged by ``graph.validate``
      (``CYCLE_DETECTED``) and we short-circuit there.
    """
    label = lambda n: n.title or n.kind.value  # noqa: E731

    for node in graph.nodes.values():
        if node.kind is NodeKind.XYZ_FILE:
            incoming = graph.incoming_edges(node.id)
            if incoming:
                raise WorkflowSpecError(
                    f"{label(node)} (XYZ_FILE) must not have incoming edges; "
                    f"found {len(incoming)}"
                )
            continue
        if node.kind is NodeKind.OUTPUT:
            outgoing = graph.outgoing_edges(node.id)
            if outgoing:
                raise WorkflowSpecError(
                    f"{label(node)} (OUTPUT) must not have outgoing edges; "
                    f"found {len(outgoing)}"
                )
            continue
        if node.kind in (NodeKind.ADVANCED,):
            continue

    # Many-to-one fan-in is only allowed on ``STRUCTURES``-typed input
    # ports. For every other port type we expect ≤ 1 incoming edge.
    from jobdesk_app.gui.nodegraph.model import PortType
    for node in ordered:
        if node.kind not in _STEP_EMITTING_KINDS:
            continue
        for port in node.inputs:
            incoming = graph.incoming_edges(node.id, port.name)
            if len(incoming) <= 1:
                continue
            if port.type is PortType.STRUCTURES:
                continue
            raise WorkflowSpecError(
                f"{label(node)} port '{port.label or port.name}' "
                f"({port.type.value}) accepts at most one predecessor; "
                f"found {len(incoming)} (STRUCTURES ports are the only "
                f"type that permits fan-in)"
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


def _build_step_dict(
    graph: NodeGraph,
    node: Node,
    step_name: str,
    step_name_by_node_id: dict[str, str],
) -> dict[str, Any]:
    """Build the per-step dict that will live under ``steps:`` in YAML.

    The new ``inputs`` field is a list of upstream step names wired to
    this node via the graph's incoming edges. Roots (no predecessors)
    emit ``inputs: []``; a node can declare any number of upstream
    names (the DAG engine reads each one and pulls the corresponding
    step result into its ``inputs`` mapping).

    The list is deduped and ordered by the upstream node's
    ``topological_order`` position so the YAML output is stable across
    runs.
    """
    input_names = _upstream_step_names(graph, node, step_name_by_node_id)
    params = dict(node.params)
    if node.kind is NodeKind.CONF_GEN:
        return {"name": step_name, "type": "confgen", "params": params, "inputs": list(input_names)}
    itask = _CALC_ITASK_BY_KIND[node.kind]
    step: dict[str, Any] = {"name": step_name, "type": "calc", "params": params, "inputs": list(input_names)}
    # ``itask`` is a top-level param key in confflow's calc config. Keep
    # a more specific task recovered from YAML (notably ``opt_freq``),
    # because ``NodeKind.OPT`` is only its closest visual representation.
    step["params"].setdefault("itask", itask)
    return step


def _upstream_step_names(
    graph: NodeGraph,
    node: Node,
    step_name_by_node_id: dict[str, str],
) -> list[str]:
    """Return the upstream step names (deduped, topologically ordered)."""
    name_pos: dict[str, int] = {nid: idx for idx, nid in enumerate(_node_id_topological_order(graph))}
    ordered_names: list[str] = []
    seen: set[str] = set()
    for edge in graph.incoming_edges(node.id):
        upstream_name = step_name_by_node_id.get(edge.src_node)
        if upstream_name is None or upstream_name in seen:
            continue
        seen.add(upstream_name)
        # Binary insertion to keep the list sorted by topological position.
        upstream_pos = name_pos.get(edge.src_node, len(name_pos))
        insert_at = len(ordered_names)
        for idx, existing in enumerate(ordered_names):
            existing_pos = name_pos.get(
                next(
                    src for src, name in step_name_by_node_id.items() if name == existing
                ),
                len(name_pos),
            )
            if upstream_pos < existing_pos:
                insert_at = idx
                break
        ordered_names.insert(insert_at, upstream_name)
    return ordered_names


def _node_id_topological_order(graph: NodeGraph) -> list[str]:
    """Return node ids in topological order (sources first)."""
    if any(issue.code == "CYCLE_DETECTED" for issue in graph._check_cycles()):
        raise ValueError("cannot topologically sort a graph with a cycle")
    visited: set[str] = set()
    order: list[str] = []

    def visit(nid: str) -> None:
        if nid in visited:
            return
        visited.add(nid)
        for edge in graph.incoming_edges(nid):
            visit(edge.src_node)
        order.append(nid)

    for nid in list(graph.nodes):
        visit(nid)
    return order


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
    """Pull extra (non-well-known) keys from a dumped ``GlobalConfigModel``.

    Tolerates both the legacy nested shape (``calc: { program, method, … }``)
    and the v5 flat shape (everything at the top level).

    We treat fields the user actually wrote as ``extra`` — the
    ``GlobalConfigModel`` exposes ``model_fields_set()`` for this.  When
    the caller passes a raw dict (e.g. from a legacy YAML file), we
    accept every well-known key as "known" and pass the rest through.
    """
    if not isinstance(data, dict):
        return {}
    well_known = {
        "work_dir", "calc",
        "program", "method", "basis", "charge", "multiplicity",
        "nproc", "memory_mb", "cores_per_task", "total_memory",
        "max_parallel_jobs", "energy_window",
        "keyword", "steps", "freeze", "gaussian_path", "orca_path",
        # Keep every field declared by ConfFlow's GlobalConfigModel here.
        # ``from_workflow_spec`` also accepts the flat YAML emitted by the
        # DAG submit path, which contains model defaults (for example
        # ``rmsd_threshold`` and ``resume_from_backups``).  Treating those
        # defaults as arbitrary extras creates a spurious ADVANCED node on
        # every reload of a saved graph.  Unknown keys remain preserved as
        # genuine free-form advanced options below.
        "rmsd_threshold", "energy_tolerance", "noH", "ts_bond_atoms",
        "ts_rescue_scan", "scan_coarse_step", "scan_fine_step",
        "scan_uphill_limit", "ts_bond_drift_threshold", "ts_rmsd_threshold",
        "enable_dynamic_resources", "resume_from_backups",
        "stop_check_interval_seconds", "force_consistency",
    }
    # Prefer the legacy ``calc`` subsection; fall back to the flat top level.
    calc = data.get("calc")
    source: dict[str, Any]
    if isinstance(calc, dict):
        source = calc
    else:
        source = data
    # If the caller embedded ``_user_set_keys`` (a list of model fields
    # that were explicitly populated), honour that to filter out the
    # confflow defaults that the model_dump() emits.
    explicit_keys = data.get("_user_set_keys")
    if isinstance(explicit_keys, (set, list, tuple)):
        explicit_keys = set(explicit_keys)
    else:
        explicit_keys = None
    out: dict[str, Any] = {}
    for k, v in source.items():
        if k in well_known or k == "_user_set_keys":
            continue
        if explicit_keys is not None and k not in explicit_keys:
            continue
        out[k] = v
    return out


def default_node_for_step(kind: NodeKind, step: dict[str, Any]) -> Node:
    """Construct a :class:`Node` for ``kind`` and seed it from ``step``."""
    from jobdesk_app.gui.nodegraph.model import default_node as _mk

    node = _mk(kind)
    node.title = str(step.get("name", kind.value))
    params = dict(step.get("params", {}))
    # For canonical visual kinds the itask is implied by ``kind``. Keep
    # non-canonical tasks such as ``opt_freq``: otherwise reopening a
    # workflow would silently degrade it to the closest kind (``opt``).
    itask = params.get("itask")
    if itask == _CALC_ITASK_BY_KIND.get(kind):
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
    """Create an edge between the canonical output / input ports.

    Kept as a thin alias of :func:`_make_dag_edge` so existing call
    sites (and historical tests) that named the helper specifically
    continue to work.
    """
    return _make_dag_edge(
        graph,
        src_node_id,
        dst_node_id,
        src_port=port,
        dst_port=dst_port,
    )


def _canonical_output(node: Node) -> str:
    if node.outputs:
        return node.outputs[0].name
    return "out"


def _canonical_input(node: Node) -> str:
    if node.inputs:
        return node.inputs[0].name
    return "in"
