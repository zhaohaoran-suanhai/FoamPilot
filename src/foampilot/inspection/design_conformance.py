"""Deterministic conformance checks between a frozen design and authored files."""

from __future__ import annotations

import math
from pathlib import PurePosixPath
import re
from typing import TYPE_CHECKING

from .models import InspectionIssue, InspectionReport
from foampilot.extensions.physics.foundation10_porous import (
    FOUNDATION10_POROUS_VALIDATOR_ID,
)

if TYPE_CHECKING:
    from foampilot.authoring import CaseBundle
    from foampilot.extensions import CapabilityRegistry
    from foampilot.preprocessing import InputMeshFacts
    from foampilot.simulation import CaseDesign, ResolvedValue


_APPLICATION = re.compile(r"(?m)^\s*application\s+([A-Za-z0-9_.+-]+)\s*;")
_END_TIME = re.compile(
    r"(?m)^\s*endTime\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*;"
)
_NUMERICAL_ENTRY = re.compile(
    r"(?m)^\s*{keyword}\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*;"
)
_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_DARCY_VECTOR = re.compile(r"(?<![A-Za-z0-9_])d\s*\(([^)]*)\)\s*;")
_FORCHHEIMER_VECTOR = re.compile(r"(?<![A-Za-z0-9_])f\s*\(([^)]*)\)\s*;")
_NU = re.compile(
    r"(?m)^\s*nu\s+(?:\[[^]]+\]\s+)?"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*;"
)
_FIXED_VALUE = re.compile(r"(?m)^\s*type\s+fixedValue\s*;")
_UNIFORM_VECTOR_VALUE = re.compile(
    r"(?m)^\s*value\s+uniform\s*\(([^)]*)\)\s*;"
)
_SUPPORTED_FACT_UNITS = {
    "m/s": "m/s",
    "m2/s": "m2/s",
    "m^2/s": "m2/s",
    "1/m2": "1/m2",
    "1/m^2": "1/m2",
    "m^-2": "1/m2",
    "1/m": "1/m",
    "m^-1": "1/m",
}


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
    if isinstance(left, dict) and "value" in left:
        left = left["value"]
    try:
        value = float(left)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return math.isclose(value, right, rel_tol=1.0e-12, abs_tol=1.0e-15)


