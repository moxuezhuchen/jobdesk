# Workflow Builder + Submit Dual-Entry Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the monolithic `SubmitPage` with two cooperating surfaces — a sidebar `WorkflowPage` (build / save / load named workflow presets) and a modal `SubmitDialog` (auto-selected Single vs. Workflow mode based on the selected input files). Add a `MethodPresetStore` so built-in and user presets share one disk-backed, YAML-flavoured storage layer. Files-page `[🚀 Submit]` button becomes the modal trigger. Keep `SubmitPayload`, `WorkflowSpec`, and `SubmitUseCase` untouched.

**Architecture:**
- `services/method_presets.py` — `MethodPresetStore` loads confflow YAML files from `resources/method_presets/` (built-in) and `%APPDATA%/JobDesk/method_presets/` (user). Same `WorkflowSpec` shape drives both.
- `gui/pages/workflow_page.py` — sidebar `WorkflowPage` hosting a preset selector + read-only step list + `[Save as user preset]` + `[Use this preset for submit]`.
- `gui/dialogs/workflow_builder_dialog.py` — modal that hosts the existing `WorkflowGraphEditor` and returns a serialised `WorkflowSpec` on accept.
- `gui/dialogs/submit_dialog.py` — modal with auto-detected Mode (Single for `.gjf`/`.inp` only, Workflow forced when any `.xyz` present). Builds either `kind="single"` or `kind="confflow"|"dag"` `SubmitPayload` directly.
- `gui/pages/file_transfer_page.py` — adds the `[🚀 Submit]` primary button gated on selection.
- `gui/main_window.py` — `_NAV_ITEMS[1]` renamed `Files/Workflow/Runs/Settings`; new `MethodPresetStore` instance; wires `WorkflowPage.preset_chosen_for_submit` → switch to Files page → `open_submit_dialog(selected_preset=...)`.

**Tech Stack:** Python 3.13, PySide6, Pydantic v2, PyYAML, pytest, Ruff, mypy.

---

## Handoff notes for the executing sub-agent

You are reading this plan in a fresh agent that has **none** of the originating main-session context. Read this entire file top to bottom before you start typing code. Key facts the main session established but you cannot infer alone:

1. **Where presets live on disk**:
   - Built-in: `src/jobdesk_app/resources/method_presets/` — packaged via `importlib.resources.files("jobdesk_app.resources.method_presets")`. Ship 9 YAML files (see Task 2).
   - User (mutable): `<app_data_dir>/method_presets/` where `app_data_dir = get_app_data_dir()` from `src/jobdesk_app/app_paths.py` (returns `%APPDATA%/JobDesk/` on Windows). Atomic write via `src/jobdesk_app/core/atomic_write.py::atomic_write_text`.
2. **The preset file schema is exactly `WorkflowSpec.from_yaml()` confflow YAML** — top-level `{global: {...}, steps: [...]}`. No new schema, no .json round-trip, no separate metadata file. The store round-trips via `WorkflowSpec.to_yaml()` and `from_yaml()`.
3. **Editor reuse**: `src/jobdesk_app/gui/nodegraph/editor.py::WorkflowGraphEditor` is a plain `QWidget` with `set_graph(NodeGraph)`, `graph()`, `apply_language(language)`. Embed it directly inside `WorkflowBuilderDialog`. The editor already emits `tour_requested` — keep that signal alive (the Runs-page empty state still uses it).
4. **Existing helpers to reuse, do NOT reimplement**:
   - `app_paths.get_app_data_dir()` — `src/jobdesk_app/app_paths.py:5`
   - `core.atomic_write.atomic_write_text` — `src/jobdesk_app/core/atomic_write.py`
   - `core.workflow_spec.assemble_orca_keyword` — `src/jobdesk_app/core/workflow_spec.py:59`
   - `nodegraph.serialization.from_json` / `to_json` — `src/jobdesk_app/gui/nodegraph/serialization.py:61-72`
   - `pages.file_transfer_page.FileTransferPage._selected_paths_for_side` and `_build_input_sources` — already return `list[InputSource]`.
5. **SubmitPayload contract is frozen** — `src/jobdesk_app/core/submit_payload.py` defines `SubmitKind = Literal["single", "confflow", "dag"]`. The new `SubmitDialog` emits a `SubmitPayload` whose `kind` field is either `"single"` (for mode=Single) or what `nodegraph.spec_bridge.to_workflow_spec()` produced (for mode=Workflow). `MainWindow._on_submit_requested` is the existing handler in `src/jobdesk_app/gui/main_window.py:238` — DO NOT modify it; just route the signal.
6. **The `_NAV_ITEMS` indices are load-bearing** — `MainWindow._switch_page(0)` is now Files (was 0 = Files, 1 = Submit). `runs_results_page.go_to_submit_requested` currently calls `_switch_page(1)`. After this refactor, that signal must navigate to Files page (index 0) and then call `open_submit_dialog()`. Update both the runs page and the switcher accordingly.
7. **Tests to delete**: `tests/test_submit_page.py` is fully replaced. Old assertions pinning `SubmitBtn`, `PrimaryBtn`, `SubmitServerHint`, `SubmitRemoteTargetLabel`, `submit_requested` semantics no longer exist.
8. **Hard rules from the workspace**:
   - `Shell` tool calls MUST include `required_permissions: ["all"]` (PowerShell sandbox rule from `shell-permissions.mdc`).
   - Do NOT break the WSL Gaussian 16 install at `/opt/g16/g16` and `/opt/g16/l1.exe`. None of this refactor touches WSL or g16, but if a smoke test ever runs, follow `wsl-g16-safety.mdc` pre-flight.
9. **`pytest` invocation**: every task uses `--basetemp=.pytest_<task-name>` to avoid sharing scratch files. The exact commands are written into each Step. Follow them literally.
10. **Commit cadence**: do NOT commit unless explicitly requested. This plan asks you to leave the working tree dirty at the end so the verifier (the original main session) can inspect the diff.

If any of the above is unclear, **stop and ask the user** — do not guess. Otherwise, proceed task by task.

---

## File map

Files this plan creates (new):
- `src/jobdesk_app/services/method_presets.py`
- `src/jobdesk_app/resources/method_presets/__init__.py`
- `src/jobdesk_app/resources/method_presets/gaussian/b3lyp_631gd_opt_freq.yaml`
- `src/jobdesk_app/resources/method_presets/gaussian/b3lyp_d3_def2tzvp_opt.yaml`
- `src/jobdesk_app/resources/method_presets/gaussian/m062x_def2tzvp_opt_freq.yaml`
- `src/jobdesk_app/resources/method_presets/gaussian/wb97xd_def2tzvp_sp.yaml`
- `src/jobdesk_app/resources/method_presets/gaussian/ccsd_t_ccpvtz_sp.yaml`
- `src/jobdesk_app/resources/method_presets/orca/b3lyp_def2tzvp_opt_freq.yaml`
- `src/jobdesk_app/resources/method_presets/orca/dlpno_ccsd_t_sp.yaml`
- `src/jobdesk_app/resources/method_presets/orca/r2scan3c_opt_freq.yaml`
- `src/jobdesk_app/resources/method_presets/conflow/conformer_ensemble_sp.yaml`
- `src/jobdesk_app/gui/pages/workflow_page.py`
- `src/jobdesk_app/gui/dialogs/submit_dialog.py`
- `src/jobdesk_app/gui/dialogs/workflow_builder_dialog.py`
- `tests/test_method_presets.py`
- `tests/test_workflow_page.py`
- `tests/test_submit_dialog.py`

Files this plan modifies:
- `src/jobdesk_app/gui/i18n.py` — wholesale rewrite (see Task 8)
- `src/jobdesk_app/gui/main_window.py` — nav label, page registration, signal routing
- `src/jobdesk_app/gui/pages/file_transfer_page.py` — add `[🚀 Submit]` button
- `src/jobdesk_app/gui/pages/runs_results_page.py` — `go_to_submit_requested` → files page + open dialog
- `tests/test_main_window.py` — `_NAV_ITEMS` label assertion, page registration
- `tests/test_workflow_tour_dialog.py` — `tour_requested` signal source location
- `tests/conftest.py` (if exists) — QApplication fixture already exists; reuse
- `tests/test_nodegraph/test_gui_review_fixes.py` — drop the `SubmitPage` F5 regression; replace with `WorkflowPage` analogue
- `docs/USER_GUIDE.md` — update the "Submit" walkthrough to describe Files→Submit / Workflow→Submit dual entry
- `docs/architecture.md` — add a section on `MethodPresetStore`

Files this plan deletes:
- `src/jobdesk_app/gui/pages/submit_page.py`
- `tests/test_submit_page.py`

Files this plan does **not** touch:
- `src/jobdesk_app/core/submit_payload.py`
- `src/jobdesk_app/core/workflow_spec.py`
- `src/jobdesk_app/services/submit_use_case.py`
- `src/jobdesk_app/gui/dialogs/workflow_tour_dialog.py`
- `src/jobdesk_app/resources/workflow_examples/*.json`

---

### Task 1: `MethodPresetStore` skeleton + list/load

**Files:**
- New: `src/jobdesk_app/services/method_presets.py`
- Test: `tests/test_method_presets.py`

- [ ] **Step 1: Write failing tests for `list_presets` and `load`**

```python
# tests/test_method_presets.py
from __future__ import annotations
import pytest
from jobdesk_app.services.method_presets import MethodPresetStore, MethodPreset


@pytest.fixture
def store(tmp_path, monkeypatch):
    # Re-route the user-preset directory to tmp_path.
    monkeypatch.setattr(
        "jobdesk_app.services.method_presets.get_app_data_dir",
        lambda: tmp_path,
    )
    return MethodPresetStore()


def test_list_presets_includes_builtins(store):
    names = {p.name for p in store.list_presets()}
    assert "b3lyp_631gd_opt_freq" in names  # one of the bundled gaussians
    assert "r2scan3c_opt_freq" in names     # one of the bundled orcas


def test_builtin_presets_carry_source_builtin(store):
    presets = store.list_presets()
    for p in presets:
        if p.name in {"b3lyp_631gd_opt_freq", "r2scan3c_opt_freq"}:
            assert p.source == "builtin"


def test_load_returns_workflow_spec(store):
    spec = store.load("b3lyp_631gd_opt_freq", source="builtin")
    assert spec is not None
    assert hasattr(spec, "global_config")


def test_load_unknown_raises(store):
    with pytest.raises(KeyError):
        store.load("does_not_exist", source="builtin")
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_method_presets.py -q --basetemp=.pytest_task1_red`

Expected: FAIL because `jobdesk_app.services.method_presets` does not exist (`ModuleNotFoundError` or `ImportError`).

- [ ] **Step 3: Implement `MethodPresetStore` skeleton**

