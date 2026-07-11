"""Built-in workflow example templates (Phase 2 onboarding).

This module is the *only* place in the codebase that knows the paths
and identifiers of the bundled templates. The toolbar "Examples" button
in :class:`WorkflowGraphEditor` opens a small :class:`QMenu` populated
from :data:`EXAMPLE_TEMPLATES`; selecting an entry calls
:meth:`ExamplesDrawer.selected.emit` with the matching ``template_id``.

How templates are loaded
------------------------

Templates live as JSON files under ``jobdesk_app.resources.workflow_examples``
and are read via :mod:`importlib.resources`. The same loader is used
from production code and from tests, so the shipped JSON does not have
to be on a filesystem path that's stable across ``python -m pytest`` /
``pyinstaller`` / editable-install scenarios.

Each entry in :data:`EXAMPLE_TEMPLATES` carries:

* ``id`` — a stable string (used as the menu role / signal payload)
* ``title`` — translated at display time via :func:`tr`
* ``description`` — one-line summary, also translated
* ``resource`` — :class:`importlib.resources.Traversable` (or a path
  compatible :class:`os.PathLike`) pointing at the JSON file; the
  drawer reads it on demand.

Round-trip safety
-----------------

The hand-written JSON was produced by mimicking the ``to_json`` shape
in :mod:`jobdesk_app.gui.nodegraph.serialization`. The fixtures are
verified by :mod:`tests.test_nodegraph.test_examples_drawer` so
loading then re-serialising yields the same node/edge/port graph.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMenu, QPushButton, QWidget

from jobdesk_app.gui.i18n import tr
from jobdesk_app.gui.nodegraph.serialization import from_json


@dataclass(frozen=True)
class ExampleTemplate:
    """A bundled workflow template bundled with the editor."""

    id: str
    title: str
    description: str
    resource_name: str  # filename inside jobdesk_app.resources.workflow_examples

    def load_graph(self):
        """Read this template's JSON and return a :class:`NodeGraph`."""
        from importlib import resources

        text = (
            resources.files("jobdesk_app.resources.workflow_examples")
            .joinpath(self.resource_name)
            .read_text(encoding="utf-8")
        )
        return from_json(json.loads(text))


@dataclass(frozen=True)
class _RawTemplate:
    id: str
    title_en: str
    description_en: str
    resource_name: str


_RAW: tuple[_RawTemplate, ...] = (
    _RawTemplate(
        id="linear_opt_freq",
        title_en="Linear OPT + FREQ",
        description_en="3-step backbone: optimize, then frequency analysis.",
        resource_name="linear_opt_freq.json",
    ),
    _RawTemplate(
        id="conformer_ensemble",
        title_en="Conformer ensemble + SP",
        description_en="Generate conformers, optimize the lowest, then single-point.",
        resource_name="conformer_ensemble.json",
    ),
    _RawTemplate(
        id="fan_out_gen_opt",
        title_en="Fan-out: two OPT branches",
        description_en="Same conformer ensemble feeds two parallel optimizations.",
        resource_name="fan_out_gen_opt.json",
    ),
    _RawTemplate(
        id="fan_in_refine",
        title_en="Fan-in: REFINE",
        description_en="Optimize a candidate, refine with the conformer ensemble.",
        resource_name="fan_in_refine.json",
    ),
)


def _build_examples() -> tuple[ExampleTemplate, ...]:
    """Materialise the templates using translated titles/descriptions."""
    out = []
    for raw in _RAW:
        out.append(
            ExampleTemplate(
                id=raw.id,
                title=tr(raw.title_en, "en"),
                description=tr(raw.description_en, "en"),
                resource_name=raw.resource_name,
            )
        )
    return tuple(out)


EXAMPLE_TEMPLATES: tuple[ExampleTemplate, ...] = _build_examples()


def get_example(template_id: str) -> ExampleTemplate:
    """Look up a template by its stable ``id``."""
    for tpl in EXAMPLE_TEMPLATES:
        if tpl.id == template_id:
            return tpl
    raise KeyError(f"no built-in example template with id={template_id!r}")


def all_example_ids() -> Iterable[str]:
    """Return the ids of every shipped example (handy for tests)."""
    return tuple(t.id for t in EXAMPLE_TEMPLATES)


class ExamplesDrawer(QPushButton):
    """Toolbar button that opens a menu of built-in workflow templates.

    Clicking the button pops up a :class:`QMenu` with one entry per
    :data:`EXAMPLE_TEMPLATES`. Selecting an entry emits
    :attr:`selected` with the entry's stable ``id``; the parent widget
    (typically :class:`WorkflowGraphEditor`) is responsible for the
    actual ``from_json`` + ``set_graph`` call.
    """

    selected = Signal(str)  # template_id

    def __init__(
        self,
        language: str = "en",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._language = language
        self.setText(tr("Examples", language))
        # We do not use setMenu / showMenu because we need to refresh
        # the entries every time (translations, language switch). A
        # lightweight popup built on demand is enough.
        self.clicked.connect(self._on_click)
        self._menu: QMenu | None = None

    def set_language(self, language: str) -> None:
        self._language = language
        self.setText(tr("Examples", language))
        # Recreate the menu lazily next click.
        self._menu = None

    # ── private helpers ─────────────────────────────────────────────

    def _on_click(self) -> None:
        menu = self._ensure_menu()
        # ``exec_`` runs its own event loop so we don't tie up the GUI
        # thread; the menu is rebuilt every open so language changes
        # show up without a parent-driven refresh.
        menu.exec_()

    def _ensure_menu(self) -> QMenu:
        menu = QMenu(self)
        for tpl in EXAMPLE_TEMPLATES:
            label = tr(tpl.title_en, self._language)
            action = menu.addAction(label)
            action.setStatusTip(tr(tpl.description_en, self._language))
            action.triggered.connect(
                lambda _checked=False, tid=tpl.id: self.selected.emit(tid)
            )
        self._menu = menu
        return menu


__all__ = [
    "EXAMPLE_TEMPLATES",
    "ExampleTemplate",
    "ExamplesDrawer",
    "all_example_ids",
    "get_example",
]
