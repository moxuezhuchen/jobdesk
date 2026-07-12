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
from ...gui.nodegraph.model import Edge, NodeGraph, NodeKind, default_node
from ...services.method_presets import MethodPresetStore
from ..i18n import tr

_STEP_TO_KIND: dict[str, NodeKind] = {
    "confgen": NodeKind.CONF_GEN,
    "preopt": NodeKind.PRE_OPT,
    "opt": NodeKind.OPT,
    "sp": NodeKind.SINGLE_POINT,
    "freq": NodeKind.FREQUENCY,
    "ts": NodeKind.TS,
    "refine": NodeKind.REFINE,
}


def _build_linear_graph(spec: WorkflowSpec) -> NodeGraph:
    """Reconstruct a linear XYZ -> steps -> OUTPUT chain from a ``WorkflowSpec``.

    Conservative round-trip — fan-in / fan-out and per-step params are
    dropped. This is enough to make built-in presets editable through
    the dialog without losing the chain shape. The plan documents the
    round-trip limitation under "WorkflowSpec-back-to-NodeGraph projection"
    (Open Question 1).
    """
    graph = NodeGraph()
    xyz = default_node(NodeKind.XYZ_FILE, position=(40.0, 80.0))
    graph.add_node(xyz)

    form = spec.to_form() if spec is not None else {}
    steps = list(form.get("steps") or [])

    prev_id = xyz.id
    prev_out_port = "out"
    for i, step_token in enumerate(steps):
        kind = _STEP_TO_KIND.get(str(step_token).lower(), NodeKind.OPT)
        node = default_node(kind, position=(40.0 + 240 * (i + 1), 80.0))
        graph.add_node(node)
        # Wire prev -> node.in
        graph.add_edge(Edge(
            id=Edge.new_id(),
            src_node=prev_id,
            src_port=prev_out_port,
            dst_node=node.id,
            dst_port="in",
        ))
        # Move forward; the next step consumes whatever port the
        # previous one produced (refine-style wiring).
        prev_id = node.id
        prev_out_port = "out"

    output = default_node(NodeKind.OUTPUT, position=(40.0 + 240 * (len(steps) + 1), 80.0))
    graph.add_node(output)
    # Last step -> Output.in (catch-all)
    graph.add_edge(Edge(
        id=Edge.new_id(),
        src_node=prev_id,
        src_port=prev_out_port,
        dst_node=output.id,
        dst_port="in",
    ))
    return graph


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
        self._initial_spec: Optional[WorkflowSpec] = initial_spec
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
        graph = _build_linear_graph(spec)
        self.editor.set_graph(graph)

    def _on_accept(self) -> None:
        try:
            if not self.editor.is_empty():
                from ...gui.nodegraph.spec_bridge import to_workflow_spec
                payload = to_workflow_spec(self.editor.graph())
                self._result_spec = WorkflowSpec(
                    global_config=payload.spec.global_config
                )
            else:
                # Empty editor -> keep the initial spec (conservative).
                self._result_spec = self._initial_spec
        except Exception:
            # Bridge failure: don't lose the user's prior work.
            self._result_spec = self._initial_spec
        self.accept()

    def result_spec(self) -> WorkflowSpec | None:
        return self._result_spec


__all__ = ["WorkflowBuilderDialog"]
