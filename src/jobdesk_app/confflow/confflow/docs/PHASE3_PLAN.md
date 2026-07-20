# Phase 3 Plan: DAG Workflow Execution

Branch: `feature/confflow-monorepo` (extends Phase 1 nodegraph work, commit `8d8a9db`).

## Goal

Replace the strict linear-chain workflow dispatcher in
`confflow.workflow.engine.run_workflow` with a DAG-aware dispatcher built on
`graphlib.TopologicalSorter`, while keeping the existing `run_workflow(input_xyz,
config_file, work_dir, ...)` entry point and its behaviour 100% backward
compatible.

## Why

The Phase 1.2-1.5 nodegraph visual layer already serialises workflows as a
DAG (nodes + edges). The dispatcher behind the scenes still only walks a
linear `steps` list. Without this refactor the visual DAG is a lie. With it,
fan-out (one conformer step feeding two optimisers) and fan-in (two conformer
ensembles merged before a single SP refinement) become first-class workflow
shapes.

## Schema change

A new Pydantic model `StepConfig` in `confflow/core/models.py`:

```python
class StepConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    type: str                       # "confgen" | "calc" | ...
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
```

- `inputs` lists upstream step names (DAG edges from predecessor to this step).
- `outputs` lists paths the step will write. Optional, used for the
  `step_handlers.inputs` validation hint, not for runtime wiring.
- A step with empty `inputs` is a DAG root (depends on the global workflow
  `input_xyz`).

`StepConfig` is **not** wired into the existing YAML loader in this phase.
The YAML shape stays a plain list of `dict`s; the engine builds the DAG
implicitly:

1. If **any** step has a non-empty `inputs` field, treat the workflow as a
   DAG: build a `TopologicalSorter` from `step["inputs"]`.
2. If **no** step has `inputs`, treat the list as a linear chain: step `i`
   implicitly depends on step `i-1` (existing behaviour).

This keeps every existing YAML valid and every existing test green.

## Handler signature change

`confflow/workflow/step_handlers.py`:

```python
def run_confgen_step(
    step_dir: str,
    inputs: list[str],
    params: dict[str, Any],
    input_files: list[str],
) -> str: ...

def run_calc_step(
    step_dir: str,
    inputs: list[str],
    params: dict[str, Any],
    global_config: dict[str, Any],
    root_dir: str,
    steps: list[dict[str, Any]],
    failure_tracker: FailureTracker,
    step_name: str,
) -> str: ...
```

The old `current_input: str | list[str]` parameter is **gone**; replaced by
`inputs: list[str]`. The `engine` now passes the full list of predecessor
output paths. Internally:

- Primary predecessor = `inputs[0]`.
- If `len(inputs) > 1`, log a `WARNING` that fan-in is partially supported
  (we still only feed `inputs[0]` to `ChemTaskManager.run`).

The pre-existing `step_handlers` tests at
`tests/test_step_handlers.py` use `current_input=` — they'll need a one-line
rename. The pre-existing list-input test (`test_list_input_uses_first_file`)
keeps working unchanged because it already passes a list.

## Engine change

`confflow/workflow/engine.py`:

The body of `run_workflow` is split into two phases:

1. **Setup phase** (unchanged): load config, set up work dir, checkpoint,
   stats, failure tracker, multi-input consistency check.
2. **Dispatch phase** (refactored): new private function
   `_dispatch_steps(steps, root_dir, ...)` that:
   - Builds a `TopologicalSorter` over `step["name"]` -> `set(step.get("inputs", []))`.
   - If `inputs` is empty for every step, inserts implicit linear edges
     `step[i-1].name -> step[i].name`.
   - On `prepare()` calls `CycleError` -> wrapped as a `ConfFlowError` with
     a clear message listing the cycle nodes.
   - Iterates in `static_order()` with **deterministic tie-breaking by
     `sorted(step_name)`** when multiple nodes become ready at once.

Each dispatch step uses the public `run_confgen_step` / `run_calc_step` from
`step_handlers.py` (replacing the private `_run_*` duplicates that lived in
`engine.py` and were kept for historical reasons). The shared setup
(`print_step_header_block`, stats, checkpoint, failure tracking) is factored
into a small helper `_run_one_step` that returns the step's output path.

The CLI / resume / multi-input / pre-existing tests stay green because the
public `run_workflow` signature is unchanged.

## Tests

New file `tests/test_dag_engine.py`:

1. **`test_dag_fan_out_one_conformer_two_optimizers`** — one confgen step
   with `outputs=[search1, search2]` (or a copy) feeding two `calc` steps.
   Verifies both `calc` steps run and both `output.xyz` files are written.
2. **`test_dag_fan_in_two_conformers_one_optimizer`** — two `confgen`
   steps feeding one `calc` step with `inputs=[s1, s2]`. Verifies the
   calc step uses the first input (s1) and a "fan-in partially supported"
   warning is logged.
3. **`test_dag_cycle_raises_conf_flow_error`** — three steps
   `A -> B -> C -> A`. Engine raises a `ConfFlowError` (or
   `RuntimeError` subclass) with a message mentioning "cycle" or "CycleError".
4. **`test_dag_linear_backward_compat`** — a 3-step linear chain with
   **no** `inputs` fields, runs through the DAG dispatcher, behaves
   exactly like the old linear dispatcher. Uses the same fixtures as
   `test_engine.test_run_workflow_full_and_resume`.
5. **`test_dag_deterministic_tie_breaking`** — two sibling `calc` steps
   with no dependency between them; running twice produces the same
   dispatch order in both runs.

New file `tests/data/wf_dag.yaml` — fan-out workflow used by the acceptance
run.

## Acceptance run

```bash
cd src/jobdesk_app/confflow
PYTHONPATH=src/jobdesk_app/confflow:src python -m pytest tests/test_dag_engine.py -q --no-header
```

Plus a manual smoke:

```bash
PYTHONPATH=src/jobdesk_app/confflow:src python -m pytest \
  --pyargs confflow.tests.test_dag_engine.test_dag_acceptance_run \
  -q --no-header
```

This drives `tests/data/wf_dag.yaml` through `run_workflow` with the
ChemTaskManager / confgen.run_generation patched (same fixture pattern as
`test_engine.test_run_workflow_full_and_resume`). Verifies `tmp_dag_run/`
gets `s01/search.xyz`, `s02/output.xyz`, `s03/output.xyz` and
`workflow_stats.json`.

## Docs

`docs/DAG-EXECUTION.md` — motivation, schema change, behaviour change,
fan-in/fan-out YAML examples, and the deterministic tie-breaking rule.

## Risk

- Existing `step_handlers` tests use the `current_input=` kwarg. They need
  a rename to `inputs=`. Mechanical change.
- `_run_calc_step` / `_run_confgen_step` private helpers in `engine.py`
  become dead code. Removed in this phase.
- Checkpoint file format is unchanged, so resume on a partially completed
  DAG is not supported in this phase. The resume code path falls back to
  "resume from end" if the checkpoint is found mid-DAG. Documented as a
  known limitation in DAG-EXECUTION.md.

## Out of scope

- Parallel execution of independent DAG branches. Single-threaded for now.
- Cycle-aware recovery (replanning). The cycle just raises.
- Visual DAG ↔ YAML round-trip. Phase 1.5 already round-trips; this phase
  only touches the dispatcher.