```python
# src/jobdesk_app/services/method_presets.py
"""Disk-backed method preset library for the workflow builder.

Built-in presets ship under ``jobdesk_app.resources.method_presets`` as
confflow YAML files; user-saved presets land in
``<app_data_dir>/method_presets/<name>.yaml``. Both shapes round-trip
through :class:`jobdesk_app.core.workflow_spec.WorkflowSpec` so the
editor, the wizard, and the run service see the same data model.
"""
from __future__ import annotations

import importlib.resources
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..app_paths import get_app_data_dir
from ..core.workflow_spec import WorkflowSpec

PresetSource = Literal["builtin", "user"]


@dataclass(frozen=True)
class MethodPreset:
    name: str
    source: PresetSource
    path: Path
    spec: WorkflowSpec


def _read_spec_from_path(path: Path) -> WorkflowSpec:
    text = path.read_text(encoding="utf-8")
    return WorkflowSpec.from_yaml(text)


class MethodPresetStore:
    """Resolve confflow YAML presets from built-in and user directories.

    Lookup precedence (when name collides):

    1. User directory wins (``~/.config/jobdesk/method_presets``).
    2. Built-in directory (``jobdesk_app.resources.method_presets``).

    Both directories hold confflow YAML files; the file stem is the
    preset name (``b3lyp_631gd_opt_freq.yaml``).
    """

    def __init__(self) -> None:
        self._builtin_pkg = "jobdesk_app.resources.method_presets"

    @property
    def user_dir(self) -> Path:
        d = get_app_data_dir() / "method_presets"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _builtin_root(self) -> Path:
        # ``importlib.resources.files`` returns a ``Traversable``; turn
        # the relevant ones into ``Path`` so ``iterdir`` is uniform.
        traversable = importlib.resources.files(self._builtin_pkg)
        return Path(str(traversable))

    def _iter_builtin(self) -> list[Path]:
        root = self._builtin_root()
        if not root.exists():
            return []
        # Walk subdirectories (gaussian/, orca/, conflow/).
        return sorted(p for p in root.rglob("*.yaml") if p.is_file())

    def _iter_user(self) -> list[Path]:
        if not self.user_dir.exists():
            return []
        return sorted(p for p in self.user_dir.rglob("*.yaml") if p.is_file())

    def list_presets(self) -> list[MethodPreset]:
        seen: dict[str, MethodPreset] = {}
        # User first so user overrides built-in on collision.
        for path in self._iter_user():
            seen[path.stem] = MethodPreset(
                name=path.stem, source="user", path=path, spec=_read_spec_from_path(path)
            )
        for path in self._iter_builtin():
            if path.stem in seen:
                continue
            seen[path.stem] = MethodPreset(
                name=path.stem, source="builtin", path=path, spec=_read_spec_from_path(path)
            )
        return list(seen.values())

    def load(self, name: str, *, source: PresetSource | None = None) -> WorkflowSpec:
        if source == "user" or (source is None and (self.user_dir / f"{name}.yaml").exists()):
            user_path = self.user_dir / f"{name}.yaml"
            if user_path.exists():
                return _read_spec_from_path(user_path)
        if source == "builtin" or source is None:
            for path in self._iter_builtin():
                if path.stem == name:
                    return _read_spec_from_path(path)
        raise KeyError(f"Method preset {name!r} not found")
```

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_method_presets.py -q --basetemp=.pytest_task1_green`

Expected: RED — the bundled YAML files from Task 2 are not on disk yet, so `list_presets()` returns `[]` and `test_load_returns_workflow_spec` will KeyError before any built-in is found. That is fine; this task's deliverable is the **skeleton** verified by `test_list_presets_includes_builtins` being null-and-void until Task 2 lands. Mark Task 1 complete on the basis that `MethodPresetStore` imports and `test_load_unknown_raises` passes.

Run only the structurally independent assertion:

`pytest tests/test_method_presets.py::test_load_unknown_raises tests/test_method_presets.py::test_builtin_presets_carry_source_builtin -q --basetemp=.pytest_task1_partial`

The first must PASS (KeyError raised for a name that does not exist). The second is expected to fail until Task 2; note this in the handoff log.

---

### Task 2: Ship the bundled presets

**Files:**
- New: `src/jobdesk_app/resources/method_presets/__init__.py`
- New: `src/jobdesk_app/resources/method_presets/gaussian/b3lyp_631gd_opt_freq.yaml`
- New: `src/jobdesk_app/resources/method_presets/gaussian/b3lyp_d3_def2tzvp_opt.yaml`
- New: `src/jobdesk_app/resources/method_presets/gaussian/m062x_def2tzvp_opt_freq.yaml`
- New: `src/jobdesk_app/resources/method_presets/gaussian/wb97xd_def2tzvp_sp.yaml`
- New: `src/jobdesk_app/resources/method_presets/gaussian/ccsd_t_ccpvtz_sp.yaml`
- New: `src/jobdesk_app/resources/method_presets/orca/b3lyp_def2tzvp_opt_freq.yaml`
- New: `src/jobdesk_app/resources/method_presets/orca/dlpno_ccsd_t_sp.yaml`
- New: `src/jobdesk_app/resources/method_presets/orca/r2scan3c_opt_freq.yaml`
- New: `src/jobdesk_app/resources/method_presets/conflow/conformer_ensemble_sp.yaml`
- Modify: `pyproject.toml` (add `package data` for the new resource package if not already discovered)

- [ ] **Step 1: Confirm `importlib.resources` discoverability**

Run: `python -c "import importlib.resources as r; print(r.files('jobdesk_app.resources'))"` from the project root.

Expected: a `PosixPath` or `WindowsPath` printed (i.e. the existing `resources/` package is reachable). If empty or `None`, add `tool.setuptools.package-data = {"jobdesk_app" = ["resources/**/*"]}` (or the equivalent `[tool.hatch.build.targets.wheel]` data section if Hatch is used) to `pyproject.toml` and re-run.

- [ ] **Step 2: Create the resource package skeleton**

```python
# src/jobdesk_app/resources/method_presets/__init__.py
"""Bundled confflow method presets.

Each ``*.yaml`` file is loaded as a :class:`WorkflowSpec` by
:mod:`jobdesk_app.services.method_presets`. Subdirectories group presets
by program (``gaussian/``, ``orca/``, ``conflow/``) — they are advisory;
the file stem is the preset name. Do not put code in this directory
beyond the empty namespace package.
"""
```

Verify with: `python -c "from jobdesk_app.services.method_presets import MethodPresetStore; print(len(MethodPresetStore().list_presets()))"` — should print `0` because no YAML files exist yet.

- [ ] **Step 3: Ship Gaussian presets**

Each file is a confflow YAML document of shape `{global: {calc: {...}, ...}, steps: [{name, type, params: {...}}]}` and must round-trip through `WorkflowSpec.from_yaml()` / `to_yaml()`. Use `WorkflowSpec.from_form(...)` in a REPL to generate the canonical text.

`src/jobdesk_app/resources/method_presets/gaussian/b3lyp_631gd_opt_freq.yaml`:

```yaml
global:
  gaussian_path: "/opt/g16/g16"
  charge: 0
  multiplicity: 1
  cores_per_task: 8
  total_memory: "16GB"
  max_parallel_jobs: 1
  freeze: []
  rmsd_threshold: 0.25
steps:
  - name: "preset_step_01"
    type: "calc"
    params:
      iprog: gaussian
      itask: opt_freq
      keyword: "opt freq b3lyp/6-31g(d)"
```

`b3lyp_d3_def2tzvp_opt.yaml` — `itask: opt`, keyword `opt b3lyp/def2-tzvp empiricaldispersion=gd3bj`, cores 8, 16 GB.
`m062x_def2tzvp_opt_freq.yaml` — `itask: opt_freq`, keyword `opt freq m06-2x/def2-tzvp`, cores 8, 16 GB.
`wb97xd_def2tzvp_sp.yaml` — `itask: sp`, keyword `sp wb97x-d/def2-tzvp`, cores 8, 16 GB.
`ccsd_t_ccpvtz_sp.yaml` — `itask: sp`, keyword `sp ccsd(t)/cc-pvtz`, cores 16, 32 GB.

Verify each parses:

```bash
python -c "
from jobdesk_app.services.method_presets import MethodPresetStore
s = MethodPresetStore()
for p in s.list_presets():
    print(p.name, p.source)
"
```

Expected output (one line per preset, `source=builtin`):

```
b3lyp_631gd_opt_freq builtin
b3lyp_d3_def2tzvp_opt builtin
m062x_def2tzvp_opt_freq builtin
wb97xd_def2tzvp_sp builtin
ccsd_t_ccpvtz_sp builtin
```

- [ ] **Step 4: Ship ORCA presets**

`src/jobdesk_app/resources/method_presets/orca/b3lyp_def2tzvp_opt_freq.yaml`:

```yaml
global:
  orca_path: "/opt/orca601/orca"
  charge: 0
  multiplicity: 1
  cores_per_task: 8
  total_memory: "16GB"
  max_parallel_jobs: 1
  freeze: []
  rmsd_threshold: 0.25
steps:
  - name: "preset_step_01"
    type: "calc"
    params:
      iprog: orca
      itask: opt_freq
      keyword: "B3LYP D3BJ def2-TZVP def2/J RIJCOSX TightSCF opt freq"
```

`dlpno_ccsd_t_sp.yaml` — `itask: sp`, keyword `DLPNO-CCSD(T) cc-pVTZ cc-pVTZ/C TightSCF`, cores 16, 32 GB.
`r2scan3c_opt_freq.yaml` — `itask: opt_freq`, keyword `r2SCAN-3c opt freq`, cores 8, 16 GB.

- [ ] **Step 5: Ship one ConfFlow multi-step preset**

`src/jobdesk_app/resources/method_presets/conflow/conformer_ensemble_sp.yaml`:

```yaml
global:
  gaussian_path: "/opt/g16/g16"
  orca_path: "/opt/orca601/orca"
  charge: 0
  multiplicity: 1
  cores_per_task: 8
  total_memory: "32GB"
  max_parallel_jobs: 4
  freeze: []
  rmsd_threshold: 0.25
  energy_window: 5.0
steps:
  - name: "confgen"
    type: "confgen"
    params:
      chains: ["1-2-3-4-5"]
      angle_step: 120
      bond_multiplier: 1.15
  - name: "preopt"
    type: "calc"
    params:
      iprog: orca
      itask: opt
      keyword: "xTB2 Opt"
      cores_per_task: 4
  - name: "refine"
    type: "calc"
    params:
      iprog: orca
      itask: sp
      keyword: "r2SCAN-3c"
