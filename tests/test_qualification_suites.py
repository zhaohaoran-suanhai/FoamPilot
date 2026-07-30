from __future__ import annotations

from collections import Counter

import pytest
from pydantic import ValidationError

from foampilot.qualification.suites import (
    QualificationSuite,
    SuiteRole,
    load_qualification_suite,
    qualification_suite_path,
)
from foampilot.qualification.runner import validate_qualification_inputs


def test_suite_requires_unique_cases() -> None:
    with pytest.raises(ValidationError, match="unique"):
        QualificationSuite(
            protocol_id="duplicate-v1",
            cases=[
                {"case_id": "same", "role": "development"},
                {"case_id": "same", "role": "holdout"},
            ],
        )


def test_suite_requires_safe_case_ids_and_bounded_workers() -> None:
    with pytest.raises(ValidationError):
        QualificationSuite(
            protocol_id="unsafe-v1",
            max_workers=3,
            cases=[{"case_id": "../escape", "role": "development"}],
        )


def test_official_six_suite_preserves_case_order_and_exclusive_case() -> None:
    suite = load_qualification_suite(
        qualification_suite_path("official-six-v1")
    )

    assert suite.protocol_id == "official-six-v1"
    assert [item.case_id for item in suite.cases] == [
        "laminar-cavity",
        "potential-cylinder",
        "rans-pitzdaily",
        "multiphase-dam-break",
        "compressible-shock-tube",
        "buoyant-cavity",
    ]
    assert [item.case_id for item in suite.cases if item.exclusive] == [
        "buoyant-cavity"
    ]


def test_controlled_learning_suite_has_6_6_3_split() -> None:
    suite = load_qualification_suite(
        qualification_suite_path("controlled-learning-15-v1")
    )
    counts = Counter(item.role for item in suite.cases)

    assert counts == {
        SuiteRole.REGRESSION: 6,
        SuiteRole.DEVELOPMENT: 6,
        SuiteRole.HOLDOUT: 3,
    }
    assert len(suite.cases) == 15


def test_controlled_learning_suite_has_all_frozen_inputs() -> None:
    suite = load_qualification_suite(
        qualification_suite_path("controlled-learning-15-v1")
    )

    assert validate_qualification_inputs(
        [item.case_id for item in suite.cases]
    ) == []
