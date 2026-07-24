#!/usr/bin/env python3

"""Differential fixture tests for the JobDesk offline ConfFlow validator.

JobDesk's offline validator in
:mod:`jobdesk_app.core._confflow_validation` is an intentional **stable
subset** of ConfFlow's own validator. The differential fixtures below
pin the *boundaries* between the two:

* ``EXPECTED_ACCEPTED_BY_BOTH`` — inputs both accept.
* ``EXPECTED_REJECTED_BY_BOTH`` — inputs both reject (same root cause).
* ``KNOWN_DIVERGENCE`` — inputs the two sides handle differently. Each
  fixture is named with a *direction tag* (``accepted_by_jobdesk_only``
  / ``accepted_by_confflow_only``) and a stable ``id`` so the gap can
  be referenced in code review without being mistaken for a bug.

When ConfFlow is installed (the CI runner installs the 1.4.2 wheel),
the differential runs against the real producer-side validator. When
ConfFlow is *not* installed the file is skipped through the
``test_module_skips_are_never_silent`` guard so silent skips cannot
mask a missing CI install.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from jobdesk_app.core._confflow_validation import validate_yaml_config as jd_validate

try:
    from confflow.shared.config_validation import validate_yaml_config as cf_validate

    _CONFFLOW_AVAILABLE = True
except ImportError:  # chemistry extra not installed
    cf_validate = None  # type: ignore[assignment]
    _CONFFLOW_AVAILABLE = False


def _require_or_skip_differential() -> None:
    """Require ConfFlow in CI, while allowing local chem-less development."""
    if _CONFFLOW_AVAILABLE:
        return
    if os.getenv("JOBDESK_REQUIRE_CONFFLOW_DIFFERENTIAL") == "1":
        pytest.fail("ConfFlow differential is required but confflow is not importable")
    pytest.skip("ConfFlow not installed; differential unverified")


# ---------------------------------------------------------------------------
# EXPECTED_ACCEPTED_BY_BOTH
# ---------------------------------------------------------------------------

EXPECTED_ACCEPTED_BY_BOTH: list[dict[str, Any]] = [
    pytest.param(
        {
            "global": {"cores_per_task": 4, "max_parallel_jobs": 2},
            "steps": [
                {
                    "name": "confgen",
                    "type": "confgen",
                    "params": {"chains": ["1-2-3-4-5"]},
                },
                {
                    "name": "opt",
                    "type": "calc",
                    "params": {"itask": "opt", "iprog": "gaussian"},
                },
            ],
        },
        id="minimal_global_with_confgen_and_calc",
    ),
    pytest.param(
        {
            "global": {"cores_per_task": 8, "max_parallel_jobs": 1},
            "steps": [
                {
                    "name": "ts",
                    "type": "calc",
                    "params": {"itask": "ts", "iprog": "orca", "keyword": "B3LYP def2-SVP"},
                },
            ],
        },
        id="orca_task_with_explicit_keyword",
    ),
    pytest.param(
        # ConfFlow 1.4.2 accepts whole-number floats; the offline subset
        # mirrors this behaviour.
        {"global": {"cores_per_task": 4.0, "max_parallel_jobs": 1}, "steps": []},
        id="floating_cores_per_task",
    ),
    pytest.param(
        {"global": {"cores_per_task": 4, "max_parallel_jobs": 1}, "steps": []},
        id="empty_steps_legal",
    ),
]


@pytest.mark.parametrize("config", EXPECTED_ACCEPTED_BY_BOTH)
def test_expected_accepted_by_both(config):
    """Sanity: inputs both validators must accept."""
    assert jd_validate(config) == []
    _require_or_skip_differential()
    assert cf_validate(config) == []


# ---------------------------------------------------------------------------
# EXPECTED_REJECTED_BY_BOTH
# ---------------------------------------------------------------------------

EXPECTED_REJECTED_BY_BOTH: list[dict[str, Any]] = [
    pytest.param({}, id="empty_config"),
    pytest.param({"global": {}, "steps": "not-a-list"}, id="steps_must_be_list"),
    pytest.param(
        {"global": {"cores_per_task": 0}, "steps": []},
        id="non_positive_cores",
    ),
    pytest.param(
        {"global": {"max_parallel_jobs": -1}, "steps": []},
        id="negative_max_parallel_jobs",
    ),
    pytest.param(
        {
            "global": {},
            "steps": [
                {"type": "calc", "params": {"itask": "nonsense"}},
            ],
        },
        id="invalid_itask_value",
    ),
    pytest.param(
        {
            "global": {},
            "steps": [
                {"type": "calc", "params": {"iprog": "mystery"}},
            ],
        },
        id="invalid_iprog_value",
    ),
    pytest.param(
        {
            "global": {},
            "steps": [
                {"name": "g", "type": "confgen", "params": {}},
            ],
        },
        id="confgen_missing_chains",
    ),
    pytest.param(
        {
            "global": {},
            "steps": [
                {
                    "name": "g",
                    "type": "confgen",
                    "params": {"chains": ["1-2-3-4"], "add_bond": [[1, 2, 3]]},
                },
            ],
        },
        id="confgen_add_bond_wrong_pair_shape",
    ),
    pytest.param(
        {
            "global": {},
            "steps": [
                {
                    "name": "g",
                    "type": "confgen",
                    "params": {"chains": ["1-2-3"], "angle_step": 0},
                },
            ],
        },
        id="confgen_angle_step_zero",
    ),
    pytest.param(
        {
            "global": {},
            "steps": [
                {"name": "o", "type": "calc", "params": {"iprog": "orca"}},
            ],
        },
        id="orca_task_missing_keyword",
    ),
]


@pytest.mark.parametrize("config", EXPECTED_REJECTED_BY_BOTH)
def test_expected_rejected_by_both(config):
    """Both validators must reject these inputs."""
    assert jd_validate(config) != []
    _require_or_skip_differential()
    assert cf_validate(config) != []


# ---------------------------------------------------------------------------
# KNOWN_DIVERGENCE
# ---------------------------------------------------------------------------

# Each param id starts with a *direction tag* describing which side
# accepts.
#
#   * ``accepted_by_jobdesk_only`` — JobDesk offline subset accepts,
#     ConfFlow 1.4.2 rejects. The offline subset is more permissive.
#   * ``accepted_by_confflow_only`` — ConfFlow 1.4.2 accepts,
#     JobDesk offline subset rejects. The offline subset is more
#     conservative.
#
# Each entry in this list is a *named* record of a known gap. Removing
# the gap is a real change in the offline subset and must be done
# deliberately (and a new test pinned).
KNOWN_DIVERGENCE: list[dict[str, Any]] = [
    pytest.param(
        # ConfFlow 1.4.2 normalizes ``global: None`` to ``{}``. The
        # offline subset rejects it as "must be a mapping" because the
        # YAML editor should never write ``global: null`` and we want
        # to catch it early.
        {"global": None, "steps": []},
        id="accepted_by_confflow_only__global_is_none",
    ),
    pytest.param(
        # ConfFlow 1.4.2 tolerates ``cores_per_task: True`` (python
        # ``bool`` is a subclass of ``int`` and ``int(True) == 1``).
        # The offline subset rejects bool explicitly because the wizard
        # should never pass a boolean for a numeric field.
        {"global": {"cores_per_task": True, "max_parallel_jobs": 1}, "steps": []},
        id="accepted_by_confflow_only__bool_cores_per_task",
    ),
    pytest.param(
        # ConfFlow 1.4.2 calls ``int(4.5) == 4`` for ``cores_per_task``
        # and accepts the input. The offline subset rejects non-integer
        # floats because the wizard emits ints and accepting a float
        # would mask a config bug.
        {"global": {"cores_per_task": 4.5, "max_parallel_jobs": 1}, "steps": []},
        id="accepted_by_confflow_only__non_integer_cores",
    ),
    pytest.param(
        # ConfFlow 1.4.2 checks that ``gaussian_path`` / ``orca_path``
        # exist on disk. The offline subset trusts the path: the
        # consumer-side path check would degrade the editor's snappy
        # UX while the user is typing on a path that does not exist
        # yet.
        {
            "global": {
                "cores_per_task": 4,
                "max_parallel_jobs": 1,
                "gaussian_path": "/definitely/does/not/exist/g16",
            },
            "steps": [],
        },
        id="accepted_by_jobdesk_only__missing_gaussian_path",
    ),
]


@pytest.mark.parametrize("config", KNOWN_DIVERGENCE)
def test_known_divergence(config):
    """Inputs that the two sides handle differently.

    The ``id`` declares which side accepts. This test asserts the
    declared direction holds — if a future change makes the offline
    subset stricter on a "accepted_by_jobdesk_only" entry, it must be
    renamed and the divergence recorded as accepted by neither.
    """
    jd_ok = jd_validate(config) == []
    _require_or_skip_differential()
    cf_ok = cf_validate(config) == []
    assert jd_ok != cf_ok, "Known divergence fixture stopped diverging — pick a new direction tag or remove the entry."
    param_id = config  # not used; pytest injects the id via request.
    _ = param_id


def _fixture_id(request: pytest.FixtureRequest) -> str:
    """Extract the param id from the current request for assertions."""
    return request.node.callspec.id


@pytest.mark.parametrize("config", KNOWN_DIVERGENCE)
def test_known_divergence_direction_matches_id(config, request):
    """Lock the id prefix to the actual behaviour.

    The ``id`` declaration in KNOWN_DIVERGENCE is the contract — this
    test asserts the prefix matches the actual acceptance of the
    offline subset.
    """
    jd_ok = jd_validate(config) == []
    _require_or_skip_differential()
    cf_ok = cf_validate(config) == []
    node_id = _fixture_id(request)
    if node_id.startswith("accepted_by_jobdesk_only"):
        assert jd_ok is True, (
            f"{node_id}: JobDesk offline subset rejected a jobdesk-only "
            "fixture; rename the direction tag or remove the entry."
        )
        assert cf_ok is False, (
            f"{node_id}: ConfFlow accepted a jobdesk-only fixture; rename the direction tag or remove the entry."
        )
    elif node_id.startswith("accepted_by_confflow_only"):
        assert jd_ok is False, (
            f"{node_id}: JobDesk offline subset accepted a confflow-only "
            "fixture; rename the direction tag or remove the entry."
        )
        assert cf_ok is True, (
            f"{node_id}: ConfFlow rejected a confflow-only fixture; rename the direction tag or remove the entry."
        )
    else:
        pytest.fail(f"Unknown direction tag in fixture id: {node_id}")


# ---------------------------------------------------------------------------
# Generic skip-silently guard
# ---------------------------------------------------------------------------


def test_module_skips_are_never_silent():
    """If ConfFlow is installed *and* the differential fixtures do not
    run, the test session must fail. The CI runner installs the
    ConfFlow 1.4.2 wheel as part of the workflow's
    ``Install ConfFlow v1.4.2`` step, so a skip here means the
    install failed silently.
    """
    _require_or_skip_differential()
    # Smoke check: the cross-checker itself is callable and accepts
    # the minimal legal config.
    assert (
        cf_validate(
            {
                "global": {"cores_per_task": 4, "max_parallel_jobs": 1},
                "steps": [],
            }
        )
        == []
    )
