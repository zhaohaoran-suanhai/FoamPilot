"""Deterministic conformance checks between a frozen design and authored files."""

from __future__ import annotations

import math
from pathlib import PurePosixPath
import re
from typing import TYPE_CHECKING

from .models import InspectionIssue, InspectionReport

if TYPE_CHECKING:
    from foampilot.authoring import CaseBundle
    from foampilot.extensions import CapabilityRegistry
    from foampilot.preprocessing import InputMeshFacts
    from foampilot.simulation import CaseDesign, ResolvedValue


_APPLICATION = re.compile(r"(?m)^\s*application\s+([A-Za-z0-9_.+-]+)\s*;")
_END_TIME = re.compile(
    r"(?m)^\s*endTime\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*;"
)
_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def _issue(code: str, detail: str, path: str | None = None) -> InspectionIssue:
    return InspectionIssue(code=code, detail=detail, path=path)


def _advisory(path: str, detail: str) -> InspectionIssue:
    return InspectionIssue(
        code="DESIGN_CONFORMANCE_NOT_VERIFIED",
        detail=detail,
        path=path,
        severity="warning",
    )


def _file_map(bundle: CaseBundle) -> dict[str, str]:
    return {item.path: item.content for item in bundle.files}


def _fact_map(design: CaseDesign) -> dict[str, ResolvedValue]:
    return {item.field_path: item for item in design.proposal.iter_values()}


def _equivalent_number(left: object, right: float) -> bool:
    try:
        value = float(left)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return math.isclose(value, right, rel_tol=1.0e-12, abs_tol=1.0e-15)


def _check_common_facts(
    *,
    design: CaseDesign,
    bundle: CaseBundle,
    issues: list[InspectionIssue],
    advisories: list[InspectionIssue],
) -> None:
    manifest = bundle.manifest
    files = _file_map(bundle)
    facts = _fact_map(design)
    known_paths: set[str] = {"solver.family"}

    expected_solver = str(design.proposal.solver_family.value)
    if manifest.solver_executable != expected_solver:
        issues.append(
            _issue(
                "DESIGN_CONFORMANCE_SOLVER_MISMATCH",
                f"manifest solver {manifest.solver_executable} differs from frozen {expected_solver}",
                "manifest.solver_executable",
            )
        )
    control = _COMMENTS.sub("", files.get("system/controlDict", ""))
    application = _APPLICATION.search(control)
    if application is None or application.group(1) != expected_solver:
        issues.append(
            _issue(
                "DESIGN_CONFORMANCE_APPLICATION_MISMATCH",
                "controlDict application does not implement the frozen solver",
                "system/controlDict",
            )
        )

    for path, fact in facts.items():
        value = fact.value
        if path == "physics.family":
            known_paths.add(path)
            if str(value) != manifest.physics_family:
                issues.append(
                    _issue(
                        "DESIGN_CONFORMANCE_PHYSICS_FAMILY_MISMATCH",
                        f"manifest physics family {manifest.physics_family} differs from {value}",
                        "manifest.physics_family",
                    )
                )
        elif path == "physics.regime" and str(value) in {"steady", "transient"}:
            known_paths.add(path)
            if manifest.regime != value:
                issues.append(
                    _issue(
                        "DESIGN_CONFORMANCE_REGIME_MISMATCH",
                        f"manifest regime {manifest.regime} differs from {value}",
                        "manifest.regime",
                    )
                )
        elif path == "physics.turbulence":
            known_paths.add(path)
            requested = str(value).casefold()
            observed = (manifest.models.turbulence or "laminar").casefold()
            if requested == "laminar" and observed not in {"laminar", "none"}:
                issues.append(
                    _issue(
                        "DESIGN_CONFORMANCE_EXTRA_PHYSICAL_MODEL",
                        f"authored case activates {observed} for a laminar design",
                        "manifest.models.turbulence",
                    )
                )
            elif requested != "laminar" and observed != requested:
                issues.append(
                    _issue(
                        "DESIGN_CONFORMANCE_PHYSICAL_MODEL_MISMATCH",
                        f"authored turbulence model {observed} differs from {requested}",
                        "manifest.models.turbulence",
                    )
                )
        elif path.startswith("materials."):
            known_paths.add(path)
        elif path in {"time.end", "time.end_time"}:
            known_paths.add(path)
            match = _END_TIME.search(control)
            if match is None or not _equivalent_number(value, float(match.group(1))):
                issues.append(
                    _issue(
                        "DESIGN_CONFORMANCE_END_TIME_MISMATCH",
                        "controlDict endTime differs from the frozen time design",
                        "system/controlDict:endTime",
                    )
                )
        elif path.startswith("boundaries.") and path.endswith(".mesh_type"):
            known_paths.add(path)
            name = path.removeprefix("boundaries.").removesuffix(".mesh_type")
            patch = next((item for item in manifest.patches if item.name == name), None)
            if patch is None or patch.mesh_type != str(value):
                issues.append(
                    _issue(
                        "DESIGN_CONFORMANCE_PATCH_TYPE_MISMATCH",
                        f"patch {name} does not implement mesh type {value}",
                        f"manifest.patches.{name}",
                    )
                )
        elif path.startswith("regions.") and path.endswith((".kind", ".role")):
            known_paths.add(path)
            suffix = ".kind" if path.endswith(".kind") else ".role"
            name = path.removeprefix("regions.").removesuffix(suffix)
            expected_kind = {
                "porous_fluid": "fluid",
                "fluid": "fluid",
                "solid": "solid",
            }.get(str(value), str(value))
            region = next((item for item in manifest.regions if item.name == name), None)
            if region is None or region.kind != expected_kind:
                issues.append(
                    _issue(
                        "DESIGN_CONFORMANCE_REGION_KIND_MISMATCH",
                        f"region {name} does not implement kind {expected_kind}",
                        f"manifest.regions.{name}",
                    )
                )
        elif path == "mesh.strategy":
            known_paths.add(path)
            expected_mesh = str(value)
            if manifest.mesh_family != expected_mesh:
                issues.append(
                    _issue(
                        "DESIGN_CONFORMANCE_MESH_FAMILY_MISMATCH",
                        f"manifest mesh family {manifest.mesh_family} differs from {expected_mesh}",
                        "manifest.mesh_family",
                    )
                )
        elif path == "execution.mpi_ranks":
            known_paths.add(path)

    if any(path.startswith("materials.") for path in facts) and (
        "constant/physicalProperties" not in files
    ):
        issues.append(
            _issue(
                "DESIGN_CONFORMANCE_REQUIRED_MODEL_FILE_MISSING",
                "Foundation material design requires constant/physicalProperties",
                "constant/physicalProperties",
            )
        )

    for path in sorted(set(facts) - known_paths):
        advisories.append(
            _advisory(
                path,
                "no registered deterministic relation can verify this design field against authored text",
            )
        )


