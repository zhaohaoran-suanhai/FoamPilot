"""High-confidence cross-file semantic inspection for compiled plans."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re

from foampilot.manifests import (
    SemanticRuleProvenance,
    family_contract,
)
from foampilot.manifests.family_contracts import GENERIC_RULES
from foampilot.plans import CommandStage, ExecutionPlan
from foampilot.plans.command_stages import KNOWN_UTILITY_STAGES
from foampilot.tasks import TaskSpec

from .models import InspectionIssue, InspectionReport


_APPLICATION = re.compile(
    r"(?m)^\s*application\s+([A-Za-z0-9_.+-]+)\s*;"
)
_DIMENSIONS = re.compile(r"(?m)^\s*dimensions\s+\[([^]]+)\]\s*;")
def _semantic_issue(
    *,
    code: str,
    detail: str,
    rule: SemanticRuleProvenance,
    path: str | None = None,
) -> InspectionIssue:
    return InspectionIssue(
        code=code,
        path=path,
        detail=detail,
        severity=rule.severity,
        provenance=rule,
    )


def _text(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _requires_reconstruction(task: TaskSpec) -> bool:
    text = " ".join(
        (
            task.prompt,
            *task.required_outputs,
            *task.acceptance_requirements,
        )
    ).lower()
    return "reconstruct" in text


def _solver_run_enabled(task: TaskSpec) -> bool:
    return not any(
        fact.field_path == "execution.run_solver"
        and fact.confirmed
        and fact.value is False
        for fact in task.explicit_facts
    )


def _field_region_matches(
    path: str,
    *,
    prefix: str,
) -> bool:
    if not prefix:
        return True
    parts = PurePosixPath(path).parts
    return len(parts) >= 3 and parts[1] == prefix


def _stage_value(value: CommandStage | str) -> str:
    return value.value if isinstance(value, CommandStage) else value


def inspect_semantics(
    case_root: str | Path,
    task: TaskSpec,
    plan: ExecutionPlan,
) -> InspectionReport:
    """Check only deterministic v3 relationships; uncertainty is advisory."""

    root = Path(case_root).resolve()
    issues: list[InspectionIssue] = []
    advisories: list[InspectionIssue] = []
    manifest = plan.manifest
    regions = {region.name: region for region in manifest.regions}
    run_solver = _solver_run_enabled(task)

    solve_commands = [
        command
        for command in plan.commands
        if command.stage == CommandStage.SOLVE
    ]
    if not run_solver and solve_commands:
        issues.append(
            _semantic_issue(
                code="SEMANTIC_SOLVER_EXECUTION_FORBIDDEN",
                detail="case-only task must not contain a solve-stage command",
                path="commands",
                rule=GENERIC_RULES["solver_command"],
            )
        )
    elif run_solver and (
        not solve_commands
        or any(
            command.executable != manifest.solver_executable
            for command in solve_commands
        )
    ):
        issues.append(
            _semantic_issue(
                code="SEMANTIC_SOLVER_COMMAND_MISMATCH",
                detail=(
                    "manifest solver must match every solve-stage executable"
                ),
                path="commands",
                rule=GENERIC_RULES["solver_command"],
            )
        )

    control_path = root / "system/controlDict"
    control = _text(control_path)
    if control is not None:
        match = _APPLICATION.search(control)
        if (
            match is not None
            and match.group(1) != manifest.solver_executable
        ):
            issues.append(
                _semantic_issue(
                    code="SEMANTIC_APPLICATION_MISMATCH",
                    detail=(
                        "controlDict application does not match manifest "
                        f"solver {manifest.solver_executable}"
                    ),
                    path="system/controlDict",
                    rule=GENERIC_RULES["application"],
                )
            )

    for field in manifest.fields:
        region = regions[field.region]
        field_path = root / field.path
        name_mismatch = (
            field.created_by != "solver"
            and PurePosixPath(field.path).name != field.name
        )
        if name_mismatch or not _field_region_matches(
            field.path,
            prefix=region.path_prefix,
        ):
            issues.append(
                _semantic_issue(
                    code="SEMANTIC_FIELD_REGION_MISMATCH",
                    detail=(
                        "field path is inconsistent with its name or "
                        "region path_prefix"
                    ),
                    path=field.path,
                    rule=GENERIC_RULES["field"],
                )
            )
            continue
        if (
            not field_path.is_file()
            and field.created_by in {"author", "public_asset"}
        ):
            issues.append(
                _semantic_issue(
                    code="SEMANTIC_FIELD_PATH_MISSING",
                    detail=(
                        f"manifest field {field.region}:{field.name} "
                        "does not exist at its declared path"
                    ),
                    path=field.path,
                    rule=GENERIC_RULES["field"],
                )
            )
        if (
            manifest.solver_executable == "icoFoam"
            and field.created_by != "solver"
            and field.name in {"U", "p"}
        ):
            expected = {
                "U": "0 1 -1 0 0 0 0",
                "p": "0 2 -2 0 0 0 0",
            }[field.name]
            content = _text(field_path)
            match = _DIMENSIONS.search(content or "")
            observed = (
                " ".join(match.group(1).split())
                if match is not None
                else None
            )
            if observed is not None and observed != expected:
                issues.append(
                    _semantic_issue(
                        code="SEMANTIC_FIELD_DIMENSIONS_MISMATCH",
                        detail=(
                            f"icoFoam field {field.name} requires dimensions "
                            f"[{expected}]"
                        ),
                        path=field.path,
                        rule=GENERIC_RULES["field_dimensions"],
                    )
                )

    block_mesh = _text(root / "system/blockMeshDict")
    if block_mesh is not None and len(regions) == 1:
        if "#" not in block_mesh and "$" not in block_mesh:
            for patch in manifest.patches:
                if re.search(
                    rf"(?m)^\s*{re.escape(patch.name)}\s*(?:\n\s*)?\{{",
                    block_mesh,
                ) is None:
                    issues.append(
                        _semantic_issue(
                            code="SEMANTIC_PATCH_MESH_MISMATCH",
                            detail=(
                                "manifest patch is absent from the explicit "
                                "blockMesh boundary"
                            ),
                            path="system/blockMeshDict",
                            rule=GENERIC_RULES["patch"],
                        )
                    )
    elif manifest.patches:
        advisories.append(
            _semantic_issue(
                code="SEMANTIC_PATCH_REGION_UNVERIFIED",
                detail=(
                    "regional or generated mesh patch membership cannot be "
                    "proven before native mesh generation"
                ),
                rule=GENERIC_RULES["family_unregistered"],
            )
        )

    for index, command in enumerate(plan.commands):
        expected = (
            CommandStage.SOLVE
            if command.executable == manifest.solver_executable
            else KNOWN_UTILITY_STAGES.get(command.executable)
        )
        if expected is None:
            advisories.append(
                _semantic_issue(
                    code="SEMANTIC_COMMAND_SHAPE_UNREGISTERED",
                    detail=(
                        "command executable has no deterministic stage rule"
                    ),
                    path=f"commands[{index}]",
                    rule=GENERIC_RULES["family_unregistered"],
                )
            )
        elif command.stage != expected:
            issues.append(
                _semantic_issue(
                    code="SEMANTIC_COMMAND_STAGE_MISMATCH",
                    detail=(
                        f"{command.executable} requires stage {expected.value}"
                    ),
                    path=f"commands[{index}].stage",
                    rule=GENERIC_RULES["command_stage"],
                )
            )

    parallel_solve = any(
        command.mpi_ranks > 1 for command in solve_commands
    )
    if parallel_solve:
        has_decompose_config = any(
            PurePosixPath(item.path).name == "decomposeParDict"
            for item in plan.files
        )
        if not has_decompose_config:
            issues.append(
                _semantic_issue(
                    code="SEMANTIC_MPI_DECOMPOSE_CONFIG_MISSING",
                    detail="MPI solve requires a decomposeParDict",
                    path="system/decomposeParDict",
                    rule=GENERIC_RULES["mpi"],
                )
            )
        if not any(
            command.stage == CommandStage.DECOMPOSE
            for command in plan.commands
        ):
            issues.append(
                _semantic_issue(
                    code="SEMANTIC_MPI_DECOMPOSE_STAGE_MISSING",
                    detail="MPI solve requires a decomposition command stage",
                    path="commands",
                    rule=GENERIC_RULES["mpi"],
                )
            )
        if (
            _requires_reconstruction(task)
            and not any(
                command.stage == CommandStage.RECONSTRUCT
                for command in plan.commands
            )
        ):
            issues.append(
                _semantic_issue(
                    code="SEMANTIC_RECONSTRUCT_STAGE_MISSING",
                    detail=(
                        "the public task requires reconstructed parallel "
                        "results"
                    ),
                    path="commands",
                    rule=GENERIC_RULES["mpi"],
                )
            )

    contract = family_contract(manifest.solver_executable)
    if contract is None:
        advisories.append(
            _semantic_issue(
                code="SEMANTIC_FAMILY_UNREGISTERED",
                detail=(
                    "no blocking family contract is registered; generic "
                    "semantic checks still apply"
                ),
                rule=GENERIC_RULES["family_unregistered"],
            )
        )
    else:
        for required in contract.required_files:
            if not (root / required).is_file():
                issues.append(
                    _semantic_issue(
                        code="SEMANTIC_FAMILY_REQUIRED_FILE_MISSING",
                        detail=f"{manifest.solver_executable} requires {required}",
                        path=required,
                        rule=contract.rule,
                    )
                )
        field_names = {field.name for field in manifest.fields}
        for required in contract.required_field_names:
            if required not in field_names:
                issues.append(
                    _semantic_issue(
                        code="SEMANTIC_FAMILY_REQUIRED_FIELD_MISSING",
                        detail=(
                            f"{manifest.solver_executable} manifest requires "
                            f"field {required}"
                        ),
                        path="manifest.fields",
                        rule=contract.rule,
                    )
                )
        stages = {_stage_value(command.stage) for command in plan.commands}
        for required in contract.required_stages:
            if required == "solve" and not run_solver:
                continue
            if required not in stages:
                issues.append(
                    _semantic_issue(
                        code="SEMANTIC_FAMILY_REQUIRED_STAGE_MISSING",
                        detail=(
                            f"{manifest.solver_executable} requires command "
                            f"stage {required}"
                        ),
                        path="commands",
                        rule=contract.rule,
                    )
                )

    return InspectionReport(
        issues=issues,
        advisories=advisories,
    )
