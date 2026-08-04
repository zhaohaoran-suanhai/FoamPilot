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


def test_public_validation_cases_require_only_public_task_assets(
    tmp_path,
    monkeypatch,
) -> None:
    task = tmp_path / "public-only.yaml"
    source = (
        qualification_suite_path("controlled-learning-15-v1")
        .parent.parent
        / "tasks"
        / "mhd-hartmann.yaml"
    )
    task.write_text(
        source.read_text(encoding="utf-8").replace(
            "mhd-hartmann",
            "public-only",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "foampilot.qualification.runner.qualification_data_path",
        lambda kind, case_id: (
            task if kind == "tasks" else tmp_path / f"missing.{kind}"
        ),
    )

    assert validate_qualification_inputs(
        ["public-only"],
        public_validation_only={"public-only"},
    ) == []


def test_official_corpus_suite_has_thirty_distinct_tasks_and_two_levels() -> None:
    suite = load_qualification_suite(
        qualification_suite_path("official-corpus-30-baseline-v1")
    )
    public_only = {
        item.case_id
        for item in suite.cases
        if item.evaluation_level == "public_validation"
    }

    assert len(suite.cases) == 30
    assert len({item.case_id for item in suite.cases}) == 30
    assert len(public_only) == 15
    assert validate_qualification_inputs(
        [item.case_id for item in suite.cases],
        public_validation_only=public_only,
    ) == []