```

- [ ] **Step 6: Verify all 9 presets load**

Run: `python -c "from jobdesk_app.services.method_presets import MethodPresetStore; print(sorted((p.name, p.source) for p in MethodPresetStore().list_presets()))"`

Expected output (alpha-sorted tuples):

```python
[
    ('b3lyp_631gd_opt_freq', 'builtin'),
    ('b3lyp_d3_def2tzvp_opt', 'builtin'),
    ('b3lyp_def2tzvp_opt_freq', 'builtin'),
    ('ccsd_t_ccpvtz_sp', 'builtin'),
    ('conformer_ensemble_sp', 'builtin'),
    ('dlpno_ccsd_t_sp', 'builtin'),
    ('m062x_def2tzvp_opt_freq', 'builtin'),
    ('r2scan3c_opt_freq', 'builtin'),
    ('wb97xd_def2tzvp_sp', 'builtin'),
]
```

Run `pytest tests/test_method_presets.py -q --basetemp=.pytest_task2_full` — expect all four tests to PASS.

---

### Task 3: `MethodPresetStore` writes (save / rename / delete)

**Files:**
- Modify: `src/jobdesk_app/services/method_presets.py`
- Test: `tests/test_method_presets.py`

- [ ] **Step 1: Add RED tests for `save_user` and `delete_user`**

Append to `tests/test_method_presets.py`:

```python
from jobdesk_app.core.workflow_spec import WorkflowSpec


def test_save_user_writes_yaml_to_user_dir(store, tmp_path):
    # Build a spec via from_form so we have a guaranteed-valid specimen.
    spec = WorkflowSpec.from_form(
        work_dir_name="user_demo",
        program="orca",
        method="B3LYP",
        basis="def2-SVP",
        charge=0,
        multiplicity=1,
        nproc=8,
        memory_mb=8192,
        steps=("confgen", "opt"),
    )
    path = store.save_user("user_demo", spec)
    assert path.exists()
    assert path.parent == store.user_dir
    assert path.suffix == ".yaml"
    # Re-parse the written file
    reloaded = WorkflowSpec.from_yaml(path.read_text(encoding="utf-8"))
    assert reloaded.to_form()["work_dir_name"] == "user_demo"


def test_save_user_then_list_includes_it_as_user(store):
    spec = WorkflowSpec.from_form(
        work_dir_name="x", program="gaussian", method="B3LYP",
        basis="6-31G(d)", charge=0, multiplicity=1,
        nproc=4, memory_mb=4096,
    )
    store.save_user("user_x", spec)
    user_names = {p.name for p in store.list_presets() if p.source == "user"}
    assert "user_x" in user_names


def test_user_preset_with_same_name_overrides_builtin(store):
    spec = WorkflowSpec.from_form(
        work_dir_name="override", program="orca", method="r2SCAN-3c",
        basis="", charge=0, multiplicity=1, nproc=8, memory_mb=4096,
    )
    store.save_user("b3lyp_631gd_opt_freq", spec)  # collide with builtin
    match = next(p for p in store.list_presets() if p.name == "b3lyp_631gd_opt_freq")
    assert match.source == "user"


def test_delete_user_removes_file(store):
    spec = WorkflowSpec.from_form(
        work_dir_name="x", program="gaussian", method="B3LYP",
        basis="6-31G(d)", charge=0, multiplicity=1,
        nproc=4, memory_mb=4096,
    )
    store.save_user("temp", spec)
    store.delete_user("temp")
    assert not (store.user_dir / "temp.yaml").exists()
    # Should fall back to built-in for the same name
    user_names = [p for p in store.list_presets() if p.source == "user" and p.name == "temp"]
    assert user_names == []


def test_rename_user_creates_new_removes_old(store):
    spec = WorkflowSpec.from_form(
        work_dir_name="x", program="gaussian", method="B3LYP",
        basis="6-31G(d)", charge=0, multiplicity=1, nproc=4, memory_mb=4096,
    )
    store.save_user("old_name", spec)
    new_path = store.rename_user("old_name", "new_name")
    assert new_path.exists()
    assert not (store.user_dir / "old_name.yaml").exists()
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_method_presets.py -q --basetemp=.pytest_task3_red`

Expected: ALL FIVE new tests FAIL with `AttributeError: 'MethodPresetStore' object has no attribute 'save_user'` / `delete_user` / `rename_user`.

- [ ] **Step 3: Implement write API**

Append to `src/jobdesk_app/services/method_presets.py`:

```python
from ..core.atomic_write import atomic_write_text


def _safe_preset_filename(name: str) -> str:
    # Strip path separators to keep this strictly inside user_dir.
    cleaned = name.strip().replace("/", "_").replace("\\", "_")
    if not cleaned:
        raise ValueError("preset name must be non-empty")
    return f"{cleaned}.yaml"


class MethodPresetStore:
    # ... existing code ...

    def save_user(self, name: str, spec: WorkflowSpec) -> Path:
        """Persist ``spec`` to ``<user_dir>/<name>.yaml`` atomically."""
        target = self.user_dir / _safe_preset_filename(name)
        atomic_write_text(target, spec.to_yaml())
        return target

    def delete_user(self, name: str) -> None:
        target = self.user_dir / _safe_preset_filename(name)
        if target.exists():
            target.unlink()

    def rename_user(self, old_name: str, new_name: str) -> Path:
        src = self.user_dir / _safe_preset_filename(old_name)
        dst = self.user_dir / _safe_preset_filename(new_name)
        atomic_write_text(dst, src.read_text(encoding="utf-8"))
        src.unlink()
        return dst
```

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_method_presets.py -q --basetemp=.pytest_task3_green`

Expected: ALL NINE tests PASS.

---

### Task 4: Replace `gui/i18n.py` with the new key set

**Files:**
- Modify: `src/jobdesk_app/gui/i18n.py`

This is a wholesale rewrite. The new `i18n.py` follows the existing `tr(key, language)` API exactly; only the dictionary contents and the `en` fallback semantics change.

- [ ] **Step 1: Snapshot current usage**

Run: `grep -rn 'tr("' src/jobdesk_app | head -200`

Save the output to your local scratch file. The new `i18n.py` MUST define every `tr()` key currently called from `src/jobdesk_app`. Skim the snapshot and confirm each requested key is either in the new `ZH` dict (Chinese) or acceptable as English fallback.

- [ ] **Step 2: Write the new `i18n.py`**

