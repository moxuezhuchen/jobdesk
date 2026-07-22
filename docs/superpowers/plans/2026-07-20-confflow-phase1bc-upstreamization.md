# ConfFlow Phase 1b/1c upstreamization plan

## Objective

Move the smallest production-relevant DAG capability from JobDesk's vendored
ConfFlow snapshot into upstream ConfFlow, prove that the external engine honors
explicit `inputs` edges, and keep the vendored subtree until a later deletion
gate explicitly passes.

## Confirmed baseline

- JobDesk baseline: `7fff020` (`revert: restore vendored ConfFlow pending migration decision`).
- Approved external package baseline: `confflow==1.3.0`.
- Upstream v1.3.0 source baseline: `5bc4c40` (`v1.3.0`).
- External v1.3.0 has no `confflow.workflow.dag` module and its workflow engine
  executes steps in list order.
- JobDesk application code does not directly import the vendored DAG helpers,
  but JobDesk emits `inputs` edges into workflow YAML and remote ConfFlow must
  preserve their execution semantics.
- `confflow.calc.task_execution` already exists upstream. The vendored
  `calc.async_exec` and DAG introspection classes have no non-vendored JobDesk
  production consumers.

## Safety boundaries

- Do not modify or delete `src/jobdesk_app/confflow/` during this phase.
- Do not modify the four protected vendored DAG/task-execution files.
- Do not touch the user's pre-existing uncommitted JobDesk files or the
  untracked `=` file.
- Do not run g16, ORCA, WSL smoke, WSL shutdown, apt, or pip installation.
- Do not write Gaussian executables or use network Git operations.
- Upstream implementation must be performed in an isolated writable clone of
  the clean ConfFlow v1.3.0 source.

## Public API decision

Add one module, `confflow.workflow.dag`, with only these public functions:

1. `build_step_graph(steps)`
   - canonicalize missing step names deterministically;
   - reject duplicate names;
   - normalize `inputs` into predecessor lists;
   - return predecessors, the step lookup, and declared inputs.
2. `topo_order(predecessors)`
   - return deterministic topological waves;
   - reject cycles with `ConfFlowError`.

Do not upstream `DAGStep`, `DAGGraph`, `WorkflowDAG`,
`resolve_step_outputs_map`, the module/package compatibility shim, or vendored
`calc.async_exec` unless a new consumer is demonstrated.

## Upstream implementation tasks

1. Add `confflow/workflow/dag.py` as a single module.
2. Integrate the helpers into `confflow/workflow/engine.py`:
   - explicit `inputs` select predecessor outputs;
   - no declared `inputs` anywhere preserves the current linear workflow;
   - unknown predecessors and dependency cycles fail before step execution;
   - execution order is deterministic;
   - state/resume and injected `CalcExecutor` behavior remain compatible.
3. Define fan-in behavior using the existing step-handler input contracts.
   Multiple predecessor outputs must be passed as a list; unsupported calc
   fan-in must fail with the existing clear validation error rather than
   silently selecting the previous list item.
4. Keep `confflow.calc.task_execution` unchanged unless an engine-level test
   proves a required adjustment.

## Upstream tests

Add focused tests that verify behavior, not only artifact existence:

- helper chain, diamond, deterministic ready-wave ordering;
- duplicate name, unknown predecessor, and cycle rejection;
- explicit fan-out: both children consume the same predecessor output;
- explicit fan-in: the successor receives both predecessor outputs;
- linear backward compatibility when no step declares `inputs`;
- mixed explicit/implicit declarations have a documented deterministic rule;
- injected `CalcExecutor`, workflow state, and resume regressions remain green.

Only pure Python tests are allowed in this phase. No chemistry executable or
WSL smoke may be invoked.

## JobDesk follow-up after an upstream wheel exists

1. Build and verify a new ConfFlow wheel in a separate authorized phase.
2. Change the DAG round-trip smoke import from the vendored namespace to
   `confflow.workflow.dag`.
3. Strengthen the Phase 3A DAG test to assert predecessor-output lineage.
4. Run the approved non-g16 ConfFlow/JobDesk test set.
5. Re-run the vendored deletion audit. Delete the vendored subtree only when
   external imports, behavior tests, packaging, and documentation all pass.

## Completion criteria for this implementation phase

- The isolated upstream clone contains the minimal DAG module and engine
  integration.
- Focused DAG tests pass without g16, ORCA, WSL, network, or package installs.
- Existing relevant upstream engine/state/executor tests pass.
- No JobDesk vendored or user-owned uncommitted file is changed.
- The result is reviewed before any wheel publication or vendored deletion.
