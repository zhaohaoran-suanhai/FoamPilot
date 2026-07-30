"""Installable, role-aware qualification for FoamPilot."""

from .models import (
    QualificationMetric,
    QualificationReport,
    QualificationResult,
)
from .reporting import (
    CASE_ORDER,
    build_qualification_report,
    classify_qualification,
    native_case_dir,
)
from .runner import (
    run_official_six,
    run_qualification_suite,
    validate_qualification_inputs,
)
from .suites import (
    QualificationSuite,
    SuiteCase,
    SuiteRole,
    load_qualification_suite,
    qualification_suite_path,
)

__all__ = [
    "CASE_ORDER",
    "QualificationMetric",
    "QualificationReport",
    "QualificationResult",
    "QualificationSuite",
    "SuiteCase",
    "SuiteRole",
    "build_qualification_report",
    "classify_qualification",
    "native_case_dir",
    "load_qualification_suite",
    "qualification_suite_path",
    "run_official_six",
    "run_qualification_suite",
    "validate_qualification_inputs",
]
