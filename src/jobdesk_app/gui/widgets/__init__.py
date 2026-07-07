"""Reusable QWidget classes extracted from existing dialog/page bodies.

These widgets are pure refactors of bodies that previously lived inside
``InputBuilderDialog`` (QDialog) and ``ConfFlowWizard`` (QWizard).  They drop
the dialog/wizard superclass so they can be embedded anywhere — most notably
the upcoming ``SubmitPage`` (Phase 14B).

The source classes remain untouched; tests continue to exercise them. The
widgets here are alternative entry points that share the same form/validation
logic and i18n keys.
"""