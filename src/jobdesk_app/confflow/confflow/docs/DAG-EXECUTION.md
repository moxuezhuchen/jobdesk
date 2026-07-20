# DAG Execution

ConfFlow workflows can be expressed as directed acyclic graphs (DAGs)
rather than strict linear chains. This document describes the schema,
behaviour, and limitations of the DAG-aware dispatcher that ships in
Phase 3 (branch `feature/confflow-monorepo`).

## Motivation

The Phase 1.2-1.5 nodegraph visual layer already serialises workflows
as a DAG (nodes + edges) and round-trips them to YAML. The dispatcher
behind the scenes, however, still walked a linear `steps` list. Phase 3
turns the dispatcher into a true topological executor so the visual
DAG stops being a lie.

Two workflow shapes become first-class:

- **Fan-out**: one conformer step feeding two parallel optimisations
  (or one SP and one TS search, etc.).
- **Fan-in**: two conformer ensembles merged before a single
  refinement step.

## Schema change

A step in a workflow YAML may now declare an `inputs` field listing
the names of upstream steps it depends on. Optionally it can declare
an `outputs` field listing the paths it will write.

```yaml
- name: opt_a
  type: calc
  inputs: [confgen]
  params: { iprog: orca, itask: sp, keyword: B3LYP }

- name: merge
  type: calc
  inputs: [opt_a, opt_b]
  params: { iprog: orca, itask: sp, keyword: B3LYP }
```

These fields are validated at the type level by the new
`confflow.core.models.StepConfig` Pydantic model:

```python
class StepConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    type: str
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
```

The schema is permissive (`extra="allow"`) so existing step dicts that
contain extra keys not modelled here (e.g. `kind`, `enabled`, runtime
metadata) keep working.

## Behaviour change

The dispatch loop in `confflow.workflow.engine.run_workflow` now uses
`graphlib.TopologicalSorter` to schedule steps in dependency order.

- If **any** step declares a non-empty `inputs` field, the engine
  builds a DAG from the declared edges.
- If **no** step declares `inputs`, the engine synthesises the legacy
  linear chain (`step[i-1].name -> step[i].name`) so existing YAML
  workflows run exactly as before. No migration needed.

### Deterministic tie-breaking

`graphlib.TopologicalSorter` returns ready nodes in dict-insertion
order, which is not guaranteed identical across Python implementations
or even across runs. To keep the workflow reproducible we:

1. Sort the ready wave in `confflow.workflow.dag.topo_order`.
2. Sort the wave again in the engine before dispatching.

This means two independent steps whose dependencies both finish in the
same wave always run in alphabetical order, every time.

### Cycle detection

If the workflow graph contains a cycle, `graphlib.TopologicalSorter`
raises `graphlib.CycleError`. The DAG helper `topo_order` wraps this
in a `ConfFlowError` with a message naming the participating nodes.
The engine re-exports the class as `DagCycleError` for callers that
want to catch the more specific type.

```python
from confflow.workflow.engine import DagCycleError, run_workflow

try:
    run_workflow(...)
except DagCycleError as e:
    print(f"workflow is not a DAG: {e}")
```

### Fan-in: partially supported

When a step receives more than one predecessor (fan-in), the engine
logs a warning:

```
WARNING confflow.workflow.step_handlers:
    step 'merge' received 2 inputs from predecessors; fan-in is
    partially supported in this release. Using the primary
    (inputs[0]) only.
```

The step's handler (`run_confgen_step` or `run_calc_step`) receives
the full list, but only `inputs[0]` is fed to `ChemTaskManager.run`
or `confgen.run_generation`. Real fan-in (merging two conformer
ensembles into one input) is on the roadmap but not implemented in
this release.

### Resume

Resume reads the legacy `.checkpoint` file (an integer index) and
skips any step whose YAML-list position is `<= resume_from_step`. In
DAG mode this is best-effort: if a resumed step has no surviving
output on disk, the engine falls back to the workflow input so the
chain can keep advancing. Partial mid-DAG resume is not supported in
this release.

## Fan-in / Fan-out Examples

### Linear chain (backward compatible, no `inputs` declared)

```yaml
steps:
  - name: s1
    type: confgen
    params: { chains: ["1-2"] }
  - name: s2
    type: calc
    params: { iprog: orca, itask: sp, keyword: B3LYP }
  - name: s3
    type: calc
    params: { iprog: orca, itask: sp, keyword: B3LYP }
```

This runs as before: s1, then s2, then s3.

### Fan-out

```yaml
steps:
  - name: confgen
    type: confgen
    params: { chains: ["1-2"] }
  - name: opt_a
    type: calc
    inputs: [confgen]
    params: { iprog: orca, itask: opt, keyword: B3LYP }
  - name: opt_b
    type: calc
    inputs: [confgen]
    params: { iprog: orca, itask: opt, keyword: B3LYP }
```

`confgen` runs first. `opt_a` and `opt_b` both run in the second wave
(alphabetical order: `opt_a` before `opt_b`).

### Fan-in (warning expected)

```yaml
steps:
  - name: confgen_a
    type: confgen
    params: { chains: ["1-2"] }
  - name: confgen_b
    type: confgen
    params: { chains: ["1-2"] }
  - name: merge
    type: calc
    inputs: [confgen_a, confgen_b]
    params: { iprog: orca, itask: sp, keyword: B3LYP }
```

Both `confgen_a` and `confgen_b` run in the first wave. The `merge`
calc step waits for both, logs a fan-in warning, and consumes
`confgen_a/output.xyz` only.

### Diamond (fan-out + fan-in)

```yaml
steps:
  - name: confgen
    type: confgen
  - name: opt_a
    type: calc
    inputs: [confgen]
  - name: opt_b
    type: calc
    inputs: [confgen]
  - name: final
    type: calc
    inputs: [opt_a, opt_b]
```

Three waves: `[confgen]`, `[opt_a, opt_b]`, `[final]`.

## Limitations / Out of scope

- **Parallel execution of independent steps**: still serial in this
  release. The DAG enables it conceptually but the engine loops one
  step at a time.
- **Cycle-aware recovery**: a cycle just raises; the engine does not
  try to replan.
- **Visual DAG <-> YAML round-trip**: handled by Phase 1.5; not
  touched here.
- **Pydantic string-coercion of `inputs`**: `StepConfig` expects a
  `list[str]`. Build the list explicitly in YAML; the runtime
  helper `confflow.workflow.dag.build_step_graph` also accepts a
  comma-separated string for ergonomics when parsing raw dicts.

## Test coverage

- `tests/test_dag_engine.py` covers fan-out, fan-in, cycle
  detection, deterministic tie-breaking, and an end-to-end
  acceptance run against `tests/data/wf_dag.yaml`.
- `tests/test_step_handlers.py` was updated for the new
  `inputs: list[str]` signature.
- All pre-existing confflow tests pass unchanged thanks to the
  legacy linear fallback.
