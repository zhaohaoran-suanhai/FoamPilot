"""Small, provenance-bearing solver-family semantic contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SemanticRuleProvenance(StrictModel):
    rule_id: str = Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")
    openfoam_distribution: Literal["foundation"] = "foundation"
    openfoam_version: Literal["10"] = "10"
    source: str = Field(min_length=1)
    severity: Literal["error", "warning"]
    tested_by: tuple[str, ...] = Field(min_length=1)


class FamilyContract(StrictModel):
    solver_executable: str
    required_files: tuple[str, ...] = ()
    required_field_names: tuple[str, ...] = ()
    required_stages: tuple[str, ...] = ()
    rule: SemanticRuleProvenance


GENERIC_RULES: dict[str, SemanticRuleProvenance] = {
    "solver_command": SemanticRuleProvenance(
        rule_id="of10.semantic.solver-command-match",
        source=(
            "Foundation OpenFOAM v10 application entrypoints and "
            "FoamPilot ExecutionPlan v3 command contract"
        ),
        severity="error",
        tested_by=(
            "tests/test_semantic_inspection.py::"
            "test_semantic_errors_capture_solver_application_and_field_mismatches",
        ),
    ),
    "application": SemanticRuleProvenance(
        rule_id="of10.semantic.controldict-application-match",
        source=(
            "Foundation OpenFOAM v10 Time/controlDict application dispatch"
        ),
        severity="error",
        tested_by=(
            "tests/test_semantic_inspection.py::"
            "test_semantic_errors_capture_solver_application_and_field_mismatches",
        ),
    ),
    "field": SemanticRuleProvenance(
        rule_id="of10.semantic.manifest-field-path",
        source=(
            "Foundation OpenFOAM v10 IOobject region and time-directory lookup"
        ),
        severity="error",
        tested_by=(
            "tests/test_semantic_inspection.py::"
            "test_semantic_errors_capture_solver_application_and_field_mismatches",
            "tests/test_semantic_inspection.py::"
            "test_region_aware_manifest_accepts_fluid_solid_cht_layout",
        ),
    ),
    "patch": SemanticRuleProvenance(
        rule_id="of10.semantic.manifest-patch-region",
        source=(
            "Foundation OpenFOAM v10 polyMesh boundary and fvPatchField lookup"
        ),
        severity="error",
        tested_by=(
            "tests/test_semantic_inspection.py::"
            "test_every_blocking_semantic_issue_has_rule_provenance",
        ),
    ),
    "command_stage": SemanticRuleProvenance(
        rule_id="of10.semantic.command-stage-shape",
        source=(
            "Foundation OpenFOAM v10 utility interfaces and FoamPilot "
            "ExecutionPlan v3 stage contract"
        ),
        severity="error",
        tested_by=(
            "tests/test_semantic_inspection.py::"
            "test_command_stage_shape_is_checked_without_guessing_unknown_utility",
        ),
    ),
    "mpi": SemanticRuleProvenance(
        rule_id="of10.semantic.parallel-decomposition",
        source=(
            "Foundation OpenFOAM v10 decomposePar and reconstructPar interfaces"
        ),
        severity="error",
        tested_by=(
            "tests/test_semantic_inspection.py::"
            "test_mpi_requires_decomposition_and_requested_reconstruction",
        ),
    ),
    "family_unregistered": SemanticRuleProvenance(
        rule_id="of10.semantic.family-unregistered",
        source="FoamPilot Stage B conservative semantic-inspection policy",
        severity="warning",
        tested_by=(
            "tests/test_semantic_inspection.py::"
            "test_unknown_solver_family_is_advisory_not_blocking",
        ),
    ),
}


FAMILY_CONTRACTS: dict[str, FamilyContract] = {
    "icoFoam": FamilyContract(
        solver_executable="icoFoam",
        required_files=(
            "system/controlDict",
            "system/fvSchemes",
            "system/fvSolution",
            "constant/physicalProperties",
        ),
        required_field_names=("U", "p"),
        required_stages=("solve",),
        rule=SemanticRuleProvenance(
            rule_id="of10.family.icofoam.required-case",
            source=(
                "Foundation OpenFOAM v10 applications/solvers/"
                "incompressible/icoFoam/createFields.H and icoFoam.C"
            ),
            severity="error",
            tested_by=(
                "tests/test_semantic_inspection.py::"
                "test_every_blocking_semantic_issue_has_rule_provenance",
                "tests/test_real_native_vertical_slice.py",
            ),
        ),
    )
}


def family_contract(solver: str) -> FamilyContract | None:
    return FAMILY_CONTRACTS.get(solver)
