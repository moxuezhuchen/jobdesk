# Contributing to JobDesk

Thank you for your interest in contributing to JobDesk. This document
covers the workflow, conventions, and quality gates that apply to
every change before it lands.

## Ground rules

1. **No credentials in the repository.** SSH keys, tokens, hostnames,
   IP addresses, and anything that resembles production infrastructure
   must not appear in source, tests, fixtures, or screenshots. The
   pre-commit gate `scripts/check_public_tree.ps1` will block any
   commit that does.
2. **One commit per logical change.** A wizard feature should not
   ship alongside a CI fix; squash or reorder before opening a PR.
3. **Tests first when possible.** When fixing a bug, add a failing
   test that reproduces it, then make it pass. New code paths should
   arrive with a test that covers their public contract.

## Development environment

```powershell
py -3.13 -m venv .venv
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -e ".[dev]"
```

The commands explicitly use the project `.venv`; activation is optional. Keep
using `.venv\Scripts\python.exe` for subsequent commands so a global
JobDesk/ConfFlow installation cannot silently take precedence.

The base development extra is enough for non-chemistry paths. The full test
suite and workflow/node-graph tests require the optional chemistry stack too:

```powershell
$venvPython = ".venv\Scripts\python.exe"
# Put the approved wheel at .matrix-artifacts before syncing the lock.
uv pip sync --python $venvPython --find-links .matrix-artifacts `
  requirements\locks\jobdesk-chem-py313-win_amd64.txt
& $venvPython -m pip install --no-deps -e .
& $venvPython -m pip check
& $venvPython -c "import confflow, importlib.metadata as metadata, pathlib, sys; venv=pathlib.Path(sys.executable).resolve().parents[1]; source=pathlib.Path(confflow.__file__).resolve(); assert metadata.version('jobdesk') == '0.7.10'; assert metadata.version('confflow') == '2.1.6'; assert source.is_relative_to(venv / 'Lib' / 'site-packages'); print(confflow.__version__, source)"
```

The lock requires the released `confflow-2.1.6-py3-none-any.whl` and its
SHA-256 `d8fe44611ec128fece79309f42792b716c1f2f59871b5aab4024f3d136f75548`.
This chemistry lock setup is a prerequisite for the full test suite and
workflow/node-graph tests. Regenerate or check it with
`powershell -ExecutionPolicy Bypass -File scripts/compile_chem_locks.ps1`.

## Test / lint / type-check cycle

The project uses `ruff` for linting, `mypy` for type-checking, and
`pytest` (with `pytest-qt`) for tests. CI runs all three on
Python 3.11 / 3.12 / 3.13 against `windows-latest`; please run the
same on your machine before pushing.

```powershell
& ".venv\Scripts\python.exe" -m ruff check .
& ".venv\Scripts\python.exe" -m mypy src
& ".venv\Scripts\python.exe" -m pytest tests -q --basetemp .pytest_tmp_dev -p no:cacheprovider
```

Tests that hit real SSH, SFTP, or the ConfFlow binary are gated by
environment variables and excluded from the default test run via
`addopts = "-m 'not integration'"`. Do not run them unless you have
the corresponding local infrastructure set up.

## Pre-commit hygiene

Before opening a PR, run:

```powershell
powershell -File scripts/check_public_tree.ps1
```

This must print `public-tree-ok`. CI runs the same script and will
fail any PR that does not.

## Commit messages

The project follows `<type>(<scope>): <subject>`:

- `feat(confflow): …` — new user-visible feature
- `fix(scheduler): …` — bug fix
- `refactor(remote): …` — internal restructuring without behaviour change
- `test(gui): …` — tests only
- `docs(readme): …` — documentation only
- `chore(ci): …` — build / CI / housekeeping

Body paragraphs wrap at 72 characters. Reference the relevant phase
document (e.g. `docs/PHASE9D_PLAN_RESULTS.md`) when the commit is
part of a larger multi-step change.

## Project layout

```
src/jobdesk_app/
  app_*.py          # paths / logging
  cli.py, cli_prep.py
  config/           # Pydantic schemas for servers.yaml + result-extraction
  core/             # run, submit, transfer, manifest, parsers/, ...
  remote/           # SSH / SFTP / scheduler wrappers
  services/         # run_repository/, run_coordinator, run_monitor, ...
  gui/              # pages, dialogs, design/, i18n
tests/
  test_*.py         # unit + GUI tests
  integration/      # real-environment tests (skipped by default)
docs/
  PHASE*.md         # per-phase retrospective
  USER_GUIDE.md     # Chinese user walk-through
  TROUBLESHOOTING.md
  EXAMPLES.md
  CONFFLOW_*.md     # ConfFlow-specific notes
```

The 3-page GUI shell (`files` / `runs` / `settings`) maps onto
`gui/pages/`. The ConfFlow wizard lives in
`gui/dialogs/confflow_wizard_dialog.py` and is composed of three
`QWizardPage` subclasses (`_XyzPage` / `_CalcPage` / `_WorkflowPage`).

## Reporting security issues

Please **do not** open a public issue for security-sensitive reports.
Follow the process described in `SECURITY.md`.

## License

By submitting a contribution, you agree that it will be licensed under
the Apache License 2.0. See `LICENSE` for the full text.
