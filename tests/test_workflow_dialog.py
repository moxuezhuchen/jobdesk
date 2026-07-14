"""Tests for ``WorkflowBuilderDialog`` (Phase 2.0)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from jobdesk_app.core.workflow_spec import WorkflowSpec  # noqa: E402
from jobdesk_app.gui.dialogs.workflow_builder_dialog import WorkflowBuilderDialog  # noqa: E402
from jobdesk_app.gui.nodegraph.editor import WorkflowGraphEditor  # noqa: E402
from jobdesk_app.services.method_presets import MethodPresetStore  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_dialog_embeds_editor(qapp):
    dlg = WorkflowBuilderDialog(language="en", preset_store=MethodPresetStore())
    assert dlg.editor is not None
    assert dlg.editor.is_empty()
    dlg.close()
    dlg.deleteLater()


def test_dialog_loads_initial_spec(qapp):
    spec = WorkflowSpec.from_form(
        work_dir_name="x",
        program="gaussian",
        method="B3LYP",
        basis="6-31G(d)",
        charge=0,
        multiplicity=1,
        nproc=4,
        memory_mb=4096,
    )
    dlg = WorkflowBuilderDialog(language="en", preset_store=MethodPresetStore(), initial_spec=spec)
    assert not dlg.editor.is_empty()
    dlg.close()
    dlg.deleteLater()


def test_dialog_accept_returns_spec(qapp):
    spec = WorkflowSpec.from_form(
        work_dir_name="x",
        program="gaussian",
        method="B3LYP",
        basis="6-31G(d)",
        charge=0,
        multiplicity=1,
        nproc=4,
        memory_mb=4096,
    )
    dlg = WorkflowBuilderDialog(language="en", preset_store=MethodPresetStore(), initial_spec=spec)
    dlg._on_accept()
    assert dlg.result_spec() is spec
    dlg.close()
    dlg.deleteLater()


def test_dialog_accept_preserves_rich_step_yaml(qapp):
    """The legacy graph dialog must not truncate a loaded workflow."""
    text = """\
global:
  cores_per_task: 8
  total_memory: 16GB
steps:
  - name: conformers
    type: confgen
    params:
      chains: [1-2-3-4]
      angle_step: 90
    inputs: []
  - name: final_sp
    type: calc
    params:
      iprog: orca
      itask: sp
      keyword: DLPNO-CCSD(T) cc-pVTZ
    inputs: [conformers]
