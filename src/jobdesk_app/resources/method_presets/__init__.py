"""Bundled confflow method presets.

Each ``*.yaml`` file is loaded as a :class:`WorkflowSpec` by
:mod:`jobdesk_app.services.method_presets`. Subdirectories group presets
by program (``gaussian/``, ``orca/``, ``conflow/``); they are advisory —
the file stem is the preset name. Do not put code in this directory
beyond the empty namespace package.

YAML schema
-----------

The shape below matches :meth:`WorkflowSpec.from_form` round-trip output
(``{work_dir, calc: {program, method, basis, charge, multiplicity,
nproc, memory_mb, steps}, plus the flat global fields}``). Do not nest
under ``global:`` — that schema is consumed by confflow's own runtime,
not by :class:`WorkflowSpec`.
"""
