# JobDesk / ConfFlow remediation final closeout

Date: 2026-08-31

Plan: `docs/superpowers/plans/2026-08-19-jobdesk-confflow-full-remediation.md`

Verdict: **APPROVED — all nine gates complete**.

## Released identities

- JobDesk `v0.7.10`
  - merge: `54f7735698f148371adb70397813c04ea569c245`
  - wheel SHA-256: `6e1c6b42f8cdbb939a57442e6b8b30b168c7bd6c5cf550cac958acd6e83992c3`
- ConfFlow `v2.1.6`
  - merge: `45bfac11f721b2152eeff5ee26e50463fcc6f657`
  - wheel SHA-256: `d8fe44611ec128fece79309f42792b716c1f2f59871b5aab4024f3d136f75548`
  - dependency lock SHA-256: `8b77eaaf8482b8f3ce56e57d197d839a4b219c53e71d5b2e016f6c96e5153409`
  - wheelhouse manifest SHA-256: `824accf85c8655c4d870b75900101970ff54f5753d2602e7e5c5a4f88909f95c`

## Strict real-launcher acceptance

Candidate 3 run:
`jd0710-cf216-real-methane-candidate3-9c42f6a1`.

- atomic submit markers: 1
- submitted tasks: 1
- dispatch attempts: 1
- automatic retry/resume/redispatch: 0
- control trajectory: `prepared → queued → running → checkpointed → completed`
- final producer status: `completed`; JobDesk status: `remote_completed=1`
- manifest-declared downloaded artifact: `g16_opt/output.xyz`
  - size: 269 bytes
  - SHA-256: `80dc8335046084e993161be1f631a1995cd6715512d5d74fa0e6e8888393c6f2`
- Gaussian log:
  - size: 37820 bytes
  - SHA-256: `687e7f8c062919078b52fdf3d262f2a37976ae26847d782b514e0f8739938d3f`
  - contains optimization completion and normal Gaussian 16 termination
- run summary: one input conformer, one final conformer, one completed `g16_opt`
  step, final energy `-40.51838331`
- G16 and production identities were byte-for-byte unchanged across the real
  workload.

The original acceptance harness correctly observed a successful manifest-driven
JobDesk download but then incorrectly required metadata files that were not part
of the control artifact manifest. It stopped after the workload had completed.
No second submit occurred. A same-run recovery verifier copied and hashed the
remote manifest, summary, workflow metadata, output, and Gaussian log, then
rechecked the protected identities. Independent review approved this evidence.
This closeout does not claim that JobDesk downloaded the metadata or Gaussian
log; JobDesk downloaded the sole artifact declared by the producer manifest.

## Shared source environment

The authorized `/opt/ConfFlow/.venv` replacement is complete.

- source HEAD: `c6a4263bf3ec84669fd5279ec336b10ab2e18c9f`
- source version: ConfFlow `2.0.0`
- editable binding: `/opt/ConfFlow`
- runtime dependencies installed from the repository's hashed 2.0.0 lock and
  the verified offline wheelhouse
- external-directory import and `pip check`: pass
- focused source-environment suite: `101 passed, 1 skipped`
- new rollback: `/opt/ConfFlow/.venv.rollback-20260831T1804-v200`
- historical rollback retained: `/opt/ConfFlow/.venv.previous-c6a4263`

The source tree's pre-existing modified test and rollback directories were
preserved. No source change was reset, stashed, cleaned, or deleted.

## Production promotion

The production switch was one atomic symlink replacement.

- `/usr/local/bin/confflow` → `/opt/confflow-current`
- `/opt/confflow-current` →
  `/opt/confflow-2.1.6-prod-venv/bin/confflow`
- production version: `2.1.6`
- executable SHA-256:
  `88d089564c46e7af83ce0e85643345faf6098d542b735b86c9dcc44fadf54656`
- install provenance status: `verified`
- install provenance SHA-256:
  `45057562d669acc69500f4c01ab48f3a0b07f944772777b9efc7dd17148052a1`
- promotion record:
  `/opt/confflow-promotions/20260831T1807-v2.1.6.json`

Post-switch non-compute smoke passed for:

- ConfFlow version and capability schema v4
- producer build, wheel, executable, and install provenance identity
- control protocol capabilities
- canonical configuration parsing
- JobDesk `v0.7.10` remote probe
- JobDesk producer-owned configuration contract resolution and validation

The actual JobDesk `wsl` server binding now names the exact production
executable `/opt/confflow-2.1.6-prod-venv/bin/confflow`. Its previous config is
preserved as
`servers.yaml.pre-confflow-2.1.6-20260831T1817`.

## Rollback and protected G16 identity

- promotion rollback link:
  `/opt/confflow-current.pre-v2.1.6-20260831T1807`
- rollback target:
  `/opt/confflow-2.0.0-prod-venv/bin/confflow`
- rollback version: `2.0.0`
- rollback entrypoint SHA-256:
  `5c8775167ff5aa0065cb01a7e585b7a827e5b4f531a1ada67f5c8a291274560c`

G16 identities were checked before and after promotion:

- `/opt/g16/g16`:
  `9dd1b1a7495c313954b5243ea427389adac55e55829ab4934f89b8e46fb0c8d5`
- `/opt/g16/l1.exe`:
  `8e0b3055b7529293109112c9d0ce6f26f78a0ace5282102f1ae75ae0e9b64152`
- `/opt/g16/bsd/g16.profile`:
  `3c657a7d07ab22be53afcdd70e4a4456a6ddcd398d8be341d9625c5b4da670ed`

All three hashes were unchanged.

## Nine-gate audit

| Gate | Verdict | Completion evidence |
|---|---|---|
| 0 | PASS | Exact refs, protected trees, baselines, and evidence inventories retained. |
| 1 | PASS | Clean release environments plus authorized shared source `.venv` replacement, identity tests, focused tests, and retained rollback. |
| 2 | PASS | Enforced warning/coverage gates and GUI contract regression coverage remain green. |
| 3 | PASS | Producer-owned canonical configuration contract and compatibility facades released and verified. |
| 4 | PASS | Per-server exact executable/producer binding, remote validation, and compatibility matrices passed. |
| 5 | PASS | SQLite-authoritative decisions, durable reconciliation, single-submit marker, and zero redispatch verified. |
| 6 | PASS | Engine/worker decomposition and producer-owned control state passed release and compatibility tests. |
| 7 | PASS | Connection budgets, monitor separation, and GUI ownership boundaries passed stress/regression coverage. |
| 8 | PASS | Immutable releases, side-by-side validation, strict real G16 run, production promotion, post-switch smoke, provenance, and rollback all passed. |

No unresolved blocker remains. Historical documents retain their original
measurements; this file and the current-status sections of the README,
README.zh, architecture document, and execution plan describe the final
deployed reality.
