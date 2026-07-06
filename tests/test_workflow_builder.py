"""Stage 4 — workflow builder form-state round-trip and validation tests."""

from __future__ import annotations

import pytest

from jobdesk_app.workflow.builder import (
    BuilderError,
    FormState,
    StepState,
    ValidationError,
    build_mapping,
    default_form_state,
    form_state_to_yaml,
    validate_runtime,
    validate_state,
    yaml_to_form_state,
)
from jobdesk_app.workflow.config.models import WorkflowConfig


# ---------------------------------------------------------------------------
# Default state
# ---------------------------------------------------------------------------


class TestDefaultState:
    def test_default_has_global_options(self):
        state = default_form_state()
        assert "charge" in state.global_options
        assert "iprog" in state.global_options
        assert state.steps == []

    def test_clone_is_independent(self):
        a = default_form_state()
        b = a.clone()
        b.global_options["charge"] = 99
        assert a.global_options["charge"] != 99
        b.steps.append(StepState(type="calc", params={"name": "x", "iprog": "orca", "itask": "sp", "keyword": "k"}))
        assert a.steps == []


# ---------------------------------------------------------------------------
# Form state <-> YAML round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_round_trip_minimal(self):
        state = default_form_state()
        state.steps.append(
            StepState(
                type="calc",
                enabled=True,
                params={"name": "opt", "iprog": "orca", "itask": "opt_freq", "keyword": "B3LYP def2-SVP", "cores_per_task": 8},
            )
        )
        yaml_text = form_state_to_yaml(state)
        parsed = yaml_to_form_state(yaml_text)
        assert len(parsed.steps) == 1
        assert parsed.steps[0].type == "calc"
        assert parsed.steps[0].params["keyword"] == "B3LYP def2-SVP"

    def test_round_trip_with_confgen(self):
        state = default_form_state()
        state.global_options["charge"] = 0
        state.steps.append(StepState(type="confgen", params={"name": "cg", "engine": "rdkit", "angle_step": 30.0}))
        state.steps.append(
            StepState(
                type="calc",
                params={"name": "sp", "iprog": "g16", "itask": "sp", "keyword": "B3LYP/6-31G* sp"},
            )
        )
        parsed = yaml_to_form_state(form_state_to_yaml(state))
        assert len(parsed.steps) == 2
        assert parsed.steps[0].type == "confgen"
        assert parsed.steps[1].type == "calc"

    def test_round_trip_preserves_disabled_steps(self):
        state = default_form_state()
        state.steps.append(
            StepState(type="calc", enabled=False, params={"name": "x", "iprog": "orca", "itask": "sp", "keyword": "k"})
        )
        parsed = yaml_to_form_state(form_state_to_yaml(state))
        assert parsed.steps[0].enabled is False

    def test_alias_gen_to_confgen(self):
        text = (
            "global:\n"
            "  iprog: orca\n"
            "steps:\n"
            "  - name: cg\n"
            "    type: gen\n"
            "    enabled: true\n"
            "    params:\n"
            "      engine: rdkit\n"
        )
        state = yaml_to_form_state(text)
        assert state.steps[0].type == "confgen"

    def test_alias_task_to_calc(self):
        text = (
            "global:\n  iprog: orca\n"
            "steps:\n  - name: x\n    type: task\n"
            "    params:\n      iprog: orca\n      itask: sp\n      keyword: k\n"
        )
        state = yaml_to_form_state(text)
        assert state.steps[0].type == "calc"

    def test_unknown_step_type_raises(self):
        text = "global:\n  iprog: orca\nsteps:\n  - {type: bogus, params: {}}\n"
        with pytest.raises(BuilderError):
            yaml_to_form_state(text)

    def test_round_trip_passes_runtime_validation(self):
        state = default_form_state()
        state.global_options["charge"] = 0
        state.global_options["keyword"] = "B3LYP def2-SVP"
        state.steps.append(
            StepState(
                type="calc",
                params={"name": "opt", "iprog": "orca", "itask": "opt_freq",
                        "keyword": "B3LYP def2-SVP opt freq"},
            )
        )
        wf = validate_runtime(state)
        assert isinstance(wf, WorkflowConfig)
        assert len(wf.steps) == 1