"""
    spec = WorkflowSpec.from_yaml(text)
    dlg = WorkflowBuilderDialog(language="en", preset_store=MethodPresetStore(), initial_spec=spec)
    try:
        dlg._on_accept()
        result = dlg.result_spec()
        assert result is not None
        rendered = result.to_yaml()
        assert "chains:" in rendered
        assert "angle_step: 90" in rendered
        assert "inputs:" in rendered
        assert "DLPNO-CCSD(T) cc-pVTZ" in rendered
    finally:
        dlg.close()
        dlg.deleteLater()


# ─────────────────────────────────────────────────────────────────
# Regression: opening a built-in preset in the builder must produce
# a validating graph AND keep the onboarding "Quick start" button
# reachable.
#
# Pre-fix bug: ``_build_linear_graph`` wired the last step to
# ``output.in`` even though ``OUTPUT`` ships with no input ports.
# That produced an ``UNKNOWN_PORT`` validation error every time the
# user opened an existing preset in the modal builder, and the
# status pill rendered "1 error(s) — see properties panel" on first
# paint. The user-facing symptom: "Open in builder" → empty-feeling
# canvas + a red error pill, plus the empty-canvas onboarding card
# (and its Quick-start button) was suppressed because the canvas
# wasn't technically empty. Fix: stop wiring OUTPUT altogether so
# the loaded graph round-trips cleanly, mirroring the bundled JSON
# templates that leave OUTPUT as a sentinel.
# ─────────────────────────────────────────────────────────────────


def test_dialog_loads_initial_spec_has_no_validation_errors(qapp):
    """Built-in presets must round-trip into a fully validating graph."""
    store = MethodPresetStore()
    issues_per_preset = {}
    for preset in store.list_presets():
        if preset.source != "builtin":
            continue
        spec = store.load(preset.name)
        dlg = WorkflowBuilderDialog(
            language="en",
            preset_store=store,
            initial_spec=spec,
        )
        try:
            issues = dlg.editor.graph().validate()
            issues_per_preset[preset.name] = [i for i in issues if i.severity == "error"]
        finally:
            dlg.close()
            dlg.deleteLater()
    bad = {name: [i.message for i in issues] for name, issues in issues_per_preset.items() if issues}
    assert not bad, (
        "Loading a built-in preset in the builder must produce a "
        "validating graph (no errors). Pre-fix bug wired "
        "'last_step → output.in', which always raised UNKNOWN_PORT. "
        f"Failing presets: {bad}"
    )


def test_dialog_loads_initial_spec_keeps_quick_start_button_hidden(qapp):
    """When the canvas is non-empty (preset loaded), the empty-state
    onboarding card stays hidden — but the editor can still be reset
    via the toolbar Examples menu.

    This test pins down the visibility contract so a future change
    to ``_refresh_onboarding_visibility`` can't silently regress the
    user experience: "Open in builder" → canvas populated + 0 errors,
    so the Quick-start overlay correctly hides (it's only meant for
    the empty-canvas state). The toolbar Examples drawer remains the
    canonical way to load a template into a populated canvas.
    """
    spec = WorkflowSpec.from_form(
        work_dir_name="x", program="gaussian", method="B3LYP", basis="6-31G(d)",
        charge=0, multiplicity=1, nproc=4, memory_mb=4096,
    )
    dlg = WorkflowBuilderDialog(
        language="en",
        preset_store=MethodPresetStore(),
        initial_spec=spec,
    )
    try:
        assert not dlg.editor.is_empty()
        # The card is bound to the empty-canvas state by design. After
        # opening a preset it hides so the user focuses on the graph.
        assert not dlg.editor.onboarding_card().isVisible()
        # The toolbar Examples drawer is always available as the
        # recovery path — verify it's wired to load a template.
        assert dlg.editor._examples_btn is not None
    finally:
        dlg.close()
        dlg.deleteLater()


def test_dialog_quick_start_button_loads_template_when_canvas_empty(qapp, monkeypatch, tmp_path):
    """Sanity: the onboarding card's Quick-start button still loads
    ``linear_opt_freq`` when the canvas is empty. This is the path
    the user takes after clicking "New workflow".
    """
    monkeypatch.setattr(
        "jobdesk_app.services.gui_settings.get_app_data_dir",
        lambda: tmp_path,
    )
    settings = tmp_path / "gui_settings.yaml"
    settings.write_text("show_onboarding: true\n", encoding="utf-8")
    from jobdesk_app.services.gui_settings import GuiSettingsStore

    settings_store = GuiSettingsStore(path=settings)
    editor = WorkflowGraphEditor(language="en", settings_store=settings_store)
    try:
        assert editor.is_empty()
        card = editor.onboarding_card()
        assert card is not None
        editor.show()
        editor.resize(960, 640)
        qapp.processEvents()
        editor._refresh_onboarding_visibility()
        assert card.isVisible(), (
            "Onboarding card must be visible on a fresh editor so the Quick-start button is reachable."
        )
        btn = card._quick_start_btn
        assert btn.text(), "Quick-start button must have a label"
        btn.click()
        # After click, the graph must be populated and validate clean.
        assert not editor.is_empty()
        errors = [i for i in editor.graph().validate() if i.severity == "error"]
        assert not errors, (
            f"Quick-start template load must not introduce validation errors. Found: {[i.message for i in errors]}"
        )
    finally:
        editor.close()
        editor.deleteLater()


# ─────────────────────────────────────────────────────────────────
# Review-round 3 follow-up: clicking Quick-start used to leave the
# canvas visually blank even though the model had nodes. ``set_graph``
# only mutated the model + rebuilt the registry; the QGraphicsView's
# scroll position stayed wherever it was on the empty canvas (the
# centre of the 8000×6000 sceneRect), which is far from the new
# nodes at (40, 80)–(700, 80). The fix calls ``fit_to_items`` so the
# view immediately re-centres + scales to show the new graph.
# ─────────────────────────────────────────────────────────────────


def test_quick_start_repositions_view_to_show_new_nodes(qapp, monkeypatch, tmp_path):
    """Click Quick-start on an empty editor and assert the view is
    scrolled/zoomed so the new nodes land inside the viewport. The
    pre-fix bug was that the view's transform and scroll position
    stayed at the empty-canvas defaults (identity + scene-rect
    centre), so even though the scene contained 4 nodes + 2 edges,
    the user saw a blank canvas.
    """
    monkeypatch.setattr(
        "jobdesk_app.services.gui_settings.get_app_data_dir",
        lambda: tmp_path,
    )
    settings = tmp_path / "gui_settings.yaml"
    settings.write_text("show_onboarding: true\n", encoding="utf-8")
    from jobdesk_app.services.gui_settings import GuiSettingsStore

    settings_store = GuiSettingsStore(path=settings)
    editor = WorkflowGraphEditor(language="en", settings_store=settings_store)
    try:
        editor.show()
        editor.resize(960, 640)
        qapp.processEvents()
        # Capture pre-click state.
        view = editor.view()
        transform_before = view.transform().m11()
        scroll_before = (
            view.horizontalScrollBar().value(),
            view.verticalScrollBar().value(),
        )
        # Sanity: the empty canvas had no items.
        assert editor.is_empty()

        # Click Quick-start.
        editor.onboarding_card()._quick_start_btn.click()
        qapp.processEvents()

        # Model is populated.
        assert not editor.is_empty()
        nodes = list(editor.graph().nodes.values())
        assert len(nodes) == 4

        # Viewport must contain at least one of the new node items
        # AFTER the click. Pre-fix the viewport was scrolled to
        # (~1800, ~700), which is way past the rightmost node at
        # x=700, so ``items()`` would return an empty list inside
        # the viewport rect.
        viewport_rect = view.viewport().rect()
        top_left = view.mapToScene(viewport_rect.topLeft())
        bottom_right = view.mapToScene(viewport_rect.bottomRight())
        visible_x_range = (top_left.x(), bottom_right.x())
        visible_y_range = (top_left.y(), bottom_right.y())
        node_xs = [n.position[0] for n in nodes]
        node_ys = [n.position[1] for n in nodes]
        assert min(node_xs) <= max(visible_x_range) and max(node_xs) >= min(visible_x_range), (
            f"After Quick-start the view's x-range {visible_x_range} must "
            f"overlap the node x-positions {node_xs}. Pre-fix the view "
            f"stayed at the empty-canvas scroll ({scroll_before}) and the "
            f"new nodes fell outside the viewport."
        )
        assert min(node_ys) <= max(visible_y_range) and max(node_ys) >= min(visible_y_range), (
            f"After Quick-start the view's y-range {visible_y_range} must "
            f"overlap the node y-positions {node_ys}."
        )

        # And ``fit_to_items`` was called, so the transform zoomed
        # out from 1.0× (or stayed at 1.0× if everything already fit).
        # The important invariant is that *something* changed.
        transform_after = view.transform().m11()
        scroll_after = (
            view.horizontalScrollBar().value(),
            view.verticalScrollBar().value(),
        )
        assert (transform_after, scroll_after) != (transform_before, scroll_before), (
            "set_graph must reposition the view when populating an "
            "empty canvas, otherwise the user sees a blank canvas "
            "after Quick-start even though the model is populated."
        )
    finally:
        editor.close()
        editor.deleteLater()


def test_set_graph_repositions_view_when_populating_empty_canvas(qapp):
    """Generalise the Quick-start fix: any wholesale template load
    onto an empty canvas must re-fit the view. Covers the toolbar
    Examples drawer + the toolbar Load button paths.
    """
    editor = WorkflowGraphEditor(language="en")
    try:
        editor.show()
        editor.resize(960, 640)
        qapp.processEvents()
        assert editor.is_empty()
        view = editor.view()
        scroll_before = (
            view.horizontalScrollBar().value(),
            view.verticalScrollBar().value(),
        )
        # Load a built-in template via the public Examples API.
        from jobdesk_app.gui.nodegraph.examples_drawer import get_example
        graph = get_example("linear_opt_freq").load_graph()
        editor.set_graph(graph)
        qapp.processEvents()
        # View must have re-fitted — its scroll position should differ.
        scroll_after = (
            view.horizontalScrollBar().value(),
            view.verticalScrollBar().value(),
        )
        assert scroll_after != scroll_before, (
            "set_graph on an empty canvas must re-fit the view so "
            "the user can see the freshly loaded nodes."
        )
    finally:
        editor.close()
        editor.deleteLater()


# ─────────────────────────────────────────────────────────────────
# Review-round 3 follow-up: keyboard-only users starting from the
# library search box had to press Tab 25 times to reach the
# onboarding card's Quick-start button — effectively a focus trap.
# The fix installs a Tab shortcut on the search box that jumps to
# the Quick-start button when the card is visible, and removes it
# when the card hides so the standard tab order resumes.
# ─────────────────────────────────────────────────────────────────


def test_search_box_tab_jumps_to_quick_start_when_card_visible(qapp, monkeypatch, tmp_path):
    """Pressing Tab on the search box while the onboarding card is
    visible must land focus on the Quick-start button. Pre-fix the
    chain walked through every library button + toolbar first.
    """
    monkeypatch.setattr(
        "jobdesk_app.services.gui_settings.get_app_data_dir",
        lambda: tmp_path,
    )
    settings = tmp_path / "gui_settings.yaml"
    settings.write_text("show_onboarding: true\n", encoding="utf-8")
    from jobdesk_app.services.gui_settings import GuiSettingsStore

    settings_store = GuiSettingsStore(path=settings)
    editor = WorkflowGraphEditor(language="en", settings_store=settings_store)
    try:
        editor.show()
        editor.resize(960, 640)
        qapp.processEvents()
        card = editor.onboarding_card()
        assert card.isVisible(), "Onboarding card must be visible for this test"
        quick_start = card._quick_start_btn
        search = editor._library._search_box
        search.setFocus()
        qapp.processEvents()
        assert qapp.focusWidget() is search
        # Single Tab keypress → focus jumps to the Quick-start button.
        tab_event = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Tab,
            Qt.KeyboardModifier.NoModifier,
        )
        search.keyPressEvent(tab_event)
        qapp.processEvents()
        assert qapp.focusWidget() is quick_start, (
            "Tab from the search box must jump to the Quick-start "
            "button while the onboarding card is visible. Pre-fix "
            "the focus chain walked through ~25 widgets first."
        )
    finally:
        editor.close()
        editor.deleteLater()


def test_search_box_tab_shortcut_disabled_when_card_hidden(qapp):
    """Once the canvas is populated (card hidden), the search box's
    Tab shortcut must be cleared so the standard focus chain
    resumes. Otherwise users would land on a hidden button.
    """
    editor = WorkflowGraphEditor(language="en")
    try:
        editor.show()
        editor.resize(960, 640)
        qapp.processEvents()
        # Confirm card is visible initially.
        assert editor.onboarding_card().isVisible()
        # Populate the canvas via the toolbar Examples API.
        from jobdesk_app.gui.nodegraph.examples_drawer import get_example
        editor.set_graph(get_example("linear_opt_freq").load_graph())
        qapp.processEvents()
        # Card hides when there are nodes.
        assert not editor.onboarding_card().isVisible()
        # Send Tab from the search box — focus must NOT land on the
        # (now hidden) Quick-start button.
        search = editor._library._search_box
        search.setFocus()
        qapp.processEvents()
        tab_event = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Tab,
            Qt.KeyboardModifier.NoModifier,
        )
        search.keyPressEvent(tab_event)
        qapp.processEvents()
        assert qapp.focusWidget() is not editor.onboarding_card()._quick_start_btn, (
            "Tab shortcut must be cleared once the card is hidden, "
            "otherwise focus can land on an invisible widget."
        )
    finally:
        editor.close()
        editor.deleteLater()
