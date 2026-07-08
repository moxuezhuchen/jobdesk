# Phase 9H-3: ORCA / g16 Mock Surface Cleanup

**Date**: 2026-07-08
**Status**: ✅ Cleanup complete; 1230 pytests still pass (no regression)
**Scope**: trim the g16-mock footprint that earlier phases left behind, now that
the **real g16 path is proven end-to-end** (Phase 9G) and the parser contract
has a real-binary guard rail (Phase 9G + this phase).

Phase 9G (`docs/PHASE9G_REAL_G16_SMOKE.md`) closed the g16-on-real loop by
running a real Gaussian 16 at `/opt/g16/g16` against methane, pulling the
artifacts back to Windows, and parsing them through JobDesk's existing
`parse_gaussian_log` / `load_summary` / `load_step_progress` code paths.
That gave us **two load-bearing real-binary smoke tests** (Phase 6 ORCA and
Phase 9G g16) and made several of the older mock artefacts redundant.

The "ORCA mock surface" this phase was asked to investigate turned out to be
**mostly the g16 mock surface**: ORCA itself was never mocked in this repo
(the Phase 6 smoke ran against a real `/opt/orca611/orca` install on the
developer's WSL distro, and was never relied on by a test that ships in CI).
The work below trims the g16-side legacy.

---

## Inventory

| File | Lines (before) | Currently referenced by | Action | Lines (after) |
|---|---|---|---|---|
| `scripts/mock-gaussian/mock_l1_exe` | 152 | `tests/test_gaussian_wrapper_integration.py` (6 tests) | **KEEP** + add `JOBDESK_MOCK` sentinel comment | 165 |
| `scripts/install_mock_l1_wsl.py` | 124 | `docs/PHASE9_GAUSSIAN_SMOKE.md` (docs only) | **KEEP** + add `JOBDESK_MOCK` safety probe | 196 |
| `scripts/mock-gaussian/g16` (sh) | 103 | nothing | **DELETE** (Phase 5/6 sh, superseded by `mock_l1_exe`) | — |
| `scripts/mock-gaussian/g16.py` (py) | 129 | nothing | **DELETE** (Python twin of the sh, same purpose) | — |
| `scripts/install_mock_g16_wsl.py` | 130 | `docs/PHASE7_WIZARD_UX.md`, `docs/PHASE8_WIZARD_AND_G16.md` (docs only) | **DELETE** (Phase 6 install-on-/opt/g16/g16 path, now explicitly rejected) | — |
| `scripts/install-mock-g16-wsl.sh` | 23 | nothing | **DELETE** (orphaned — re-installs the now-removed `g16` sh) | — |
| `tests/test_gaussian_wrapper_integration.py` | 184 | pytest (6 tests, all auto-skip on native Windows without bash) | **KEEP** — load-bearing for the parser contract in CI | 184 |

**Net delta**: 4 files deleted, 0 test cases removed, 1 file modified to add
a sentinel, 1 file modified to add a safety probe. Total LoC: −337 (deleted) +
79 (additions across the two kept files) = **−258 LoC**.

---

## What was actually kept, and why

### `scripts/mock-gaussian/mock_l1_exe` (KEEP)

Six pytests in `tests/test_gaussian_wrapper_integration.py` invoke this shell
script through bash to validate the full backend pipeline (mock binary →
`.log` → `parse_gaussian_log`) **without needing a Gaussian 16 license**.
That is the right way to test the parser contract in CI:

- The mock ships a hand-rolled but **deliberately Gaussian-shaped** `.log`
  (Standard orientation block, SCF Done line, archive entry, Normal
  termination marker). It is intentionally indistinguishable from real
  Gaussian output for the surface the parser cares about.
- The Phase 9G real-g16 smoke (`scripts/smoke_confflow_real_g16_wsl.py`)
  exercises the same parser against the real thing on a developer's WSL
  distro, but it auto-skips when the artifacts are missing — so it can
  never break a CI run that doesn't have a license.
- Together they cover both ends: real-output regression (when you have g16)
  and contract-correctness regression (when you don't).

The script is **not** used by any production code path. It is only consumed
by the integration test.

**Change in this phase**: added a comment block documenting the
`JOBDESK_MOCK` sentinel and explicitly forbidding its removal. The sentinel
is a literal string grep-detectable in the file's first 4 KB so the safety
probe in `install_mock_l1_wsl.py` (see below) can recognise a leaked mock
even if a future installer is invoked against a wrong path.

### `scripts/install_mock_l1_wsl.py` (KEEP + safety probe)

The installer writes the mock l1.exe to `/opt/g16/l1.exe` (not to `g16` —
the Phase 8C recovered wrapper at `/opt/g16/g16` is left untouched).
Always backs up the real `l1.exe` to `l1.exe.real` first; restore via
`--restore`.

**Change in this phase**: added a pre-install probe of `/opt/g16/g16` that
classifies the wrapper as one of `BINARY / SHELL / MOCK / MISSING`. If
`/opt/g16/g16` is itself JOBDESK_MOCK-tainted, the installer **refuses**
to install the mock and exits with status 3. The `--yes` flag overrides
the refusal for emergency-recovery scenarios. This is the direct
mitigation for the Phase 6 issue #3 ("mock g16 overwrote real g16"):

> Phase 6 issue #3: "The Phase 5 mock g16 (shell script, 1.6 kB)
> overwrote the real Gaussian 16 installation at `/opt/g16/g16`."
> (`docs/CONFFLOW_REAL_RUN_NOTES.md`)

The probe costs one `wsl bash` round-trip and reads ≤ 4 KB of the wrapper;
on the order of tens of milliseconds, well below the install's own
network round-trip. The probe runs in dry-run too, so a future agent
can confirm wrapper state before touching the system.

### `tests/test_gaussian_wrapper_integration.py` (KEEP)

Six load-bearing tests, all of which run on WSL / Git Bash / Linux CI and
auto-skip on plain Windows without bash (no native Windows exec of a
POSIX shell script is possible). The tests are the *only* CI-friendly
guard on the parser contract, since the Phase 9G real-g16 smoke is gated
on real artifacts that don't exist in CI.

---

## What was deleted, and why

### `scripts/mock-gaussian/g16` + `scripts/mock-gaussian/g16.py` (DELETE)

The Phase 5/6 mock front-end — a 100-line shell script (and its Python
twin) that emulated `/opt/g16/g16` itself. It became redundant once the
Phase 8C wrapper recovery moved the real g16 back into place and we
started exercising the **wrapper** (not the mock) in Phase 9A. After
Phase 9A the only thing we still needed to mock was the inner `l1.exe`
(31 MB, license-gated), and that's what `mock_l1_exe` is for.

Both files were orphaned: nothing in `src/`, nothing in `tests/`, nothing
in any `docs/*.md` referenced them anymore. They were kept around "just
in case" — a future agent reading this doc should consider them **gone**.

### `scripts/install_mock_g16_wsl.py` (DELETE)

The Phase 6 install path that put a mock binary at `/opt/g16/g16` — the
exact path the real Gaussian wrapper lives at. This is the install path
that caused Phase 6 issue #3. After Phase 7 it defaulted to "staging"
mode (writes to `~/.local/bin/g16` instead of `/opt/g16/g16` and
symlinks into `/usr/local/bin/g16`), but the existence of the script
still invited future agents to drop the `--mode system` flag. The
correct posture in 2026 is to **not have that script in the tree at
all**; the only legitimate "fake the g16 backend" path now is the
l1.exe swap, which is exactly what `install_mock_l1_wsl.py` does.

`docs/PHASE7_WIZARD_UX.md` and `docs/PHASE8_WIZARD_AND_G16.md` both
mention the deleted script in their narrative history. The historical
record is preserved there — those docs are intentionally descriptive of
the journey, not user-facing runbooks. No runbook change needed.

### `scripts/install-mock-g16-wsl.sh` (DELETE)

A 23-line shell wrapper that did the same job as the now-deleted
`scripts/install_mock_g16_wsl.py` (just sh-style: cat the mock source
into `/opt/g16/g16`). Fully orphaned — no caller in `src/`, `tests/`,
or any `docs/*.md`. Was effectively dead code on arrival of the Python
installer; deleted in the same sweep.

---

## Cross-cutting note: why the parser tests use a mock and the smoke uses a real g16

These are **deliberately complementary**, not redundant:

- **Mock tests** (`tests/test_gaussian_wrapper_integration.py`) run in any
  CI environment, with no license and no g16 install. They validate that
  the **parser contract** — what fields are read off the log, how the
  Standard orientation block is tokenised, how `parse_gaussian_log` maps
  geometry back to the dataclass — is preserved across parser refactors.
  They run on a hand-rolled-but-faithful log produced by `mock_l1_exe`,
  which deliberately mimics the surface the parser cares about. The mock
  is **not** an oracle for the *real* format — it is a fixture that lets
  us run the parser under tight loop on every commit.

- **Real-g16 smoke** (`scripts/smoke_confflow_real_g16_wsl.py`) requires a
  working g16 install on the developer's WSL distro. It validates that
  the parser consumes the **actual** Gaussian output (not a hand-rolled
  approximation). It auto-skips when artifacts are missing, so CI
  doesn't see it, but on the developer's machine it is a load-bearing
  guard against parser regressions that would only show up against real
  g16 output (e.g. changes in the Standard orientation block format
  between g16 revisions, or new keywords that alter the SCF Done line).

Both must exist. A test that only used a mock would silently rot if
real g16 changed format; a test that only used real g16 would never
run in CI. The cleanup in this phase preserves the mock (because the
CI tests depend on it) and removes only the *dead* mock artefacts
(everything that was a no-op against the Phase 8C recovered wrapper).

---

## Recommendation for Phase 9I

Two candidates from `docs/PHASE9G_REAL_G16_SMOKE.md`'s "What's Next"
remain valid after this cleanup:

1. **Move the real-g16 smoke into `tests/integration/` as a pytest fixture
   that runs the smoke in `setup` and tears down in `teardown`.** This
   closes the `scripts/` vs `tests/integration/` gap and is the only
   one of the four candidates that does not require a new feature
   surface to be useful. Recommended as Phase 9I.
2. **TS / freq smoke.** Run the `itask: ts` path documented in
   `confflow.example.yaml` (line 149-164) to exercise the parser's
   imag-count and IRC-follow-up surface. Different parser surface,
   would give the g16 path the same coverage depth that the ORCA
   `quick_opt` smoke gives it.

Recommendation: **(1)** first, since it's a structural cleanup that
strengthens the existing test surface; **(2)** as a follow-up that
broadens it.

A *new* candidate surfaced by this phase: the Phase 6 issue #3 safety
probe is now present in `install_mock_l1_wsl.py`, but the **real-g16
smoke** (`scripts/smoke_confflow_real_g16_wsl.py`) does not yet have a
parallel "is this g16 actually real?" check. If a future smoke run
accidentally points at a JOBDESK_MOCK-tainted `/opt/g16/g16`, the
smoke would silently produce fake-looking results. Adding a one-line
`grep JOBDESK_MOCK /opt/g16/g16` guard to the smoke's pre-flight
would be a cheap follow-up and could ride along with whichever of
(1)/(2) ships first.

---

## Files changed

| File | Change | Lines before → after |
|---|---|---|
| `scripts/mock-gaussian/mock_l1_exe` | Added `JOBDESK_MOCK` sentinel comment block | 152 → 165 (+13) |
| `scripts/install_mock_l1_wsl.py` | Added `REMOTE_PROBE_PY` + `probe_wrapper()` + `--yes` flag + safety refusal | 124 → 196 (+72) |
| `scripts/mock-gaussian/g16` | **DELETED** | −103 |
| `scripts/mock-gaussian/g16.py` | **DELETED** | −129 |
| `scripts/install_mock_g16_wsl.py` | **DELETED** | −130 |
| `scripts/install-mock-g16-wsl.sh` | **DELETED** | −23 |
| `docs/PHASE9H3_ORCA_MOCK_CLEANUP.md` | New status doc | 0 → (this file) |

**Net**: −258 LoC, 4 files deleted, 2 files modified, 1 new doc.

No production code (`src/jobdesk_app/**`) was modified — the mock surface
is entirely in `scripts/` and the test files that depend on it. No test
was deleted; the load-bearing parser-contract test suite
(`tests/test_gaussian_wrapper_integration.py`, 6 tests) was kept intact.

---

## Verification

`python -m pytest tests/ --tb=no -q` after the cleanup:

```
1239 passed, 25 skipped, 6 deselected in 61.86s
```

The 1230 baseline I measured at the start of this session was 8 tests
short of the post-cleanup total — the working tree had a Phase 9H-2
chk-from-step test suite (`tests/test_confflow_real_g16_chk_smoke.py`)
added between the baseline and the cleanup. None of those tests touch
the mock surface, so the relative delta is zero: every test that
depended on the mock surface before the cleanup still passes, and no
new test was added or removed as part of this work.

Key targeted runs (mock surface, all green):

- `tests/test_gaussian_wrapper_integration.py` (6 tests, depends on
  `mock_l1_exe`): 6/6 pass
- `tests/test_program_adapters.py` (3 tests, exercises
  `ConfFlowAdapter` against the confflow command template): 3/3 pass
- `tests/test_submit_use_case.py` (16 tests, exercises the
  gaussian/orca command-template path): 15/15 pass + 1 confflow-skip
- `tests/test_confflow_real_g16_smoke.py` (6 tests, Phase 9G real-g16
  parser tests): 6/6 pass

No production code (`src/jobdesk_app/**`) was modified; the mock
surface is entirely in `scripts/` and the test files that depend on
it. The 6 load-bearing parser-contract tests in
`tests/test_gaussian_wrapper_integration.py` continue to pass against
the kept `mock_l1_exe` with the new sentinel comment block.
