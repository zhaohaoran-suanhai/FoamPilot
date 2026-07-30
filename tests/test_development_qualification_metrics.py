from __future__ import annotations

from foampilot.qualification.runner import (
    load_private_validation,
    load_reference,
    qualification_data_path,
)
from foampilot.qualification.validators import EXTRACTORS
from foampilot.qualification.validators import validate_observations
from foampilot.tasks import load_task_spec


EXPECTED_METRICS = {
    "scalar-transport-pitzdaily": {
        "final_time",
        "scalar_conservation",
        "downstream_scalar_profile",
    },
    "laminar-planar-poiseuille": {
        "final_time",
        "flow_balance",
        "velocity_profile",
    },
    "porous-angled-duct": {
        "final_iteration",
        "flow_balance",
        "pressure_drop",
    },
    "compressible-blocked-channel": {
        "final_time",
        "total_mass",
        "primitive_profiles",
    },
    "cht-cooling-cylinder": {
        "final_time",
        "interface_heat_balance",
        "temperature_profiles",
    },
    "srf-rotor": {
        "final_time",
        "flow_balance",
        "rotating_velocity_profile",
    },
}


def test_development_metric_contracts_and_hashes_are_frozen() -> None:
    for case_id, expected_names in EXPECTED_METRICS.items():
        validation = load_private_validation(case_id)
        reference = load_reference(case_id)
        validation_names = {metric.name for metric in validation.metrics}
        reference_names = {
            str(metric["name"]) for metric in reference["metrics"]
        }

        assert validation.case_id == case_id
        assert reference["case_id"] == case_id
        assert validation.source_sha256 == reference["source_sha256"]
        assert validation.golden_sha256 is not None
        assert validation_names == expected_names
        assert reference_names == expected_names
        assert case_id in EXTRACTORS
        reference_by_name = {
            str(metric["name"]): metric for metric in reference["metrics"]
        }
        for metric in validation.metrics:
            assert metric.tolerance == reference_by_name[
                metric.name
            ]["final_tolerance"]


def test_development_metrics_cover_completion_conservation_and_physics() -> None:
    for case_id in EXPECTED_METRICS:
        validation = load_private_validation(case_id)
        categories = {metric.category.value for metric in validation.metrics}

        assert categories == {
            "completion",
            "conservation",
            "physics_golden",
        }


def test_frozen_development_references_satisfy_their_own_contracts() -> None:
    for case_id in EXPECTED_METRICS:
        reference = load_reference(case_id)
        observations = {
            str(metric["name"]): metric["reference"]
            for metric in reference["metrics"]
        }

        results = validate_observations(observations, reference)

        assert results
        assert all(result.passed for result in results)


def test_scalar_reference_matches_public_potential_flow_initialization() -> None:
    task = load_task_spec(
        qualification_data_path("tasks", "scalar-transport-pitzdaily")
    )
    validation = load_private_validation("scalar-transport-pitzdaily")
    reference = load_reference("scalar-transport-pitzdaily")
    inventory = next(
        metric
        for metric in reference["metrics"]
        if metric["name"] == "scalar_conservation"
    )
    contract = next(
        metric
        for metric in validation.metrics
        if metric.name == "scalar_conservation"
    )

    assert "potential-flow" in task.prompt
    assert "potential-flow" in contract.tolerance_source
    assert inventory["reference"] > 0.99
    assert inventory["observed_repeat_spread"] == 0.0