def _number(value: object) -> float | None:
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _fact_unit(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    units = [value[name] for name in ("unit", "units") if name in value]
    if (
        not units
        or any(item != units[0] for item in units[1:])
        or not isinstance(units[0], str)
    ):
        return None
    return _SUPPORTED_FACT_UNITS.get(units[0].strip())


def _vector_numbers(match: re.Match[str] | None) -> tuple[float, ...]:
    if match is None:
        return ()
    try:
        return tuple(float(item) for item in match.group(1).split())
    except ValueError:
        return ()


def _expected_vector(value: object) -> tuple[float, ...]:
    if isinstance(value, dict):
        representations = [
            value[name]
            for name in ("value", "vector")
            if name in value
        ]
        if (
            not representations
            or any(item != representations[0] for item in representations[1:])
        ):
            return ()
        value = representations[0]
    if not isinstance(value, (list, tuple)):
        return ()
    try:
        return tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return ()


def _named_dictionary_block(text: str, name: str) -> str | None:
    """Return one simple OpenFOAM dictionary block without parsing values."""

    opening = re.compile(
        rf"(?m)^\s*{re.escape(name)}\s*\{{"
    ).search(text)
    if opening is None:
        return None
    brace = text.find("{", opening.start())
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[brace + 1 : index]
    return None


def _matching_brace(text: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _top_level_dictionary_blocks(text: str) -> tuple[tuple[str, str], ...]:
    """Return balanced top-level OpenFOAM dictionary blocks."""

    blocks: list[tuple[str, str]] = []
    index = 0
    while index < len(text):
        opening = text.find("{", index)
        if opening < 0:
            break
        name_match = re.search(r"([A-Za-z0-9_.:-]+)\s*$", text[:opening])
        closing = _matching_brace(text, opening)
        if closing is None:
            break
        if name_match is not None:
            blocks.append(
                (name_match.group(1), text[opening + 1 : closing])
            )
        index = closing + 1
    return tuple(blocks)


def _direct_dictionary_text(text: str) -> str:
    """Blank nested dictionaries so entry searches cannot cross scopes."""

    direct: list[str] = []
    depth = 0
    for character in text:
        if character == "{":
            depth += 1
            direct.append(" ")
        elif character == "}":
            direct.append(" ")
            depth = max(0, depth - 1)
        elif depth == 0:
            direct.append(character)
        else:
            direct.append("\n" if character == "\n" else " ")
    return "".join(direct)


def _direct_words(text: str, keyword: str) -> tuple[str, ...]:
    return tuple(
        match.group(1)
        for match in re.finditer(
            rf"(?m)(?:^|(?<=[;\n]))\s*{re.escape(keyword)}\s+"
            r"([A-Za-z0-9_.:-]+)\s*;",
            _direct_dictionary_text(text),
        )
    )


def _direct_word(text: str, keyword: str) -> str | None:
    values = _direct_words(text, keyword)
    return values[0] if len(values) == 1 else None


def _direct_vectors(
    text: str,
    pattern: re.Pattern[str],
) -> tuple[tuple[float, ...], ...]:
    return tuple(
        _vector_numbers(match)
        for match in pattern.finditer(_direct_dictionary_text(text))
    )


def _direct_named_vectors(
    text: str,
    keyword: str,
) -> tuple[tuple[float, ...], ...]:
    pattern = re.compile(
        rf"(?m)(?:^|(?<=[;\n]))\s*{re.escape(keyword)}\s*"
        r"\(([^)]*)\)\s*;"
    )
    return _direct_vectors(text, pattern)


def _foundation10_coordinate_system_vectors(
    block: str,
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]] | None:
    if _direct_word(block, "type") != "cartesian":
        return None
    origins = _direct_named_vectors(block, "origin")
    rotation_blocks = [
        nested
        for name, nested in _top_level_dictionary_blocks(block)
        if name == "coordinateRotation"
    ]
    if (
        len(origins) != 1
        or len(origins[0]) != 3
        or not all(math.isfinite(value) for value in origins[0])
        or len(rotation_blocks) != 1
    ):
        return None
    rotation = rotation_blocks[0]
    if _direct_word(rotation, "type") != "axesRotation":
        return None
    e1_values = _direct_named_vectors(rotation, "e1")
    e2_values = _direct_named_vectors(rotation, "e2")
    if len(e1_values) != 1 or len(e2_values) != 1:
        return None
    e1, e2 = e1_values[0], e2_values[0]
    if (
        len(e1) != 3
        or len(e2) != 3
        or not all(math.isfinite(value) for value in (*e1, *e2))
    ):
        return None
    norm1 = math.sqrt(sum(value * value for value in e1))
    norm2 = math.sqrt(sum(value * value for value in e2))
    if norm1 == 0 or norm2 == 0:
        return None
    cross = (
        e1[1] * e2[2] - e1[2] * e2[1],
        e1[2] * e2[0] - e1[0] * e2[2],
        e1[0] * e2[1] - e1[1] * e2[0],
    )
    cross_norm = math.sqrt(sum(value * value for value in cross))
    if cross_norm <= 1.0e-12 * norm1 * norm2:
        return None
    return origins[0], e1, e2


def _expected_coordinate_system_vectors(
    value: object,
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]] | None:
    if not isinstance(value, dict) or value.get("type") != "cartesian":
        return None
    axes = value.get("axes")
    if not isinstance(axes, dict):
        return None
    origin = _expected_vector(value.get("origin"))
    e1 = _expected_vector(axes.get("e1"))
    e2 = _expected_vector(axes.get("e2"))
    if any(
        len(vector) != 3
        or not all(math.isfinite(component) for component in vector)
        for vector in (origin, e1, e2)
    ):
        return None
    return origin, e1, e2