```python
# src/jobdesk_app/gui/i18n.py
"""Runtime translation table for the GUI.

The contract is unchanged:

* :func:`tr(key, language)` returns the localised text for ``key`` at
  runtime. ``language`` is the two-letter code stored in
  ``GuiSettings.language`` (``"en"`` or ``"zh"``).
* For unknown keys the English text is the ``key`` itself so adding a
  new string is a one-line edit here.

This file is the single source of truth for all GUI copy. Adding a key
in this file is opt-in; until you do, the English key is the fallback.
"""
from __future__ import annotations

ZH: dict[str, str] = {
    # ── Nav ──
    "Files": "文件",
    "Workflow": "工作流",
    "Runs": "运行",
    "Settings": "设置",

    # ── Common ──
    "Cancel": "取消",
    "Save": "保存",
    "Open": "打开",
    "Close": "关闭",
    "Delete": "删除",
    "Edit": "编辑",
    "Add": "添加",
    "Remove": "移除",
    "Refresh": "刷新",
    "Import": "导入",
    "Export": "导出",
    "Submit": "提交",
    "Server": "服务器",
    "Server:": "服务器:",
    "Server ID:": "服务器 ID:",
    "No server": "未连服务器",
    "Connect to a server first.": "请先连接服务器。",
    "Local Folder": "本地目录",
    "Default remote directory:": "默认远程目录:",
    "Connect": "连接",
    "Disconnect": "断开",
    "Home": "主页",
    "Ready": "就绪",
    "Browse": "浏览",
    "English": "English",
    "Chinese": "中文",
    "Language:": "语言:",

    # ── WorkflowPage ──
    "Workflow presets": "工作流预设",
    "Built-in": "内置",
    "User": "用户",
    "New workflow": "新建工作流",
    "Save as user preset": "保存为用户预设",
    "Preset name:": "预设名称:",
    "Preset name cannot be empty.": "预设名称不能为空。",
    "Add Confgen": "添加构象生成",
    "Add Calculation": "添加计算步骤",
    "Steps": "步骤",
    "Use this preset for submit": "用此预设提交",
    "Open in builder": "在编辑器中打开",
    "Delete preset": "删除预设",
    "Rename preset": "重命名预设",
    "Edit workflow": "编辑工作流",
    "Workflow builder": "工作流编辑器",
    "Workflow builder closed without saving.": "工作流编辑器已取消。",
    "Workflow saved.": "工作流已保存。",
    "Workflow failed to save: {message}": "工作流保存失败：{message}",
    "Preset \"{name}\" already exists. Overwrite?": "预设 \"{name}\" 已存在。是否覆盖？",
    "Workflow preset loaded: {name}": "已加载工作流预设：{name}",
    "Loaded workflow preset.": "已加载工作流预设。",

    # ── SubmitDialog ──
    "Submit for calculation": "提交计算任务",
    "Selected files ({n})": "已选文件 ({n})",
    "Selected files (1)": "已选文件 (1)",
    "Selected files (0)": "未选文件",
    "Mode:": "模式:",
    "Single calculation": "单步计算",
    "Workflow": "工作流",
    "Charge:": "电荷:",
    "Multiplicity:": "多重度:",
    "Max parallel:": "最大并行:",
    "Max parallel": "最大并行",
    "Workflow required for .xyz inputs": ".xyz 输入必须使用工作流模式",
    "Workflow required for non-Gaussian/ORCA inputs": "非 .gjf/.inp 输入必须使用工作流模式",
    "Submit ▶": "提交 ▶",
    "Connect to a server first.": "请先连接服务器。",
    "Submitted successfully.": "提交成功。",
    "Submission failed: {message}": "提交失败：{message}",
    "Server pill: {label}": "服务器: {label}",

    # ── Empty state, banner, hints ──
    "No server selected": "未选择服务器",
    "Add a node from the library to start your workflow.": "请从节点库添加节点以构建工作流。",
    "Graph incomplete: {message}": "图不完整：{message}",
    "Preview failed: {message}": "预览失败：{message}",
    "Render failed: {message}": "渲染失败：{message}",

    # ── Files page additions ──
    "Submit (selected files)": "提交 (选中文件)",
    "Use as input → Submit": "作为输入 → 提交",
    "Pushed {n} source(s) from Files page.": "已从文件页推送 {n} 个输入。",
    "Drag .xyz / .gjf / .inp files or a directory here": "拖拽 .xyz / .gjf / .inp 文件或目录到此",
    "Local": "本地",
    "Remote": "远程",
    "Select input files": "选择输入文件",
    "Select directory": "选择目录",

    # ── Runs page status strings ──
    "Submitted: {batch_id}": "已提交：{batch_id}",
    "Submitted.": "已提交。",
    "Submit failed: {message}": "提交失败：{message}",
    "Submitted": "已提交",
    "Submitting": "正在提交",
    "No inputs selected.": "未选择任何输入。",
    "Add at least one input file.": "请至少添加一个输入文件。",
    "XYZ path is required.": "XYZ 路径不能为空。",

    # ── Tour dialog (preserved verbatim) ──
    "Workflow tour": "工作流导览",
    "Set up a server": "配置服务器",
    "Connect & browse": "连接与浏览",
    "Pick your inputs": "选择输入",
    "Build a workflow": "构建工作流",
    "Submit & monitor": "提交与监控",
    "Read results": "查看结果",

    # ── Step types (preserved verbatim from nodegraph) ──
    "Calcs": "计算",
    "Sentinels": "终止",
    "Linear OPT + FREQ": "线性 OPT + FREQ",
    "Conformer ensemble + SP": "构象体集 + SP",
    "Fan-out: two OPT branches": "扇出：两个 OPT 分支",
    "Fan-in: REFINE": "扇入：REFINE",
    "3-step backbone: optimize, then frequency analysis.": (
        "三步主线：几何优化后接频率分析。"
    ),
    "Generate conformers, optimize the lowest, then single-point.": (
        "生成构象体集，优化能量最低者，再计算单点能。"
    ),
    "Same conformer ensemble feeds two parallel optimizations.": (
        "同一构象体集同时饰入两个并行优化。"
    ),
    "Optimize a candidate, refine with the conformer ensemble.": (
        "优化候选结构，再以构象体集进行高精度精炼。"
    ),
    "Examples": "示例模板",
    "Workflow templates (*.json)": "工作流模板 (*.json)",
    "Workflow OK": "工作流正常",
    "Validation [{code}]: {message}": "校验 [{code}]: {message}",

    # ── Misc ──
    "Confirm submission state for {n} uncertain task(s)?": (
        "确认 {n} 个状态不确定任务已提交？"
    ),
    "Abandon {n} uncertain task(s) only after confirming the remote job does not exist; then retry?": (
        "仅在确认远程作业不存在后，才可放弃 {n} 个状态不确定任务并重试？"
    ),
    "Analysis:": "分析:",
    "Auto connect disabled": "自动连接已关闭",
    "Auto connect selected server": "自动连接所选服务器",
    "Batch size:": "批大小:",
    "Batch:": "批:",
    "Clear Run Profiles": "清空运行记忆",
    "Command:": "命令:",
    "Connected: {server_id}": "已连接: {server_id}",
    "Connecting: {server_id}": "正在连接: {server_id}",
    "Connection:": "连接:",
    "Create runs only": "仅创建运行",
    "Current directory": "当前目录",
    "Default command:": "默认命令:",
    "Default local folder:": "默认本地目录:",
    "Default remote directory:": "默认远程目录:",
    "Default server:": "默认服务器:",
    "Defaults": "默认设置",
    "Delete": "删除",
    "Download": "下载",
    "Download files:": "下载文件:",
    "Files:": "文件:",
    "Local {local_count} | Remote {remote_count}": "本地 {local_count} | 远程 {remote_count}",
    "Max parallel": "最大并行",
    "Max parallel:": "最大并行:",
    "Never overwrite": "从不覆盖",
    "New Folder": "新建目录",
    "No server selected": "未选择服务器",
    "Overwrite": "覆盖",
    "Paths": "路径",
    "Preview": "预览",
    "Preview Analysis": "预览分析",
    "Preview Commands": "预览命令",
    "Queue {transferred} ok | {skipped} skip | {failed} fail": (
        "队列 {transferred} 成功 | {skipped} 跳过 | {failed} 失败"
    ),
    "Ready": "就绪",
    "Refresh List": "刷新列表",
    "Refresh": "刷新",
    "Refresh Local": "刷新本地",
    "Refresh Remote": "刷新远程",
    "Server": "服务器",
    "Settings": "设置",
    "Status:": "状态:",
    "Unknown server": "未知服务器",
}

EN: dict[str, str] = {}  # all keys fall back to themselves


def tr(key: str, language: str = "en", /, **kwargs: object) -> str:
    """Return the localised text for ``key`` at ``language``.

    Falls back to English (``key`` itself) when the language is unknown
    or the key is missing; substitutes ``**kwargs`` via ``str.format``
    AFTER the lookup so the user's empty-language code path doesn't
    crash on missing keyword arguments.
    """
    table = ZH if language == "zh" else EN
    template = table.get(key)
    if template is None or language == "en":
        template = key
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template
    return template


__all__ = ["tr", "ZH", "EN"]
```

- [ ] **Step 3: Run the existing test suite to catch regressions**

Run: `pytest -q --basetemp=.pytest_i18n_check tests/test_main_window.py tests/test_nodegraph tests/test_empty_state_hint.py tests/test_inline_banner.py tests/test_workflow_tour_dialog.py`

Expected: any failures point at keys that the new `i18n.py` is missing. Add them to `ZH` and re-run. Cap iteration at 5 cycles; if a key is downstream of a deleted page, leave it for Task 10 cleanup.

- [ ] **Step 4: Smoke-grep `tr("...")` callsites for missing keys**

Run: `python -c "
import re, pathlib, json
import sys
sys.path.insert(0, 'src')
from jobdesk_app.gui.i18n import ZH
missing = []
for p in pathlib.Path('src/jobdesk_app').rglob('*.py'):
    for m in re.finditer(r'tr\(\"([^\"]+)\"', p.read_text(encoding='utf-8')):
        if m.group(1) not in ZH:
            missing.append((str(p), m.group(1)))
for entry in sorted(set(missing)):
    print(entry[0], entry[1])
" | sort -u`

Expected: output is empty for any key called as `tr("key literal", ...)`. If non-empty, add to `ZH` with a sensible translation and re-run Step 3.

---

### Task 5: `gui/dialogs/submit_dialog.py`

**Files:**
- New: `src/jobdesk_app/gui/dialogs/submit_dialog.py`
- Test: `tests/test_submit_dialog.py`

- [ ] **Step 1: Write RED tests**

```python
# tests/test_submit_dialog.py
from pathlib import Path
import pytest
from jobdesk_app.core.submit_payload import InputSource, SubmitPayload
from jobdesk_app.gui.dialogs.submit_dialog import SubmitDialog
from jobdesk_app.services.method_presets import MethodPresetStore


@pytest.fixture
def qapp(qtbot):
    return qtbot  # pyqtgraph-style alias; just keep fixture alive


def _src(name: str, kind: str) -> InputSource:
    p = Path("/tmp") / name
    return InputSource(path=p, side="local", kind=kind)  # type: ignore[arg-type]


def test_mode_defaults_to_single_for_gjf_only(qapp):
    sources = [_src("a.gjf", "gjf"), _src("b.gjf", "gjf")]
    dlg = SubmitDialog("en", files=sources)
    assert dlg.mode() == "single"


def test_mode_defaults_to_single_for_inp_only(qapp):
    sources = [_src("a.inp", "inp")]
    dlg = SubmitDialog("en", files=sources)
    assert dlg.mode() == "single"


def test_mode_defaults_to_single_for_mixed_gjf_inp(qapp):
    sources = [_src("a.gjf", "gjf"), _src("b.inp", "inp")]
    dlg = SubmitDialog("en", files=sources)
    assert dlg.mode() == "single"


def test_mode_forces_workflow_when_xyz_present(qapp):
    sources = [_src("a.gjf", "gjf"), _src("b.xyz", "xyz")]
    dlg = SubmitDialog("en", files=sources)
    assert dlg.mode() == "workflow"


def test_single_radio_disabled_when_xyz_present(qapp):
    sources = [_src("a.xyz", "xyz")]
    dlg = SubmitDialog("en", files=sources)
    assert dlg.single_radio.isEnabled() is False


def test_charge_and_server_flow_into_single_payload(qapp, monkeypatch):
    sources = [_src("a.gjf", "gjf")]
    dlg = SubmitDialog("en", files=sources, server_id="prod-01")
    dlg.charge_spin.setValue(2)
    payload = dlg.build_payload()
    assert isinstance(payload, SubmitPayload)
    assert payload.kind == "single"
    assert payload.calc.charge == 2
    assert payload.server_id == "prod-01"
    assert payload.program == "gaussian"


def test_inp_only_payload_program_is_orca(qapp):
    sources = [_src("a.inp", "inp")]
    dlg = SubmitDialog("en", files=sources, server_id="prod-01")
    payload = dlg.build_payload()
    assert payload.program == "orca"


def test_workflow_payload_uses_selected_preset(qapp, tmp_path, monkeypatch):
    from jobdesk_app.core.workflow_spec import WorkflowSpec
    monkeypatch.setattr(
        "jobdesk_app.services.method_presets.get_app_data_dir",
        lambda: tmp_path,
    )
    store = MethodPresetStore()
    spec = WorkflowSpec.from_form(
        work_dir_name="x", program="gaussian", method="B3LYP",
        basis="6-31G(d)", charge=0, multiplicity=1, nproc=4, memory_mb=4096,
    )
    store.save_user("my_preset", spec)

    sources = [_src("a.xyz", "xyz")]
    dlg = SubmitDialog("en", files=sources, server_id="prod-01",
                       preset_store=store)
    dlg.set_selected_preset_name("my_preset")
    payload = dlg.build_payload()
    assert payload.kind in {"confflow", "dag"}
    assert payload.program == "gaussian"
    assert payload.calc.method_basis == "B3LYP 6-31G(d)"
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_submit_dialog.py -q --basetemp=.pytest_task5_red`

Expected: ALL tests FAIL with `ModuleNotFoundError: No module named 'jobdesk_app.gui.dialogs.submit_dialog'`.

- [ ] **Step 3: Implement `SubmitDialog`**

