from __future__ import annotations

from foampilot.qualification.runner import (
    load_private_validation,
    load_reference,
)
from foampilot.qualification.validators import (
    EXTRACTORS,
    validate_observations,
)


EXPECTED_HOLDOUT_METRICS = {
    "mhd-hartmann": {
        "final_time",
        "divergence_conservation",
        "velocity_profile",
    },
    "multiphase-capillary-rise": {
        "final_time",
        "liquid_volume",
        "interface_height",
    },
    "solid-plate-hole": {
        "final_iteration",
        "displacement_symmetry",
        "hole_edge_stress",
    },
}


def test_holdout_metric_contracts_are_complete_and_frozen() -> None:
    for case_id, expected_names in EXPECTED_HOLDOUT_METRICS.items():
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
        assert {
            metric.category.value for metric in validation.metrics
        } == {"completion", "conservation", "physics_golden"}
        reference_by_name = {
            str(metric["name"]): metric for metric in reference["metrics"]
        }
        for metric in validation.metrics:
            assert metric.tolerance == reference_by_name[
                metric.name
            ]["final_tolerance"]


def test_frozen_holdout_references_satisfy_their_own_contracts() -> None:
    for case_id in EXPECTED_HOLDOUT_METRICS:
        reference = load_reference(case_id)
        observations = {
            str(metric["name"]): metric["reference"]
            for metric in reference["metrics"]
        }

        results = validate_observations(observations, reference)

        assert results
        assert all(result.passed for result in results)