def _foundation_laminar_stokes_valid(text: str) -> bool:
    if not text:
        return False
    if _direct_word(text, "simulationType") != "laminar":
        return False
    laminar_blocks = [
        block
        for name, block in _top_level_dictionary_blocks(text)
        if name == "laminar"
    ]
    return (
        len(laminar_blocks) == 1
        and _direct_word(laminar_blocks[0], "model") == "Stokes"
    )


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
            if requested == "laminar" and observed not in {
                "laminar",
                "none",
                "stokes",
                "laminar/stokes",
                "laminar stokes",
            }:
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
            if requested == "laminar":
                momentum = _COMMENTS.sub(
                    "",
                    files.get("constant/momentumTransport", ""),
                )
                if not _foundation_laminar_stokes_valid(momentum):
                    issues.append(
                        _issue(
                            "DESIGN_CONFORMANCE_PHYSICAL_MODEL_MISMATCH",
                            "momentumTransport must structurally select laminar/Stokes",
                            "constant/momentumTransport",
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
        elif path in {"numerics.delta_t", "numerics.deltaT"}:
            known_paths.add(path)
            match = re.compile(
                _NUMERICAL_ENTRY.pattern.format(keyword="deltaT")
            ).search(control)
            if match is None or not _equivalent_number(value, float(match.group(1))):
                issues.append(
                    _issue(
                        "DESIGN_CONFORMANCE_NUMERICAL_VALUE_MISMATCH",
                        "controlDict deltaT differs from the frozen numerical design",
                        "system/controlDict:deltaT",
                    )
                )
        elif path in {"numerics.max_co", "numerics.maxCo"}:
            known_paths.add(path)
            match = re.compile(
                _NUMERICAL_ENTRY.pattern.format(keyword="maxCo")
            ).search(control)
            if match is None or not _equivalent_number(value, float(match.group(1))):
                issues.append(
                    _issue(
                        "DESIGN_CONFORMANCE_NUMERICAL_VALUE_MISMATCH",
                        "controlDict maxCo differs from the frozen numerical design",
                        "system/controlDict:maxCo",
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
            if str(value) in {"porous", "porous_fluid"}:
                continue
            expected_kind = {
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
    nu_fact = facts.get("materials.fluid.nu")
    if nu_fact is not None and "constant/physicalProperties" in files:
        physical = _COMMENTS.sub("", files["constant/physicalProperties"])
        match = _NU.search(physical)
        if (
            _fact_unit(nu_fact.value) != "m2/s"
            or match is None
            or not _equivalent_number(
                nu_fact.value,
                float(match.group(1)),
            )
        ):
            issues.append(
                _issue(
                    "DESIGN_CONFORMANCE_MATERIAL_VALUE_MISMATCH",
                    "physicalProperties nu differs from the frozen material design",
                    "constant/physicalProperties:nu",
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
            if observed is None:
                issues.append(
                    _issue(
                        "DESIGN_CONFORMANCE_INPUT_PATCH_MISSING",
                        f"manifest omits input patch {patch.name}",
                        f"manifest.patches.{patch.name}",
                    )
                )
            elif observed != patch.patch_type:
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
    bundle: CaseBundle,
    mesh_facts: tuple[InputMeshFacts, ...],
    issues: list[InspectionIssue],
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
            if validator_id == FOUNDATION10_POROUS_VALIDATOR_ID:
                _check_foundation10_porous(
                    design=design,
                    bundle=bundle,
                    mesh_facts=mesh_facts,
                    issues=issues,
                )
            else:
                advisories.append(
                    _advisory(
                        f"extensions.{extension_id}.{validator_id}",
                        "extension declares an unregistered semantic validator",
                    )
                )


def _check_foundation10_porous(
    *,
    design: CaseDesign,
    bundle: CaseBundle,
    mesh_facts: tuple[InputMeshFacts, ...],
    issues: list[InspectionIssue],
) -> None:
    files = _file_map(bundle)
    model_text = _COMMENTS.sub("", files.get("constant/fvModels", ""))
    coordinates = _COMMENTS.sub(
        "", files.get("constant/coordinateSystems", "")
    )
    if not model_text:
        issues.append(
            _issue(
                "DESIGN_CONFORMANCE_POROUS_MODEL_MISSING",
                "porous design requires constant/fvModels",
                "constant/fvModels",
            )
        )
        return
    source_blocks = [
        block
        for _name, block in _top_level_dictionary_blocks(model_text)
        if _direct_word(block, "type") == "explicitPorositySource"
    ]
    coefficient_blocks = (
        [
            block
            for name, block in _top_level_dictionary_blocks(source_blocks[0])
            if name == "explicitPorositySourceCoeffs"
        ]
        if len(source_blocks) == 1
        else []
    )
    coefficient_text = (
        _direct_dictionary_text(coefficient_blocks[0])
        if len(coefficient_blocks) == 1
        else ""
    )
    coherent_model = (
        len(source_blocks) == 1
        and len(coefficient_blocks) == 1
        and _direct_word(coefficient_blocks[0], "selectionMode")
        == "cellZone"
        and _direct_word(coefficient_blocks[0], "type")
        == "DarcyForchheimer"
    )
    if not coherent_model:
        issues.append(
            _issue(
                "DESIGN_CONFORMANCE_POROUS_MODEL_MISMATCH",
                "fvModels must contain one coherent explicitPorositySourceCoeffs cellZone/DarcyForchheimer block",
                "constant/fvModels",
            )
        )
    facts = _fact_map(design)
    model_facts = [
        fact
        for path, fact in facts.items()
        if path.startswith("regions.") and path.endswith(".porosity_model")
    ]
    if len(model_facts) != 1 or str(model_facts[0].value) != "DarcyForchheimer":
        issues.append(
            _issue(
                "DESIGN_CONFORMANCE_POROUS_MODEL_MISMATCH",
                "frozen porous model must be DarcyForchheimer for this capability",
                "design.regions.porosity_model",
            )
        )
    expected_zones = {
        path.removeprefix("regions.").removesuffix(".role")
        for path, fact in _fact_map(design).items()
        if path.startswith("regions.")
        and path.endswith(".role")
        and str(fact.value) in {"porous", "porous_fluid"}
    }
    selected_zone = _direct_word(coefficient_text, "cellZone")
    selected = {selected_zone} if selected_zone is not None else set()
    available = {
        zone.name
        for mesh in mesh_facts
        for zone in mesh.cell_zones
        if zone.element_count > 0
    }
    if selected != expected_zones or not selected.issubset(available):
        issues.append(
            _issue(
                "DESIGN_CONFORMANCE_POROUS_ZONE_MISMATCH",
                "fvModels cellZone does not match the frozen non-empty porous zone",
                "constant/fvModels:cellZone",
            )
        )
    coordinate_system = _direct_word(coefficient_text, "coordinateSystem")
    coordinate_systems = (
        {coordinate_system} if coordinate_system is not None else set()
    )
    coordinate_blocks = (
        [
            block
            for block_name, block in _top_level_dictionary_blocks(
                coordinates
            )
            if block_name == coordinate_system
        ]
        if coordinate_system is not None
        else []
    )
    if (
        not coordinates
        or not coordinate_systems
        or len(coordinate_blocks) != 1
    ):
        issues.append(
            _issue(
                "DESIGN_CONFORMANCE_POROUS_COORDINATE_SYSTEM_MISSING",
                "fvModels must reference a defined coordinate system",
                "constant/coordinateSystems",
            )
        )
    else:
        observed_coordinates = _foundation10_coordinate_system_vectors(
            coordinate_blocks[0]
        )
        if observed_coordinates is None:
            issues.append(
                _issue(
                    "DESIGN_CONFORMANCE_POROUS_COORDINATE_SYSTEM_INVALID",
                    "referenced coordinate system must be one valid cartesian/axesRotation dictionary with finite origin, e1, and e2 vectors",
                    "constant/coordinateSystems",
                )
            )
        else:
            frozen_coordinate_facts = [
                facts[f"regions.{zone}.coordinate_system"]
                for zone in sorted(expected_zones)
                if f"regions.{zone}.coordinate_system" in facts
            ]
            if frozen_coordinate_facts:
                expected_coordinates = (
                    _expected_coordinate_system_vectors(
                        frozen_coordinate_facts[0].value
                    )
                    if len(frozen_coordinate_facts) == 1
                    else None
                )
                coordinates_match = (
                    expected_coordinates is not None
                    and all(
                        _equivalent_number(expected, observed)
                        for expected_vector, observed_vector in zip(
                            expected_coordinates,
                            observed_coordinates,
                            strict=True,
                        )
                        for expected, observed in zip(
                            expected_vector,
                            observed_vector,
                            strict=True,
                        )
                    )
                )
                if not coordinates_match:
                    issues.append(
                        _issue(
                            "DESIGN_CONFORMANCE_POROUS_COORDINATE_SYSTEM_MISMATCH",
                            "authored origin/e1/e2 do not match the frozen porous coordinate system",
                            "constant/coordinateSystems",
                        )
                    )
    inlet_facts = [
        (path.removeprefix("boundaries.").removesuffix(".velocity"), fact)
        for path, fact in facts.items()
        if path.startswith("boundaries.") and path.endswith(".velocity")
    ]
    velocity_text = _COMMENTS.sub("", files.get("0/U", ""))
    for inlet, fact in inlet_facts:
        expected_velocity = _expected_vector(fact.value)
        has_vector = isinstance(fact.value, (list, tuple)) or (
            isinstance(fact.value, dict)
            and any(name in fact.value for name in ("value", "vector"))
        )
        if not has_vector:
            continue
        if len(expected_velocity) != 3 or _fact_unit(fact.value) != "m/s":
            issues.append(
                _issue(
                    "DESIGN_CONFORMANCE_POROUS_INLET_VELOCITY_MISMATCH",
                    "frozen inlet velocity must be a three-vector in m/s",
                    f"design.boundaries.{inlet}.velocity",
                )
            )
            continue
        inlet_block = _named_dictionary_block(velocity_text, inlet)
        value_match = (
            _UNIFORM_VECTOR_VALUE.search(inlet_block)
            if inlet_block is not None
            else None
        )
        observed_velocity = _vector_numbers(value_match)
        if (
            inlet_block is None
            or _FIXED_VALUE.search(inlet_block) is None
            or len(observed_velocity) != 3
            or not all(
                _equivalent_number(expected, observed)
                for expected, observed in zip(
                    expected_velocity,
                    observed_velocity,
                    strict=True,
                )
            )
        ):
            issues.append(
                _issue(
                    "DESIGN_CONFORMANCE_POROUS_INLET_VELOCITY_MISMATCH",
                    "0/U inlet fixedValue differs from the frozen velocity vector",
                    f"0/U:boundaryField.{inlet}",
                )
            )
    for zone in expected_zones:
        darcy = facts.get(f"regions.{zone}.darcy_coefficient")
        forchheimer = facts.get(f"regions.{zone}.forchheimer_coefficient")
        expected_d = (
            _number(darcy.value)
            if darcy is not None and _fact_unit(darcy.value) == "1/m2"
            else None
        )
        expected_f = (
            _number(forchheimer.value)
            if forchheimer is not None
            and _fact_unit(forchheimer.value) == "1/m"
            else None
        )
        direct_d = _direct_vectors(coefficient_text, _DARCY_VECTOR)
        direct_f = _direct_vectors(coefficient_text, _FORCHHEIMER_VECTOR)
        observed_d = direct_d[0] if len(direct_d) == 1 else ()
        observed_f = direct_f[0] if len(direct_f) == 1 else ()
        if expected_d is None or len(observed_d) != 3 or not all(
            _equivalent_number(expected_d, item) for item in observed_d
        ):
            issues.append(
                _issue(
                    "DESIGN_CONFORMANCE_POROUS_DARCY_MISMATCH",
                    "fvModels d vector differs from the frozen Darcy coefficient",
                    "constant/fvModels:d",
                )
            )
        if expected_f is None or len(observed_f) != 3 or not all(
            _equivalent_number(expected_f, item) for item in observed_f
        ):
            issues.append(
                _issue(
                    "DESIGN_CONFORMANCE_POROUS_FORCHHEIMER_MISMATCH",
                    "fvModels f vector differs from the frozen Forchheimer coefficient",
                    "constant/fvModels:f",
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
        bundle=bundle,
        mesh_facts=mesh_facts,
        issues=issues,
        advisories=advisories,
    )
    return InspectionReport(issues=issues, advisories=advisories)


__all__ = ["verify_design_conformance"]