# ---------------------------------------------------------------------------
# build_mapping — global and step field handling
# ---------------------------------------------------------------------------


class TestBuildMapping:
    def test_global_drops_blank_keys(self):
        state = default_form_state()
        state.global_options["sandbox_root"] = ""
        state.global_options["allowed_executables"] = []
        mapping = build_mapping(state)
        assert "sandbox_root" not in mapping["global"]
        assert "allowed_executables" not in mapping["global"]

    def test_global_keeps_set_keys(self):
        state = default_form_state()
        state.global_options["sandbox_root"] = "/scratch"
        state.global_options["allowed_executables"] = ["g16", "orca"]
        mapping = build_mapping(state)
        assert mapping["global"]["sandbox_root"] == "/scratch"
        assert mapping["global"]["allowed_executables"] == ["g16", "orca"]

    def test_calc_step_blocks_yaml_dict_passthrough(self):
        state = default_form_state()
        state.steps.append(
            StepState(
                type="calc",
                params={
                    "name": "x",
                    "iprog": "orca",
                    "itask": "sp",
                    "keyword": "k",
                    "blocks": "%pal nprocs 8 end",
                },
            )
        )
        mapping = build_mapping(state)
        assert mapping["steps"][0]["params"]["blocks"] == "%pal nprocs 8 end"

    def test_step_name_promoted_to_top_level(self):
        state = default_form_state()
        state.steps.append(
            StepState(
                type="calc",
                params={"name": "my-opt", "iprog": "orca", "itask": "opt_freq", "keyword": "k"},
            )
        )
        mapping = build_mapping(state)
        assert mapping["steps"][0]["name"] == "my-opt"
        assert "name" not in mapping["steps"][0]["params"]

    def test_global_itask_drives_ts_visibility(self):
        """ts_bond_atoms should drop from the output when itask != ts."""
        state = default_form_state()
        state.global_options["itask"] = "opt_freq"
        state.global_options["ts_bond_atoms"] = [1, 2]
        state.global_options["scan_coarse_step"] = 0.5
        mapping = build_mapping(state)
        assert "ts_bond_atoms" not in mapping["global"]
        assert "scan_coarse_step" not in mapping["global"]

    def test_global_ts_options_visible_when_itask_ts(self):
        state = default_form_state()
        state.global_options["itask"] = "ts"
        state.global_options["ts_bond_atoms"] = [1, 2]
        state.global_options["scan_coarse_step"] = 0.5
        state.global_options["keyword"] = "B3LYP def2-SVP"
        mapping = build_mapping(state)
        # pair lists serialize as list of tuples
        pair = mapping["global"]["ts_bond_atoms"]
        assert list(pair[0]) == [1, 2]
        assert mapping["global"]["scan_coarse_step"] == 0.5


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_validate_state_passes_for_valid_form(self):
        state = default_form_state()
        state.global_options["keyword"] = "B3LYP def2-SVP"
        state.steps.append(
            StepState(
                type="calc",
                params={"name": "opt", "iprog": "orca", "itask": "opt_freq", "keyword": "k"},
            )
        )
        validate_state(state)

    def test_validate_state_rejects_bad_choice(self):
        state = default_form_state()
        state.global_options["iprog"] = "gibberish"
        with pytest.raises(ValidationError):
            validate_state(state)

    def test_validate_state_rejects_bad_int(self):
        state = default_form_state()
        state.global_options["charge"] = "not a number"
        with pytest.raises(ValidationError):
            validate_state(state)

    def test_validate_state_unknown_step_type(self):
        state = default_form_state()
        state.steps.append(StepState(type="bogus", params={}))
        with pytest.raises(ValidationError):
            validate_state(state)

    def test_validate_runtime_accepts_known_good_form(self):
        state = default_form_state()
        state.global_options["keyword"] = "B3LYP def2-SVP"
        state.steps.append(
            StepState(
                type="calc",
                params={"name": "opt", "iprog": "orca", "itask": "opt_freq", "keyword": "B3LYP def2-SVP"},
            )
        )
        wf = validate_runtime(state)
        assert isinstance(wf, WorkflowConfig)
        assert wf.global_options.keyword == "B3LYP def2-SVP"
