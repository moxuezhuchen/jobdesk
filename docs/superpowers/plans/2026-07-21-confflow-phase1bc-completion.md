# ConfFlow Phase 1b/1c completion plan

## Goal

Finish the upstreamization loop from the authoritative WSL repository at
`Ubuntu-24.04:/opt/ConfFlow`: validate the merged minimal DAG implementation,
produce an offline wheel, prove JobDesk works against that external wheel, and
make an evidence-based vendored deletion-gate decision.

## Safety and ownership

- Preserve the pre-existing `/opt/ConfFlow` changes in
  `confflow/calc/executor.py`, `tests/test_calc_executor_protocol.py`, and
  `tests/test_simple.py.disabled`; classify their semantic content separately
  from CRLF-only noise before deciding whether they belong to this phase.
- Preserve all listed JobDesk user changes and the untracked `=` file.
- Do not modify or delete `src/jobdesk_app/confflow/` or its four protected
  DAG/task-execution files.
- Do not run g16, ORCA, chemistry smoke, WSL shutdown, apt, WSL pip install, or
  network Git operations. Do not write Gaussian executables.
- Do not stage, commit, push, or delete vendored code during execution. A later
  publication action requires a clean reviewed diff and must respect the prior
  rule that Git commit/push is not run inside WSL.

## Execution

1. Re-audit `/opt/ConfFlow` status and review the complete semantic diff while
   ignoring line-ending-only noise. Confirm the Phase 1b/1c write set is only:
   `confflow/config/models.py`, `confflow/workflow/engine.py`,
   `confflow/workflow/dag.py`, and `tests/test_workflow_dag.py`.
2. Review the DAG engine implementation for deterministic ordering, explicit
   `inputs`, roots, fan-out, fan-in, disabled steps, state/resume behavior, and
   legacy linear fallback. Fix only confirmed defects in the four-file write
   set and add focused pure-Python regression coverage when needed.
3. Run Ruff check/format check and the focused upstream suite. Run the broader
   pure-Python suite only when it can be selected without invoking chemistry
   executables or smoke tests.
4. Build a wheel from `/opt/ConfFlow` entirely offline using already-installed
   build tooling. Do not install packages in WSL. Record the wheel path,
   filename, metadata version, and SHA-256.
5. Install that exact local wheel into
   `C:\dft\tool\verify-venv` on Windows with `--no-deps` and no index/network.
   Verify `confflow.__version__`, module origin, and imports of
   `confflow.workflow.dag.build_step_graph` and `topo_order`.
6. Change `scripts/smoke_confflow_dag_round_trip.py` to import the DAG helpers
   from external `confflow.workflow.dag`, without running the smoke script.
   Strengthen the Phase 3A fan-out test so it proves both branches consume the
   same predecessor output rather than merely checking output-file existence.
7. Run the approved JobDesk non-g16 external-ConfFlow regression set, including
   DAG lineage behavior and CalcExecutor Protocol coverage. Ensure JobDesk's
   editable `src` path is not mistaken for a vendored ConfFlow import.
8. Re-audit all Phase 1b/1c consumers and issue the final deletion gate. The
   gate passes only if every required runtime/test/import path uses the external
   wheel and no protected vendored-only behavior remains required.

## Acceptance criteria

- Upstream focused tests and static checks pass.
- A reproducible local wheel is built and its hash recorded.
- Windows verification imports the exact installed wheel from site-packages.
- Approved JobDesk non-g16 tests pass.
- Existing user-owned changes remain untouched and no files are staged.
- The final report distinguishes implementation completion from publication
  and gives an explicit `PASS` or `BLOCKED` vendored deletion gate with exact
  blockers.
