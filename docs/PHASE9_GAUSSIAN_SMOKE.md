# Phase 9A: Gaussian Wrapper + Mock l1.exe Smoke Test

**Date**: 2026-07-07
**Status**: ✅ End-to-end pipeline validated
**Tests**: 6 new, **1043 total passing**

This phase completes the loop that was left dangling at the end of
Phase 8C: the recovered `g16` wrapper works, but the real `l1.exe`
binary dumps core without a license.  Rather than block on a license,
we built a **mock** `l1.exe` that produces a properly-formatted
Gaussian log file so the downstream pipeline (parse_gaussian_log,
wizard result display) can be exercised end-to-end.

The recovered `/opt/g16/g16` wrapper is the front-end; the mock lives
at `/opt/g16/l1.exe` (with the real binary backed up as
`/opt/g16/l1.exe.real`).

---

## What was added

### `scripts/mock-gaussian/mock_l1_exe`

A POSIX-sh shell script that mimics the **front-end** of Gaussian's
`l1.exe` binary:

- Reads `<basename>.gjf` from the current working directory.
- Parses route line (`#`), title, charge/multiplicity, and atom
  coordinates.
- Writes:
  - `<basename>.log` with Normal termination, SCF Done line, Standard
    orientation block (geometry), and an archive section.
  - `<basename>.xyz` (for ConFlow's geometry assembly).
- Honors `JOBDESK_MOCK_L1_DELAY` for tests (skip the sleep entirely).
- Produces Gaussian-realistic output so `parse_gaussian_log` and
  downstream consumers treat it as a real run.

### `scripts/install_mock_l1_wsl.py`

Install / restore script (mirrors Phase 7's `install_mock_g16_wsl.py`):

- **Install**: backs up the real `l1.exe` to `l1.exe.real` if not
  already backed up, then writes the mock in its place.
- **Restore** (`--restore`): copies `l1.exe.real` back over the mock.
- **Dry-run** (`--dry-run`): prints the plan without executing.
- Always prints a verification line (`file <dest>` + size).

### `tests/test_gaussian_wrapper_integration.py`

Six end-to-end tests, all passing on Windows + bash (Git Bash or WSL):

| Test | Verifies |
|---|---|
| `test_g16_wrapper_produces_normal_termination_log` | `.log` exists with "Normal termination" |
| `test_g16_wrapper_log_parses_to_gaussian_result` | `parse_gaussian_log` returns scf_energies, normal_termination, no error |
| `test_g16_wrapper_extracts_geometry` | geometry block extracts 5 atoms (C H H H H) for methane |
| `test_g16_wrapper_water_energy_is_hf` | different method → different mock SCF energy |
| `test_g16_wrapper_writes_result_xyz` | `<basename>.xyz` produced with correct atom count header |
| `test_g16_wrapper_exits_zero` | the mock exits cleanly |

The whole file skips if `bash` isn't on PATH (i.e. plain Windows
without Git Bash). On Linux CI / WSL / Git Bash all 6 tests run.

---

## How the integration test runs the mock

The native-Windows Python interpreter can't `subprocess.run` a POSIX
shell script directly (Windows returns "not a Win32 application" for
shebangs). The test:

1. Reads `mock_l1_exe` source from disk.
2. Normalises line endings to LF (`\r\n`/`\r` → `\n`).
3. Writes the normalised source to `tmp_path/_mock_l1.sh` with `newline="\n"`.
4. Converts the Windows path to a `/mnt/c/...` WSL path (no-op on Linux).
5. Invokes `bash <wsl_path> <basename.gjf>`.
6. Reads the produced `.log` and feeds it to `parse_gaussian_log`.

A `_to_bash_path()` helper handles the path translation:

```python
def _to_bash_path(p: Path) -> str:
    raw = str(p)
    if len(raw) >= 2 and raw[1] == ":":
        drive = raw[0].lower()
        rest = raw[2:].replace("\\", "/")
        return f"/mnt/{drive}{rest}"
    return raw.replace("\\", "/")
```

On plain Linux the function is a no-op (POSIX paths already).

---

## Detour: why the mock fell back to methane on first run

The first iteration of `mock_l1_exe` parsed geometry with:

```sh
GEO=$(awk 'NR>=9 && NF==4 {print; next} NF!=4 {exit}' "$GJF")
```

This worked when invoked via WSL from a `/tmp/...` (WSL native fs)
shell but **returned empty** when invoked from a `/mnt/c/...`
directory (Windows fs).  Through binary search we discovered:

- `awk 'NR>=9 {print; next}' $GJF` → works.
- `awk 'NR>=9 {print; next} NF!=4 {exit}' $GJF` → empty.
- `awk 'NR>=9 && NF==4 {print}' $GJF` → works.
- `awk 'NR>=9 {print} NF!=4 {exit}' $GJF` → empty.

Pattern: any second pattern with `exit` triggered an early shutdown.
The cause: GNU awk on this WSL image evaluates the *next* pattern's
condition when `next` is invoked.  When the input is exhausted, the
internal record is empty and `NF != 4` is true, so `exit` fires
*before* any of the previous `print`s have flushed.  The pipe driver
sees an empty result and the placeholder geometry fires.

Fix: switch to a counter that doesn't trigger on EOF:

```sh
GEO=$(awk -v max=64 'NR>=9 && NF==4 && cnt<max {print; cnt++; next} NR>=9+max {exit}' "$GJF")
```

`max=64` is generous enough for any real molecule.  Tests pass on
both Windows-via-WSL-bash and Linux-native bash.

---

## How to re-run the smoke on WSL

```bash
# 1. Install the mock (one-time, idempotent — won't overwrite the backup).
python scripts/install_mock_l1_wsl.py

# 2. Drive a calculation through the recovered wrapper.
cd /tmp
cat > methane.gjf <<'EOF'
%chk=methane.chk
%mem=1GB
%nproc=2
# b3lyp/6-31g(d) sp

methane

0 1
C   0.000000   0.000000   0.000000
H   0.629118   0.629118   0.629118
H  -0.629118  -0.629118   0.629118
H  -0.629118   0.629118  -0.629118
H   0.629118  -0.629118  -0.629118
EOF

# 3. Run it. The recovered /opt/g16/g16 wrapper execs /opt/g16/l1.exe (mock).
g16 methane.gjf

# 4. Inspect the output.
head -15 methane.log     # shows route, geometry, SCF Done
tail -10 methane.log     # shows Normal termination

# 5. (optional) Restore the real l1.exe when a license is available.
python scripts/install_mock_l1_wsl.py --restore
```

---

## What this enables

- **Wizard result download works**: when a wizard-submitted run finishes,
  the backend pulls `<basename>.log` and `<basename>.xyz` from the
  remote dir. With the mock in place the wizard sees Normal termination
  + SCF energy + geometry and renders them in the result pane.
- **End-to-end CI for the Gaussian flow**: even without a license, a CI
  job can validate that wizard → SFTP → `g16` → log download →
  `parse_gaussian_log` → result display all work.
- **Test fixtures for downstream views**: when we build a "view result
  detail" pane that needs a sample log, the mock is the source of that
  fixture.

---

## Files Changed / Added

| File | Change |
|---|---|
| `scripts/mock-gaussian/mock_l1_exe` | New mock shell script |
| `scripts/install_mock_l1_wsl.py` | New install/restore script |
| `tests/test_gaussian_wrapper_integration.py` | New 6-test integration suite |

**Recovery recipe for a future license:**

```bash
python scripts/install_mock_l1_wsl.py --restore    # bring back real l1.exe
```

The backup is at `/opt/g16/l1.exe.real` (31 MB), untouched by this
phase. Once a Gaussian license is configured, restoring the real
binary re-enables genuine calculations with no other code change.

---

## Final Test Totals

```
================================= 1043 passed, 16 skipped =================================
```

---

## What's Next (Phase 9B — wizard preview polish)

1. **Wizard cross-program favourites strip** — show recently-used / fav
   presets next to the per-program dropdown.
2. **Wizard CSV batch import** — point at a directory of `.xyz` files.
3. **Wizard step-advance validation** — block Next button if calc
   fields are wrong (charge range, memory, no XYZ).
4. **ConFlow SP-or-xyz workaround** — investigate ORCA SP emitting
   geometry.
5. **Result detail pane** — render parsed SCF energy, termination,
   geometry (using the mock to drive initial UI).