def _check_manifest_and_mesh(
    *,
    bundle: CaseBundle,
    mesh_facts: tuple[InputMeshFacts, ...],
    issues: list[InspectionIssue],
) -> None:
    files = _file_map(bundle)
    for path in sorted(files):
        parts = PurePosixPath(path).parts
        if "polyMesh" in parts:
            issues.append(
                _issue(
                    "DESIGN_CONFORMANCE_INPUT_MESH_OVERWRITE",
                    "authored bundle attempts to replace an input polyMesh member",
                    path,
                )
            )

    regions = {item.name: item for item in bundle.manifest.regions}
    for field in bundle.manifest.fields:
        region = regions[field.region]
        parts = PurePosixPath(field.path).parts
        if field.created_by != "solver" and parts[-1] != field.name:
            issues.append(
                _issue(
                    "DESIGN_CONFORMANCE_FIELD_REGION_MISMATCH",
                    "manifest field path does not match its field identity",
                    field.path,
                )
            )
        if region.name != "default" and region.name not in parts:
            issues.append(
                _issue(
                    "DESIGN_CONFORMANCE_FIELD_REGION_MISMATCH",
                    "regional field path omits its manifest region",
                    field.path,
                )
            )

    manifest_patches = {
        (item.region, item.name): item.mesh_type
        for item in bundle.manifest.patches
    }
    for mesh in mesh_facts:
        region = mesh.region or "default"
        for patch in mesh.patches:
            observed = manifest_patches.get((region, patch.name))
            if observed is not None and observed != patch.patch_type:
                issues.append(
                    _issue(
                        "DESIGN_CONFORMANCE_INPUT_PATCH_MISMATCH",
                        f"manifest changes input patch {patch.name} from {patch.patch_type} to {observed}",
                        f"manifest.patches.{patch.name}",
                    )
                )


def _check_extensions(
    *,
    design: CaseDesign,
    extensions: CapabilityRegistry,
    advisories: list[InspectionIssue],
) -> None:
    for extension_id in sorted(design.extension_identities):
        try:
            descriptor = extensions.descriptor(extension_id)
        except LookupError:
            advisories.append(
                _advisory(
                    f"extensions.{extension_id}",
                    "frozen extension is unavailable to the conformance verifier",
                )
            )
            continue
        for validator_id in descriptor.semantic_validators:
            advisories.append(
                _advisory(
                    f"extensions.{extension_id}.{validator_id}",
                    "extension declares an unregistered semantic validator",
                )
            )


def verify_design_conformance(
    *,
    design: CaseDesign,
    bundle: CaseBundle,
    mesh_facts: tuple[InputMeshFacts, ...],
    extensions: CapabilityRegistry,
) -> InspectionReport:
    """Block only deterministic contradictions; surface unknown relations."""

    issues: list[InspectionIssue] = []
    advisories: list[InspectionIssue] = []
    _check_common_facts(
        design=design,
        bundle=bundle,
        issues=issues,
        advisories=advisories,
    )
    _check_manifest_and_mesh(
        bundle=bundle,
        mesh_facts=mesh_facts,
        issues=issues,
    )
    _check_extensions(
        design=design,
        extensions=extensions,
        advisories=advisories,
    )
    return InspectionReport(issues=issues, advisories=advisories)


__all__ = ["verify_design_conformance"]