```python
# src/jobdesk_app/gui/dialogs/submit_dialog.py
"""Modal submit dialog with auto-detected Single / Workflow mode.

The dialog inspects the selected ``InputSource`` list and switches its
default Mode:

* Only ``.gjf`` and ``.inp`` → Single (Gaussian / ORCA direct run).
* Any ``.xyz`` (or unknown suffix) → Workflow forced; Single radio is
  disabled and greyed out.

The dialog emits a fully formed :class:`SubmitPayload` on accept so the
caller (``MainWindow._on_submit_requested``) is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.submit_payload import (
    DagWorkflowFields,
    InputSource,
    SubmitKind,
    SubmitPayload,
    WorkflowFields,
)
from ...core.workflow_spec import WorkflowSpec
from ...services.method_presets import MethodPresetStore
from ..i18n import tr


_MODE_LABEL = {"single": "Single calculation", "workflow": "Workflow"}


def _infer_program(sources: list[InputSource]) -> str:
    """Pick gaussian/orca based on the majority suffix of selected files."""
    counts = {"gjf": 0, "inp": 0, "xyz": 0}
    for src in sources:
        counts[src.kind] = counts.get(src.kind, 0) + 1
    if counts["inp"] > counts["gjf"]:
        return "orca"
    return "gaussian"


def _requires_workflow(sources: list[InputSource]) -> bool:
    """Workflow is mandatory when any input is not a fully-formed input file."""
    return any(s.kind != "gjf" and s.kind != "inp" for s in sources)


@dataclass(frozen=True)
class _CalculationFieldsShim:
    program: str
    preset_name: str | None
    method_basis: str
    job_keywords: list[str]
    charge: int
    multiplicity: int
    nproc: int
    mem: str


class SubmitDialog(QDialog):
    """Modal that produces a :class:`SubmitPayload` on accept.

    Constructed by ``MainWindow.open_submit_dialog``. Emits ``accepted``
    via the standard ``QDialog`` mechanism; the caller reads
    ``build_payload()`` immediately after ``exec()`` returns
    ``QDialog.Accepted``.
    """

    def __init__(
        self,
        language: str,
        *,
        files: list[InputSource],
        server_id: str = "",
        remote_dir: str = "/",
        max_parallel: int = 1,
        preset_store: MethodPresetStore | None = None,
        preset_name: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._language = language
        self._files = files
        self._server_id = server_id
        self._remote_dir = remote_dir
        self._max_parallel = max_parallel
        self._preset_store = preset_store or MethodPresetStore()
        self._preset_name = preset_name

        self.setWindowTitle(tr("Submit for calculation", language))
        self.setMinimumWidth(540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # ── File list (read-only) ──
        layout.addWidget(self._build_file_summary())
        layout.addWidget(self._build_mode_box())
        layout.addWidget(self._build_workflow_box())
        layout.addWidget(self._build_globals_box())

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            tr("Submit ▶", language)
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_mode()
        self._refresh_preset_combo()

    # ── UI builders ──

    def _build_file_summary(self) -> QWidget:
        n = len(self._files)
        label = QLabel(tr("Selected files ({n})", self._language, n=n))
        list_widget = QListWidget()
        for src in self._files:
            item = QListWidgetItem(f"{src.path.name} ({src.kind})")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            list_widget.addItem(item)
        list_widget.setMaximumHeight(80)
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(label)
        v.addWidget(list_widget)
        self.file_list = list_widget
        return wrap

    def _build_mode_box(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(tr("Mode:", self._language)))
        self.single_radio = QRadioButton(tr("Single calculation", self._language))
        self.workflow_radio = QRadioButton(tr("Workflow", self._language))
        self.single_radio.toggled.connect(self._refresh_mode)
        self.workflow_radio.toggled.connect(self._refresh_mode)
        row = QHBoxLayout()
        row.addWidget(self.single_radio)
        row.addWidget(self.workflow_radio)
        row.addStretch()
        layout.addLayout(row)
        self._mode_hint = QLabel("")
        self._mode_hint.setStyleSheet("color: #b54708; font-style: italic;")
        layout.addWidget(self._mode_hint)
        return box

    def _build_workflow_box(self) -> QWidget:
        box = QWidget()
        layout = QFormLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        self.preset_combo = QComboBox()
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        layout.addRow(tr("Workflow preset:", self._language), self.preset_combo)
        return box

    def _build_globals_box(self) -> QWidget:
        box = QWidget()
        layout = QFormLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        self.charge_spin = QSpinBox()
        self.charge_spin.setRange(-99, 99)
        self.charge_spin.setValue(0)
        self.mult_spin = QSpinBox()
        self.mult_spin.setRange(1, 10)
        self.mult_spin.setValue(1)
        self.server_combo = QComboBox()
        self.server_combo.addItem(self._server_id or tr("No server", self._language))
        if not self._server_id:
            self.server_combo.setEnabled(False)
        layout.addRow(tr("Charge:", self._language), self.charge_spin)
        layout.addRow(tr("Multiplicity:", self._language), self.mult_spin)
        layout.addRow(tr("Server:", self._language), self.server_combo)
        return box

    # ── State refresh ──

    def mode(self) -> str:
        return "workflow" if self.workflow_radio.isChecked() else "single"

    def _refresh_mode(self) -> None:
        requires_workflow = _requires_workflow(self._files)
        if requires_workflow:
            self.single_radio.setEnabled(False)
            self.single_radio.setChecked(False)
            self.workflow_radio.setChecked(True)
            self._mode_hint.setText(
                tr("Workflow required for non-Gaussian/ORCA inputs", self._language)
            )
        else:
            self.single_radio.setEnabled(True)
            if not self.workflow_radio.isChecked() and not self.single_radio.isChecked():
                self.single_radio.setChecked(True)
            self._mode_hint.setText("")
        self.preset_combo.setEnabled(self.mode() == "workflow")

    def _refresh_preset_combo(self) -> None:
        self.preset_combo.clear()
        for preset in self._preset_store.list_presets():
            label = f"{preset.name}  ({tr(preset.source.capitalize(), self._language)})"
            self.preset_combo.addItem(label, preset.name)
        if self._preset_name:
            idx = self.preset_combo.findData(self._preset_name)
            if idx >= 0:
                self.preset_combo.setCurrentIndex(idx)

    def set_selected_preset_name(self, name: str) -> None:
        self._preset_name = name
        self._refresh_preset_combo()

    def _on_preset_changed(self, _index: int) -> None:
        data = self.preset_combo.currentData()
        if isinstance(data, str):
            self._preset_name = data

    # ── Payload assembly ──

    def build_payload(self) -> SubmitPayload:
        files = list(self._files)
        first = files[0].path
        output_dir = first.parent if first.is_absolute() else first
        work_dir_name = f"{first.stem or 'job'}_work"
        server_id = self._server_id
        remote_dir = self._remote_dir
        max_parallel = self._max_parallel
        charge = self.charge_spin.value()
        mult = self.mult_spin.value()

        if self.mode() == "single":
            program = _infer_program(files)
            calc = _CalculationFieldsShim(
                program=program,
                preset_name=None,
                method_basis="",
                job_keywords=[],
                charge=charge,
                multiplicity=mult,
                nproc=8,
                mem="4GB",
            )
            return SubmitPayload(
                kind="single",
                inputs=files,
                program=program,
                calc=calc,
                workflow=None,
                output_dir=output_dir,
                output_paths=[],
                server_id=server_id,
                remote_dir=remote_dir,
                max_parallel=max_parallel,
            )

        # mode == workflow
        preset = self._preset_store.load(self._preset_name or "", source="user")
        form = preset.to_form()
        program = form.get("program") or "gaussian"
        method_basis = " ".join(p for p in (form.get("method", ""), form.get("basis", "")) if p)
        steps = list(form.get("steps", []))
        if any(self._has_fan_in(steps) for _ in [0]):
            pass
        # Detect fan-in vs linear based on the original graph; we approximate
        # via the workflow spec by trying conflow dag flow first; the
        # ``SubmitUseCase._detect_payload_kind`` rule is mirrored here.
        kind: SubmitKind = "confflow" if all(s in {"confgen", "opt", "sp", "freq", "opt_freq", "preopt", "refine", "ts"} for s in steps) else "dag"
        calc = _CalculationFieldsShim(
            program=program,
            preset_name=preset.name if hasattr(preset, 'name') else None,
            method_basis=method_basis,
            job_keywords=[],
            charge=charge,
            multiplicity=mult,
            nproc=int(form.get("nproc", 8) or 8),
            mem=f"{int(form.get('memory_mb', 4096) or 4096)}MB",
        )
        if kind == "dag":
            return SubmitPayload(
                kind="dag",
                inputs=files,
                program=program,
                calc=calc,
                workflow=None,
                dag=DagWorkflowFields(
                    work_dir_name=work_dir_name,
                    steps=list(preset.global_config.model_dump(mode="json").get("steps") or []),
                    advanced_options={},
                ),
                output_dir=output_dir,
                server_id=server_id,
                remote_dir=remote_dir,
                max_parallel=max_parallel,
            )
        return SubmitPayload(
            kind="confflow",
            inputs=files,
            program=program,
            calc=calc,
            workflow=WorkflowFields(
                work_dir_name=work_dir_name,
                steps=steps,
                advanced_options={},
            ),
            output_dir=output_dir,
            server_id=server_id,
            remote_dir=remote_dir,
            max_parallel=max_parallel,
        )

    @staticmethod
    def _has_fan_in(steps: list[Any]) -> bool:
        # Conservative: callers that need true fan-in come from the
        # nodegraph editor; the dialog always serialises through
        # ``WorkflowSpec`` which loses source-level DAG info, so we
        # always choose ``kind="confflow"`` here. Update once
        # ``WorkflowSpec`` exposes graph topology.
        return False


__all__ = ["SubmitDialog"]
```

> Note: the implementation intentionally favours linear `confflow` payloads from the dialog because `WorkflowSpec.to_form()` strips the node graph's edge topology. Future improvement: serialise `WorkflowSpec` back to its source `NodeGraph` first so DAG detection works. Documented in `docs/architecture.md` per Task 12.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_submit_dialog.py -q --basetemp=.pytest_task5_green`

Expected: ALL eight tests PASS.

---

### Task 6: `gui/dialogs/workflow_builder_dialog.py`

**Files:**
- New: `src/jobdesk_app/gui/dialogs/workflow_builder_dialog.py`
- Test: `tests/test_workflow_dialog.py`

- [ ] **Step 1: Write RED tests**

```python
# tests/test_workflow_dialog.py
from jobdesk_app.core.workflow_spec import WorkflowSpec
from jobdesk_app.gui.dialogs.workflow_builder_dialog import WorkflowBuilderDialog
from jobdesk_app.services.method_presets import MethodPresetStore


def test_dialog_embeds_editor(qtbot):
    dlg = WorkflowBuilderDialog(language="en", preset_store=MethodPresetStore())
    assert dlg.editor is not None
    assert dlg.editor.is_empty()  # starts blank


def test_dialog_loads_initial_spec(qtbot):
    spec = WorkflowSpec.from_form(
        work_dir_name="x", program="gaussian", method="B3LYP",
        basis="6-31G(d)", charge=0, multiplicity=1, nproc=4, memory_mb=4096,
    )
    dlg = WorkflowBuilderDialog(language="en",
                                 preset_store=MethodPresetStore(),
                                 initial_spec=spec)
    assert not dlg.editor.is_empty()


def test_dialog_accept_returns_spec(qtbot):
    spec = WorkflowSpec.from_form(
        work_dir_name="x", program="gaussian", method="B3LYP",
        basis="6-31G(d)", charge=0, multiplicity=1, nproc=4, memory_mb=4096,
    )
    dlg = WorkflowBuilderDialog(language="en",
                                 preset_store=MethodPresetStore(),
                                 initial_spec=spec)
    dlg._on_accept()  # bypass the modal exec
    assert dlg.result_spec() is spec
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_workflow_dialog.py -q --basetemp=.pytest_task6_red`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `WorkflowBuilderDialog`**

