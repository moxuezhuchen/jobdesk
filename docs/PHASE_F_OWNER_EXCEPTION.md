# Phase F owner exception — 2026-08-11

The repository owner authorized Phase F directly because there are no active
consumers to preserve. This is a product decision, not a conclusion derived
from the compatibility-period evidence.

## Decision and scope

- Retire JobDesk's legacy ConfFlow SSH backend, its `legacy` durable-backend
  state, automatic backend negotiation, and v1.4.6 rollback admission.
- Require the published ConfFlow control protocol for every new or restored
  ConfFlow run. A run without valid control state, including a historical
  legacy run, fails closed and is not resubmitted.
- Retire the producer's optional `confflow-agent` daemon separately in the
  paired ConfFlow change.
- Keep the current control worker, fixture-only agent, and generic JobDesk
  scheduler infrastructure. They are not the retired compatibility backend.

## Evidence boundary

The compatibility evidence index remains an immutable release-boundary record:
its `COMPATIBILITY PERIOD CONTINUES` and `phase_f_ready=false` fields mean
"not evidence-proven by a full measured period." This owner exception does not
reinterpret candidate, synthetic, historical, failed, or supplemental bundles
as stable samples, and it does not claim a full compatibility period.

## Breaking-change notice

`JOBDESK_CONFFLOW_BACKEND=auto|legacy`, legacy serialized handles, and existing
`control_backend.json` files whose backend is `legacy` are unsupported after
this change. They fail closed. No user data, remote state, `/opt` installation,
or agent SQLite is deleted by this repository change.

Main-branch merge, release, tagging, and deployment remain separate owner
actions.
