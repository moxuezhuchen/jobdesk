"""Node-graph editor for constructing computational-chemistry workflows.

This package implements a visual DAG editor built on top of Qt's
``QGraphicsView``. It replaces the previous "Build input file" / "Build
workflow" tabs on the Submit page so that the user can drag-and-drop
nodes, wire them together, and see the resulting ``workflow.yaml``
preview in one place.

Architecture
------------

```
       ┌────────────────────────┐
       │  WorkflowGraphEditor   │   ← top-level widget embedded in SubmitPage
       └─────────┬──────────────┘
                 │ owns
       ┌─────────▼──────────────┐
       │ NodeLibraryPanel       │  left side: drag-source
       │ GraphCanvas (View+Scene)│  center: drag/connect/edit
       │ PropertiesPanel        │  right side: selected-node params
       └────────────────────────┘

       NodeGraph  ◄── serialization ──►  WorkflowSpec (confflow GlobalConfigModel)
```

The data model is independent of the visualization so that it can be
unit-tested without a running ``QApplication``.

Why this exists
---------------

The legacy wizard scattered the same method/basis fields across three
widgets and buried the step list as five unlabeled checkboxes. This
package collapses that surface into one editable picture: each ConfFlow
step is a single node type with a single property panel, and the
topology *is* the workflow.
"""
from __future__ import annotations

from jobdesk_app.gui.nodegraph.spec_bridge import (
    WorkflowGraphPayload,
    WorkflowSpecError,
    from_workflow_spec,
    to_workflow_spec,
)

__all__: list[str] = [
    "WorkflowGraphPayload",
    "WorkflowSpecError",
    "from_workflow_spec",
    "to_workflow_spec",
]