```python
# src/jobdesk_app/gui/dialogs/workflow_builder_dialog.py
"""Modal editor for a single workflow preset.

Hosts the existing :class:`WorkflowGraphEditor` and converts between
the editor's :class:`NodeGraph` view and the on-disk
:class:`WorkflowSpec`. Returns the resulting ``WorkflowSpec`` on
``accept()``; ``reject()`` closes without changes.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget

from ...core.workflow_spec import WorkflowSpec
from ...gui.nodegraph.editor import WorkflowGraphEditor
from ...gui.nodegraph.model import NodeGraph
from ...gui.nodegraph.serialization import from_json, to_json
from ...services.method_presets import MethodPresetStore
from ..i18n import tr


class WorkflowBuilderDialog(QDialog):
    """Host the editor and provide Save / Cancel semantics."""

    def __init__(
        self,
        language: str,
        *,
        preset_store: MethodPresetStore,
        initial_spec: WorkflowSpec | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._language = language
        self._preset_store = preset_store
        self._result_spec: Optional[WorkflowSpec] = None
        self.setWindowTitle(tr("Workflow builder", language))
        self.setMinimumSize(960, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.editor = WorkflowGraphEditor(language=language)
        layout.addWidget(self.editor, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            tr("Save", language)
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if initial_spec is not None:
            self._populate(initial_spec)

    def _populate(self, spec: WorkflowSpec) -> None:
        graph_dict = self._spec_to_graph_dict(spec)
        graph = from_json(graph_dict)
        self.editor.set_graph(graph)

    def _on_accept(self) -> None:
        # Round-trip: NodeGraph -> JSON-dict -> WorkflowSpec.
        # The current ``to_workflow_spec`` bridge lives under
        # ``jobdesk_app.gui.nodegraph`` — reuse it.
        from ...gui.nodegraph.spec_bridge import to_workflow_spec
        payload = to_workflow_spec(self.editor.graph())
        self._result_spec = WorkflowSpec(global_config=payload.spec.global_config)
        self.accept()

    def result_spec(self) -> WorkflowSpec | None:
        return self._result_spec

    @staticmethod
    def _spec_to_graph_dict(spec: WorkflowSpec) -> dict:
        """Project a :class:`WorkflowSpec` back to a graph JSON dict.

        Inverse of :func:`nodegraph.spec_bridge.to_workflow_spec`.
        Implemented conservatively: build a linear chain of ``calc``
        nodes plus an enclosing ``xyz_file`` and ``output`` sentinel.
        """
        form = spec.to_form()
        steps = list(form.get("steps") or [])
        nodes = [
            {
                "id": "xb_xyz",
                "kind": "xyz_file",
                "title": "XYZ input",
                "inputs": [],
                "outputs": [
                    {"name": "out", "type": "structure", "direction": "out",
                     "label": "structure", "required": False}
                ],
                "params": {},
                "position": [40.0, 80.0],
            },
            {
                "id": "xb_output",
                "kind": "output",
                "title": "Output",
                "inputs": [],
                "outputs": [],
                "params": {},
                "position": [40.0 + 240 * (len(steps) + 1), 80.0],
            },
        ]
        prev = "xb_xyz"
        edges = []
        for i, step_token in enumerate(steps):
            node_id = f"xb_step_{i}"
            kind = step_token if step_token in {"confgen", "opt", "sp", "freq", "preopt", "refine", "ts"} else "opt"
            nodes.append({
                "id": node_id,
                "kind": kind,
                "title": step_token,
                "inputs": [{"name": "in", "type": "structure", "direction": "in",
                            "label": "in", "required": True}],
                "outputs": [{"name": "out", "type": "structure", "direction": "out",
                             "label": "out", "required": False}],
                "params": {
                    "program": form.get("program", "gaussian"),
                    "method": form.get("method", ""),
                    "basis": form.get("basis", ""),
                },
                "position": [40.0 + 240 * (i + 1), 80.0],
            })
            edges.append({
                "id": f"xb_e{i}",
                "src_node": prev,
                "src_port": "out",
                "dst_node": node_id,
                "dst_port": "in",
            })
            prev = node_id
        edges.append({
            "id": "xb_e_end",
            "src_node": prev,
            "src_port": "out",
            "dst_node": "xb_output",
            "dst_port": "in",
        })
        return {"nodes": nodes, "edges": edges}


__all__ = ["WorkflowBuilderDialog"]
```

> Caveat: the round-trip from `WorkflowSpec` back into a `NodeGraph` is approximate — it rebuilds the linear chain the bridge emits, losing topology metadata (DAG edges, fan-in/fan-out ports). For first-pass built-in presets that's acceptable; the user-edited path always edits the graph directly. Documented in `docs/architecture.md`.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_workflow_dialog.py -q --basetemp=.pytest_task6_green`

Expected: ALL three tests PASS.

---

### Task 7: `gui/pages/workflow_page.py`

**Files:**
- New: `src/jobdesk_app/gui/pages/workflow_page.py`
- Test: `tests/test_workflow_page.py`

- [ ] **Step 1: Write RED tests**

```python
# tests/test_workflow_page.py
import pytest
from jobdesk_app.gui.pages.workflow_page import WorkflowPage
from jobdesk_app.services.method_presets import MethodPresetStore


def test_default_view_loads_empty(qtbot):
    page = WorkflowPage(language="en", preset_store=MethodPresetStore())
    assert page.preset_combo.count() >= 1  # at least the built-ins


def test_use_for_submit_emits_signal(qtbot):
    page = WorkflowPage(language="en", preset_store=MethodPresetStore())
    captured = []
    page.preset_chosen_for_submit.connect(
        lambda name, source: captured.append((name, source))
    )
    page._on_use_for_submit()
    assert captured, "signal must fire when a preset is selected"


def test_save_user_prompt_emits_saved(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "jobdesk_app.services.method_presets.get_app_data_dir",
        lambda: tmp_path,
    )
    store = MethodPresetStore()
    page = WorkflowPage(language="en", preset_store=store)
    captured = []
    page.preset_saved.connect(lambda name, source: captured.append((name, source)))
    # Force the save path with a known name
    page._save_as_user("user_xyz")
    assert captured == [("user_xyz", "user")]


def test_apply_language_translates(qtbot):
    page = WorkflowPage(language="en", preset_store=MethodPresetStore())
    page.apply_language("zh")
    # Cheap assertion: title text rotated.
    assert page.preset_label.text() == "工作流预设"
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_workflow_page.py -q --basetemp=.pytest_task7_red`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `WorkflowPage`**

```python
# src/jobdesk_app/gui/pages/workflow_page.py
"""Sidebar Workflow page: list, save, and dispatch workflow presets.

Replaces the Phase-2 ``SubmitPage`` with a read-mostly view of
named presets plus a ``[Use this preset for submit]`` button that
navigates to Files with a pre-selected preset.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..button_feedback import ButtonRole, apply_button_role
from ..i18n import tr
from ..widgets.input_source_panel import InputSourcePanel  # reuse server pill style
from ...services.method_presets import MethodPreset, MethodPresetStore


class WorkflowPage(QWidget):
    """Sidebar page (index 1) for browsing and saving workflow presets."""

    preset_chosen_for_submit = Signal(str, str)  # (name, source)
    preset_saved = Signal(str, str)              # (name, source)
    tour_requested = Signal()                    # propagated from optional embedded editor

    def __init__(
        self,
        state,
        *,
        language: str = "en",
        preset_store: MethodPresetStore,
        settings_store=None,
        on_status=None,
        on_error=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._language = language
        self._state = state
        self._store = preset_store
        self._settings_store = settings_store
        self._on_status = on_status or (lambda msg: None)
        self._on_error = on_error or (lambda title, msg: None)
        self._current_preset: MethodPreset | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        # ── Title ──
        self.preset_label = QLabel(tr("Workflow presets", language))
        font = self.preset_label.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        self.preset_label.setFont(font)
        layout.addWidget(self.preset_label)

        # ── Selector row ──
        selector_row = QHBoxLayout()
        selector_row.setSpacing(8)
        self.preset_combo = QComboBox()
        self.preset_combo.currentIndexChanged.connect(self._on_preset_combo_changed)
        selector_row.addWidget(self.preset_combo, 1)
        self.btn_new = QPushButton(tr("New workflow", language))
        self.btn_new.clicked.connect(self._on_new_workflow)
        selector_row.addWidget(self.btn_new)
        self.btn_import = QPushButton(tr("Import", language))
        self.btn_import.clicked.connect(self._on_import)
        selector_row.addWidget(self.btn_import)
        self.btn_export = QPushButton(tr("Export", language))
        self.btn_export.clicked.connect(self._on_export)
        selector_row.addWidget(self.btn_export)
        layout.addLayout(selector_row)

        # ── Step list (read-only) ──
        self.step_list = QListWidget()
        self.step_list.setMinimumHeight(180)
        layout.addWidget(self.step_list, 1)

        # ── Action row ──
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.btn_save_user = apply_button_role(
            QPushButton(tr("Save as user preset", language)),
            ButtonRole.INSTANT_ACTION,
        )
        self.btn_save_user.clicked.connect(self._on_save_user_clicked)
        action_row.addWidget(self.btn_save_user)
        action_row.addStretch()
        layout.addLayout(action_row)

        # ── Server pill row (mirrors Files / Submit back-compat) ──
        server_row = QHBoxLayout()
        server_row.setSpacing(8)
        self.server_pill = QLabel(tr("No server", language))
        self.server_pill.setStyleSheet("padding: 4px 10px; border-radius: 10px;")
        server_row.addWidget(self.server_pill)
        server_row.addStretch()
        layout.addLayout(server_row)

        # ── Dispatch ──
        self.btn_dispatch = QPushButton(tr("Use this preset for submit", language))
        self.btn_dispatch.setObjectName("WorkflowDispatchBtn")
        apply_button_role(self.btn_dispatch, ButtonRole.PRIMARY_ACTION)
        self.btn_dispatch.clicked.connect(self._on_use_for_submit)
        layout.addWidget(self.btn_dispatch)

        self._refresh_preset_combo()
        self._refresh_step_list()

    # ── Public API ──

    def apply_language(self, language: str) -> None:
        self._language = language
        self.preset_label.setText(tr("Workflow presets", language))
        self.btn_new.setText(tr("New workflow", language))
        self.btn_import.setText(tr("Import", language))
        self.btn_export.setText(tr("Export", language))
        self.btn_save_user.setText(tr("Save as user preset", language))
        self.btn_dispatch.setText(tr("Use this preset for submit", language))
        if not self._server_label_visible():
            self.server_pill.setText(tr("No server", language))
        self._refresh_preset_combo()
        self._refresh_step_list()

    def set_server_status(self, connected: bool, server_label: str = "") -> None:
        if server_label:
            self.server_pill.setText(
                tr("Server pill: {label}", self._language, label=server_label)
            )
        else:
            self.server_pill.setText(tr("No server", self._language))

    def set_remote_dir(self, remote_dir: str) -> None:
        # Surfaced only as a label on the server row; the dialog reads
        # the actual remote_dir off MainWindow.state at submit time.
        self._remote_dir = remote_dir

    # ── Internal helpers ──

    def _server_label_visible(self) -> bool:
        return bool(getattr(self, "_current_server_label", ""))

    def _refresh_preset_combo(self) -> None:
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for preset in self._store.list_presets():
            label = f"{preset.name}  ({tr(preset.source.capitalize(), self._language)})"
            self.preset_combo.addItem(label, (preset.name, preset.source))
        self.preset_combo.blockSignals(False)
        if self.preset_combo.count() > 0:
            self.preset_combo.setCurrentIndex(0)
            self._on_preset_combo_changed(0)

    def _refresh_step_list(self) -> None:
        self.step_list.clear()
        if self._current_preset is None:
            return
        form = self._current_preset.spec.to_form()
        steps = form.get("steps", [])
        if not steps:
            self.step_list.addItem(QListWidgetItem("—"))
        for i, step in enumerate(steps, start=1):
            self.step_list.addItem(QListWidgetItem(f"{i}. {step}"))

    def _on_preset_combo_changed(self, _index: int) -> None:
        data = self.preset_combo.currentData()
        if not data:
            return
        name, source = data
        try:
            spec = self._store.load(name, source=source)
        except KeyError:
            return
        # Locate the MethodPreset for the step list preview.
        for p in self._store.list_presets():
            if p.name == name and p.source == source:
                self._current_preset = p
                break
        self._refresh_step_list()

    def _on_new_workflow(self) -> None:
        # Clear current selection — the user is expected to use the
        # SubmitDialog's [Edit workflow] button to author from scratch.
        self._current_preset = None
        self._refresh_step_list()

    def _on_import(self) -> None:
        # Stub: file dialog + spec load. Deferred to Task 11 (out-of-scope
        # for the first plan; the Files path can re-export YAML).
        self._on_status(self.tr_or_status("Import coming soon."))

    def _on_export(self) -> None:
        if self._current_preset is None:
            return
        # Write the spec back to YAML and surface the path.
        path = self._current_preset.path
        self._on_status(self.tr_or_status(f"Path: {path}"))

    def _on_save_user_clicked(self) -> None:
        name, ok = self._prompt_for_name()
        if not ok or not name:
            return
        self._save_as_user(name)

    def _save_as_user(self, name: str) -> None:
        if self._current_preset is None:
            self._on_error(
                tr("Save as user preset", self._language),
                tr("Add a step first.", self._language),
            )
            return
        try:
            path = self._store.save_user(name, self._current_preset.spec)
            self.preset_saved.emit(name, "user")
            self._on_status(self.tr_or_status(f"Saved {path}"))
            self._refresh_preset_combo()
        except Exception as exc:  # pragma: no cover - defensive
            self._on_error("Save preset", str(exc))

    def _prompt_for_name(self) -> tuple[str, bool]:
        # Lightweight inline prompt; can be upgraded to a dedicated dialog.
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self,
            tr("Save as user preset", self._language),
            tr("Preset name:", self._language),
        )
        return name.strip(), ok and bool(name.strip())

    def _on_use_for_submit(self) -> None:
        if self._current_preset is None:
            self._on_error(
                tr("Use this preset for submit", self._language),
                tr("Pick a preset first.", self._language),
            )
            return
        self.preset_chosen_for_submit.emit(
            self._current_preset.name, self._current_preset.source
        )

    @staticmethod
    def tr_or_status(text: str) -> str:
        return text


__all__ = ["WorkflowPage"]
```

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_workflow_page.py -q --basetemp=.pytest_task7_green`

Expected: ALL four tests PASS.

---

### Task 8: Wire `gui/pages/file_transfer_page.py`

**Files:**
- Modify: `src/jobdesk_app/gui/pages/file_transfer_page.py`
- Test: `tests/test_file_transfer_page.py` (extend existing)

- [ ] **Step 1: Write RED tests**

Append to `tests/test_file_transfer_page.py` (or create it if absent):

```python
def test_submit_button_disabled_with_no_selection(qtbot):
    page = FileTransferPage(state, log, status, error)
    assert page.submit_btn.isEnabled() is False


def test_submit_button_emits_signal_with_selection(qtbot, monkeypatch):
    captured = []
    page = FileTransferPage(state, log, status, error)
    page.submit_requested_with_files.connect(lambda src: captured.append(src))
    # Insert a fake selection by calling the helper directly — the
    # table widget mock is overkill for this assertion.
    page._selected_paths_for_side = lambda side: (["/tmp/a.gjf"] if side == "local" else [])
    page._on_submit_clicked()
    assert captured and len(captured[0]) == 1
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_file_transfer_page.py -q --basetemp=.pytest_task8_red`

Expected: `AttributeError: 'FileTransferPage' object has no attribute 'submit_btn'`.

- [ ] **Step 3: Add the submit button + signal**

Open `src/jobdesk_app/gui/pages/file_transfer_page.py` and locate the bottom button row (search for `btn_clear`, `btn_open_settings`, etc.). Add alongside:

```python
self.submit_btn = QPushButton(tr("Submit (selected files)", self._language))
self.submit_btn.setObjectName("FilesSubmitBtn")
apply_button_role(self.submit_btn, ButtonRole.PRIMARY_ACTION)
self.submit_btn.setEnabled(False)
self.submit_btn.clicked.connect(self._on_submit_clicked)
button_row.insertWidget(0, self.submit_btn)  # primary, leftmost
```

Add a new signal at the top of the class:

```python
submit_requested_with_files = Signal(list)  # list[InputSource]
```

Add the handler:

```python
def _on_submit_clicked(self) -> None:
    # Prefer remote paths when the user is connected, then local.
    paths = self._selected_paths_for_side("remote") or self._selected_paths_for_side("local")
    if not paths:
        return
    side = "remote" if self._selected_paths_for_side("remote") else "local"
    sources = self._build_input_sources(paths, side=side)
    self.submit_requested_with_files.emit(sources)


def _refresh_submit_button(self) -> None:
    n_remote = len(self._selected_paths_for_side("remote"))
    n_local = len(self._selected_paths_for_side("local"))
    self.submit_btn.setEnabled((n_remote + n_local) > 0)
```

Wire `_refresh_submit_button` into any existing `selectionChanged` hook by searching for `selectionChanged` in the file and connecting once. If there's no such hook, connect to the `local_table.itemSelectionChanged` and `remote_table.itemSelectionChanged` signals.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_file_transfer_page.py -q --basetemp=.pytest_task8_green`

Expected: ALL tests in this file PASS.

---

### Task 9: Replace `SubmitPage` with `WorkflowPage` in `main_window.py`

**Files:**
- Modify: `src/jobdesk_app/gui/main_window.py`
- Delete: `src/jobdesk_app/gui/pages/submit_page.py`
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py` — `go_to_submit_requested` semantics

- [ ] **Step 1: Write RED tests**

Append to `tests/test_main_window.py`:

```python
def test_nav_index_1_is_workflow(qapp):
    window = MainWindow()
    from jobdesk_app.gui.main_window import _NAV_ITEMS
    assert _NAV_ITEMS[1][1] == "Workflow"


def test_workflow_page_replaces_submit_page(qapp):
    window = MainWindow()
    assert isinstance(window.workflow_page, WorkflowPage)
    assert not hasattr(window, "submit_page")


def test_preset_chosen_signal_opens_submit_dialog(qapp, monkeypatch):
    window = MainWindow()
    # Stub: capture dialog construction.
    calls = []
    def fake_open(sources, preset_name):
        calls.append((sources, preset_name))
    monkeypatch.setattr(window.files_page, "open_submit_dialog", fake_open)
    monkeypatch.setattr(window.shell, "pages", SimpleNamespace(
        widget=MagicMock(return_value=window.files_page),
        setCurrentIndex=MagicMock(),
    ))
    window.workflow_page.preset_chosen_for_submit.emit("b3lyp_631gd_opt_freq", "builtin")
    assert calls and calls[0][1] == "b3lyp_631gd_opt_freq"
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_main_window.py -q --basetemp=.pytest_task9_red`

Expected: `AttributeError: 'MainWindow' object has no attribute 'workflow_page'` plus the missing `WorkflowPage` symbol.

- [ ] **Step 3: Remove the old page + register the new one**

In `src/jobdesk_app/gui/main_window.py`:

(a) Change `_NAV_ITEMS`:

```python
_NAV_ITEMS = [
    ("folder", "Files"),
    ("workflow", "Workflow"),
    ("bar-chart", "Runs"),
    ("settings", "Settings"),
]
```

(b) Replace `submit_page` instantiation with `workflow_page`:

```python
from .pages.file_transfer_page import FileTransferPage
from .pages.runs_results_page import RunsResultsPage
from .pages.settings_servers_page import SettingsServersPage
from .pages.workflow_page import WorkflowPage      # new
from .services.method_presets import MethodPresetStore

# Inside MainWindow.__init__:
self.preset_store = MethodPresetStore()
self.workflow_page = WorkflowPage(
    self.state,
    language=self.language,
    preset_store=self.preset_store,
    settings_store=self._settings_store,
    on_status=self._update_status,
    on_error=self.show_error,
)
self.workflow_page.preset_chosen_for_submit.connect(self._open_submit_dialog)
```

(c) Update page registration (sub `submit_page` for `workflow_page`):

```python
self.shell.add_page(self.files_page)     # 0
self.shell.add_page(self.workflow_page)  # 1
self.shell.add_page(self.runs_page)      # 2
self.shell.add_page(self.settings_page)  # 3
```

(d) Replace the `submit_requested` connection block and the `_on_nav(index == 1)` page call:

```python
# Was: self.submit_page.submit_requested.connect(self._on_submit_requested)
# Now: dialog emits through files_page.open_submit_dialog → user accepts → MainWindow signal flow
self.files_page.submit_requested_with_files.connect(self._show_submit_dialog)
```

(e) In `_on_nav`, change the `index == 1 and page is self.submit_page:` branch to `index == 1 and page is self.workflow_page:` — same operations, just renamed variable.

(f) Add `_show_submit_dialog` and `_open_submit_dialog`:

```python
def _show_submit_dialog(self, sources: list[InputSource]) -> None:
    """Open the SubmitDialog wired with the given input sources."""
    from ..core.submit_payload import InputSource as _InputSource
    sources = [_InputSource(path=s.path, side=s.side, kind=s.kind) for s in sources]
    from .dialogs.submit_dialog import SubmitDialog
    dialog = SubmitDialog(
        language=self.language,
        files=sources,
        server_id=self.files_page._connected_server_id or "",
        remote_dir=(self.files_page.remote_path.text().strip() or "/")
                    if hasattr(self.files_page, "remote_path") else "/",
        max_parallel=self.files_page.max_parallel_spin.value()
                     if hasattr(self.files_page, "max_parallel_spin") else 1,
        preset_store=self.preset_store,
        parent=self,
    )
    if dialog.exec() == dialog.DialogCode.Accepted:
        self._on_submit_requested(dialog.build_payload())


def _open_submit_dialog(self, preset_name: str, preset_source: str) -> None:
    """Workflow page dispatch → switch to Files page and open SubmitDialog."""
    self._switch_page(0)  # Files
    QTimer.singleShot(0, lambda: self._show_submit_dialog_with_preset(preset_name))


def _show_submit_dialog_with_preset(self, preset_name: str) -> None:
    """Helper: launch SubmitDialog with a pre-selected preset."""
    paths = []
    sources_local = self.files_page._selected_paths_for_side("local")
    sources_remote = self.files_page._selected_paths_for_side("remote")
    paths = sources_remote or sources_local
    side = "remote" if sources_remote else "local"
    if not paths:
        self.show_error(
            tr("Submit", self.language),
            tr("Add at least one input file.", self.language),
        )
        return
    sources = self.files_page._build_input_sources(paths, side=side)
    from .dialogs.submit_dialog import SubmitDialog
    dialog = SubmitDialog(
        language=self.language,
        files=sources,
        server_id=self.files_page._connected_server_id or "",
        remote_dir=self.files_page.remote_path.text().strip() or "/",
        max_parallel=self.files_page.max_parallel_spin.value(),
        preset_store=self.preset_store,
        preset_name=preset_name,
        parent=self,
    )
    if dialog.exec() == dialog.DialogCode.Accepted:
        self._on_submit_requested(dialog.build_payload())
```

(g) Delete the file `src/jobdesk_app/gui/pages/submit_page.py`.

(h) Update `src/jobdesk_app/gui/pages/runs_results_page.py` — find `go_to_submit_requested.emit()` sites and reroute to MainWindow's `_open_submit_dialog` (or directly `lambda: self._show_submit_dialog([])`, depending on whether the empty-sources path is acceptable; document the choice in a comment).

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_main_window.py -q --basetemp=.pytest_task9_green`

Expected: ALL tests PASS.

Run: `pytest tests/test_nodegraph -q --basetemp=.pytest_task9_nodegraph`

Expected: PASS — the nodegraph editor is independent of the page tree, but its `tour_requested` signal was previously connected to `submit_page.editor.tour_requested`. If this test relies on that, swap the connection to `workflow_page.editor` if the workflow page also embeds the editor in the future. For Phase 5 first cut, no editor is embedded; the tour button is currently not surfaced. Leave a `TODO` comment in `main_window.py`.

---

### Task 10: Delete obsolete tests and rewrite `test_main_window.py`

**Files:**
- Delete: `tests/test_submit_page.py`
- Modify: `tests/test_nodegraph/test_gui_review_fixes.py` — drop F5 regression
- Modify: `tests/test_workflow_tour_dialog.py` — adjust imports

- [ ] **Step 1: Delete `tests/test_submit_page.py`**

`rm tests/test_submit_page.py` (or `Remove-Item` on PowerShell).

- [ ] **Step 2: Drop the F5 Submit-page regression**

Open `tests/test_nodegraph/test_gui_review_fixes.py`. Search for `test_submit_page` or `SubmitPage`. Remove that test function and its imports. If the file still references `SubmitPage` via `setUp`, follow the file's existing pattern and replace with `WorkflowPage` analogue (`test_workflow_page_group_titles_retranslate` reading only `preset_label.text()` before/after `apply_language("zh")`).

- [ ] **Step 3: Patch `tests/test_workflow_tour_dialog.py`**

Search for the import line. If it imported from `submit_page`, change it to a direct import of `WorkflowTourDialog` from `jobdesk_app.gui.dialogs.workflow_tour_dialog`. No behaviour change.

- [ ] **Step 4: Run the full suite**

Run: `pytest -q --basetemp=.pytest_full`

Expected: ALL tests PASS, aside from any pre-existing WSL / g16 skip markers and the optional `.ruff_cache` warnings.

---

### Task 11: Code hygiene & docs

**Files:**
- Modify: `docs/USER_GUIDE.md`
- Modify: `docs/architecture.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add a USER_GUIDE section for the dual entry**

Append:

```markdown
## Submitting calculations

There are two ways to submit a calculation in JobDesk 2.0:

### Option 1 — Files page (recommended for one-off submissions)

1. Switch to the **Files** page.
2. Select one or more input files (`.xyz`, `.gjf`, `.inp`).
3. Click **[🚀 Submit]**. A modal opens.
4. Choose **Single** (Gaussian/ORCA direct run) or **Workflow** (preset-based multi-step). The dialog disables Single automatically when any `.xyz` is selected.
5. Pick a preset if Workflow, set charge / multiplicity / server, click **Submit ▶**.

### Option 2 — Workflow page (use a saved preset)

1. Switch to the **Workflow** page.
2. Browse built-in or user presets in the dropdown. Edit, save, or rename.
3. Click **[Use this preset for submit]** to switch to Files and open the Submit dialog with that preset pre-selected.

User presets live under `%APPDATA%/JobDesk/method_presets/<name>.yaml` and are plain confflow YAML.
```

- [ ] **Step 2: Add an architecture section**

Append to `docs/architecture.md`:

```markdown
## Method Preset Store

`services/method_presets.py::MethodPresetStore` is the single source of truth for workflow presets. Both built-in presets (packaged under `jobdesk_app.resources.method_presets`) and user presets (`<appdata>/method_presets/`) load as `WorkflowSpec` via `WorkflowSpec.from_yaml()`. The store keeps the editor, the dialog, and the run service aligned on the same on-disk schema.

Lookup precedence: **user > built-in**, matching the behaviour of `services/analysis_profiles.py`.

Save path: `MethodPresetStore.save_user(name, spec)` writes `spec.to_yaml()` to `<user_dir>/<name>.yaml` via `core/atomic_write.atomic_write_text`. Renames go through temp+move; deletes are unconditional `unlink`.
```

- [ ] **Step 3: CHANGELOG entry**

```markdown
## 2.0 — Workflow Builder + Submit dual entry

* `gui/pages/submit_page.py` removed; replaced by `gui/pages/workflow_page.py` (sidebar preset manager) and `gui/dialogs/submit_dialog.py` (modal submit with auto Single/Workflow detection).
* New `services/method_presets.py::MethodPresetStore` ships 9 built-in presets and a writable user library at `<appdata>/method_presets/`.
* Files page gains a `[🚀 Submit]` button gated on selection.
* Single-mode payload (`kind="single"`) is now first-class; previously only Workflow/DAG was supported through the UI.
```

---

### Task 12: Final verification & handoff

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q --basetemp=.pytest_full_v2`

Expected: ALL tests PASS (sans env-dependent skips).

- [ ] **Step 2: Run quality gates**

Run: `ruff check .`

Expected: exit 0.

Run: `mypy src`

Expected: exit 0 (treat any third-party typing-noisy errors as acceptable as long as our new files type-check).

Run: `git diff --check origin/main`

Expected: no conflict markers.

- [ ] **Step 3: Smoke test the GUI**

Launch the app:

```bash
python -m jobdesk_app
```

Manual smoke checklist (do not script):

- [ ] Sidebar shows Files / Workflow / Runs / Settings. Workflow (not Submit) is at index 1.
- [ ] Workflow page lists built-in presets.
- [ ] Selecting one populates the read-only step list.
- [ ] Save as user preset asks for a name, writes YAML under `%APPDATA%/JobDesk/method_presets/`.
- [ ] Files page shows `[🚀 Submit]` button, disabled when no rows are selected.
- [ ] Selecting two `.gjf` files opens the modal; Single mode is selected by default; the Single radio is enabled.
- [ ] Selecting a `.xyz` with a `.gjf` opens the modal; Single is disabled and Workflow is forced; the workflow preset dropdown picks the user's saved preset.
- [ ] Clicking Submit ▶ closes the modal and the Runs page reports "Submitted".

- [ ] **Step 4: Inventory untracked changes**

Run: `git status --short --untracked-files=all`

Expected: only the new files, deleted files, and modified files described in the File map above are present. Nothing else (no `.pytest_cache`, no `__pycache__`, no `Gau-*.inp`).

- [ ] **Step 5: Handoff log**

Drop a short status note (no commit) for the verifier chat containing:

- `git status -s` output (truncated if long)
- `pytest -q --basetemp=.pytest_full_v2` summary line
- `ruff check .` and `mypy src` summary lines
- A bullet list of any test that was temporarily skipped, with reason

Do not commit, do not push. The verifier will inspect the diff and either accept or request further changes.

---

## Open questions for the verifier (the main session that authored this plan)

When you (the verifier) inspect the result, please confirm or reject:

1. **`WorkflowSpec`-back-to-`NodeGraph` projection**: Task 6 uses a conservative linear-chain projection. Is that acceptable for the first cut, or do you want fan-in/fan-out preservation up front?
2. **`SubmitDialog` DAG detection**: Task 5 always returns `kind="confflow"` for workflow mode because `WorkflowSpec` strips graph topology. Same question — accept, or wire up a `NodeGraph`-to-`WorkflowSpec` round-trip?
3. **`runs_results_page.go_to_submit_requested`**: redirected to Files + dialog in Task 9, step (h). Should the empty-sources case show a different banner ("Pick a file in Files first"), or is the current error dialog fine?
4. **Built-in preset selection**: 9 presets bundled in Task 2. The list is modelled on the legacy `input_builder.py` `GAUSSIAN_PRESETS` / `ORCA_PRESETS` plus one confflow confgen-SP demo. Want a different mix?
5. **`tour_requested` signal**: orphaned in the new tree (Workflow page does not embed the editor). Are you OK adding the editor to the Workflow page (optional `WorkflowPage.editor` attribute), or leave it for a follow-up?
