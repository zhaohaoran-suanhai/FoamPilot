"""Bounded Foundation v10 explicit porous-source capability."""

from __future__ import annotations

from typing import TYPE_CHECKING

from foampilot.extensions.models import (
    CapabilityDescriptor,
    RequiredFact,
    SupportedTarget,
)
from foampilot.simulation.provenance import FactEvidence, ResolvedValue

if TYPE_CHECKING:
    from foampilot.simulation.design import CaseDesignProposal, ExtensionDecision


FOUNDATION10_POROUS_EXTENSION_ID = "foampilot.physics.foundation10-porous"
FOUNDATION10_POROUS_VALIDATOR_ID = "foundation10.porous-explicit"
_SUPPORTED_UNITS = {
    "m/s": "m/s",
    "m2/s": "m2/s",
    "m^2/s": "m2/s",
    "1/m2": "1/m2",
    "1/m^2": "1/m2",
    "m^-2": "1/m2",
    "1/m": "1/m",
    "m^-1": "1/m",
}


def _consistent_alias(
    value: dict[str, object],
    names: tuple[str, ...],
) -> tuple[object | None, bool]:
    present = [value[name] for name in names if name in value]
    if not present or any(item != present[0] for item in present[1:]):
        return None, False
    return present[0], True


def _explicit_unit(value: dict[str, object]) -> str | None:
    unit, consistent = _consistent_alias(value, ("unit", "units"))
    if not consistent or not isinstance(unit, str) or not unit.strip():
        return None
    return _SUPPORTED_UNITS.get(unit.strip())


def _leaf_fact(
    aggregate: ResolvedValue,
    *,
    field_path: str,
    value: object,
) -> ResolvedValue:
    return aggregate.model_copy(
        update={"field_path": field_path, "value": value}
    )


def _isotropic_scalar(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, dict):
        return None
    representations: list[float] = []
    for name in ("value", "tensor_diagonal", "diagonal", "vector"):
        if name not in value:
            continue
        candidate = value[name]
        if isinstance(candidate, (int, float)) and not isinstance(
            candidate,
            bool,
        ):
            representations.append(float(candidate))
            continue
        if not isinstance(candidate, list) or len(candidate) != 3:
            return None
        try:
            numbers = tuple(float(item) for item in candidate)
        except (TypeError, ValueError):
            return None
        if numbers[0] != numbers[1] or numbers[1] != numbers[2]:
            return None
        representations.append(numbers[0])
    if not representations or any(
        item != representations[0] for item in representations[1:]
    ):
        return None
    return representations[0]


def _canonical_vector(
    value: object,
    *,
    expected_unit: str,
) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    vector, consistent = _consistent_alias(value, ("value", "vector"))
    unit = _explicit_unit(value)
    if (
        not consistent
        or unit != expected_unit
        or not isinstance(vector, list)
        or len(vector) != 3
    ):
        return None
    try:
        numbers = [float(item) for item in vector]
    except (TypeError, ValueError):
        return None
    return {"value": numbers, "unit": unit}


def _canonical_porous_value(
    field_path: str,
    value: object,
    *,
    cell_zone: str,
) -> tuple[object, bool]:
    projected = False
    if field_path.endswith(".porosity_model"):
        if (
            isinstance(value, str)
            and value
            in {
                "explicitPorositySource/DarcyForchheimer",
                "explicit_Darcy_Forchheimer",
            }
        ):
            value = "DarcyForchheimer"
            projected = True
        elif isinstance(value, dict):
            source, source_ok = _consistent_alias(
                value,
                ("source_type", "model"),
            )
            resistance, resistance_ok = _consistent_alias(
                value,
                ("resistance_law", "resistance_model"),
            )
            selection, selection_ok = _consistent_alias(
                value,
                ("selection", "selection_mode"),
            )
            zone = value.get("zone", cell_zone)
            if (
                source_ok
                and source == "explicitPorositySource"
                and resistance_ok
                and resistance == "DarcyForchheimer"
                and selection_ok
                and selection == "cellZone"
                and zone == cell_zone
            ):
                value = "DarcyForchheimer"
                projected = True
    elif field_path.endswith(
        (".darcy_coefficient", ".forchheimer_coefficient")
    ):
        scalar = _isotropic_scalar(value)
        unit = _explicit_unit(value) if isinstance(value, dict) else None
        expected_unit = (
            "1/m2" if field_path.endswith(".darcy_coefficient") else "1/m"
        )
        if scalar is not None and unit == expected_unit:
            value = {"value": scalar, "unit": unit}
            projected = True
    return value, projected


def _canonical_porous_fact(
    fact: ResolvedValue,
    *,
    cell_zone: str,
) -> tuple[ResolvedValue, bool]:
    value, projected = _canonical_porous_value(
        fact.field_path,
        fact.value,
        cell_zone=cell_zone,
    )
    return fact.model_copy(update={"value": value}), projected


def _canonical_candidate_value(
    field_path: str,
    value: object,
    *,
    cell_zone: str,
    inlet_patch: str,
) -> tuple[object, bool]:
    if field_path == f"boundaries.{inlet_patch}.velocity" and isinstance(
        value, dict
    ):
        projected = _canonical_vector(value, expected_unit="m/s")
        if projected is not None:
            return projected, True
    return _canonical_porous_value(
        field_path,
        value,
        cell_zone=cell_zone,
    )


def canonicalize_foundation10_porous_proposal(
    proposal: "CaseDesignProposal",
    *,
    cell_zone: str,
    inlet_patch: str = "inlet",
) -> "CaseDesignProposal":
    """Project supported aggregate model output onto the extension contract."""

    projected_paths: set[str] = set()
    materials = list(proposal.materials)
    fluid = next(
        (item for item in materials if item.field_path == "materials.fluid"),
        None,
    )
    if fluid is not None and isinstance(fluid.value, dict):
        viscosity = fluid.value.get("kinematic_viscosity")
        unit = _explicit_unit(viscosity) if isinstance(viscosity, dict) else None
        if (
            isinstance(viscosity, dict)
            and "value" in viscosity
            and unit == "m2/s"
        ):
            materials = [item for item in materials if item is not fluid]
            materials.append(
                _leaf_fact(
                    fluid,
                    field_path="materials.fluid.nu",
                    value={
                        "value": viscosity["value"],
                        "unit": unit,
                    },
                )
            )
            projected_paths.add("materials.fluid.nu")

    time_design = [
        item for item in proposal.time_design if item.field_path != "time.delta_t"
    ]
    numerical_design = list(proposal.numerical_design)
    boundary_designs: list[ResolvedValue] = []
    for fact in proposal.boundary_designs:
        if (
            fact.field_path == f"boundaries.{inlet_patch}.velocity"
            and isinstance(fact.value, dict)
            and _canonical_vector(
                fact.value,
                expected_unit="m/s",
            )
            is not None
        ):
            projected_velocity = _canonical_vector(
                fact.value,
                expected_unit="m/s",
            )
            assert projected_velocity is not None
            fact = fact.model_copy(
                update={"value": projected_velocity}
            )
            projected_paths.add(fact.field_path)
        boundary_designs.append(fact)
    porous_fact_paths = {
        f"regions.{cell_zone}.porosity_model",
        f"regions.{cell_zone}.darcy_coefficient",
        f"regions.{cell_zone}.forchheimer_coefficient",
    }
    region_models: list[ResolvedValue] = []
    lifted_porous_facts: list[ResolvedValue] = []
    for item in proposal.region_models:
        if item.field_path == f"region_models.{cell_zone}":
            continue
        if item.field_path in porous_fact_paths:
            item, projected = _canonical_porous_fact(
                item,
                cell_zone=cell_zone,
            )
            lifted_porous_facts.append(item)
            if projected:
                projected_paths.add(item.field_path)
            continue
        region_models.append(item)
    decisions: list[ExtensionDecision] = []
    for decision in proposal.extension_decisions:
        if decision.extension_id != FOUNDATION10_POROUS_EXTENSION_ID:
            decisions.append(decision)
            continue
        values: list[ResolvedValue] = []
        for fact in decision.values:
            if fact.field_path in porous_fact_paths:
                fact, projected = _canonical_porous_fact(
                    fact,
                    cell_zone=cell_zone,
                )
                values.append(fact)
                if projected:
                    projected_paths.add(fact.field_path)
                continue
            if fact.field_path == "materials.fluid.nu":
                materials.append(fact)
            elif fact.field_path == f"boundaries.{inlet_patch}.velocity":
                value, projected = _canonical_candidate_value(
                    fact.field_path,
                    fact.value,
                    cell_zone=cell_zone,
                    inlet_patch=inlet_patch,
                )
                boundary_designs.append(
                    fact.model_copy(update={"value": value})
                    if projected
                    else fact
                )
                if projected:
                    projected_paths.add(fact.field_path)
            elif fact.field_path == "time.end":
                time_design.append(fact)
            elif fact.field_path == "numerics.delta_t":
                numerical_design.append(fact)
            elif fact.field_path == f"regions.{cell_zone}.role":
                region_models.append(fact)
        values.extend(lifted_porous_facts)
        decisions.append(decision.model_copy(update={"values": tuple(values)}))

    canonical = proposal.model_copy(
        update={
            "materials": tuple(sorted(materials, key=lambda item: item.field_path)),
            "boundary_designs": tuple(
                sorted(boundary_designs, key=lambda item: item.field_path)
            ),
            "time_design": tuple(
                sorted(time_design, key=lambda item: item.field_path)
            ),
            "numerical_design": tuple(
                sorted(numerical_design, key=lambda item: item.field_path)
            ),
            "region_models": tuple(
                sorted(region_models, key=lambda item: item.field_path)
            ),
            "extension_decisions": tuple(decisions),
        }
    )
    canonical_values = {
        item.field_path: item.value for item in canonical.iter_values()
    }
    uncertainties = []
    reporting_limitations: list[FactEvidence] = []
    uncertainty_aliases = {
        "materials.kinematic_viscosity": "materials.fluid.nu",
        "time.end_time": "time.end",
        "time.time_step_control": "numerics.delta_t",
        f"region_models.{cell_zone}.darcy_resistance": (
            f"regions.{cell_zone}.darcy_coefficient"
        ),
        f"region_models.{cell_zone}.inertial_resistance": (
            f"regions.{cell_zone}.forchheimer_coefficient"
        ),
    }
    preferred_uncertainty_paths = {
        item.field_path
        for item in canonical.uncertainties
        if item.field_path not in uncertainty_aliases
    }
    nonblocking_missing_paths = {
        "mesh.minimum_effective_cell_length",
        "mesh.minimum_cell_length",
        f"regions.{cell_zone}.geometric_extent",
        f"mesh.cell_zones.{cell_zone}.spatial_extent",
    }
    for uncertainty in canonical.uncertainties:
        alias_target = uncertainty_aliases.get(uncertainty.field_path)
        if alias_target is not None:
            if alias_target in preferred_uncertainty_paths:
                continue
            candidates = uncertainty.candidates
            if uncertainty.field_path == "time.time_step_control":
                projected_candidates = []
                for candidate in candidates:
                    if not isinstance(candidate.value, dict):
                        projected_candidates = []
                        break
                    delta_t = candidate.value.get("delta_t")
                    if delta_t is None:
                        projected_candidates = []
                        break
                    projected_candidates.append(
                        candidate.model_copy(update={"value": delta_t})
                    )
                if not projected_candidates:
                    uncertainties.append(uncertainty)
                    continue
                candidates = tuple(projected_candidates)
            uncertainty = uncertainty.model_copy(
                update={
                    "field_path": alias_target,
                    "candidates": candidates,
                }
            )
        if uncertainty.field_path == f"boundaries.{inlet_patch}.startup_profile":
            continue
        if (
            uncertainty.kind == "information_required"
            and uncertainty.field_path.startswith("observations.")
        ) or (
            uncertainty.kind in {"information_required", "confirmable"}
            and uncertainty.field_path in nonblocking_missing_paths
        ):
            reporting_limitations.append(
                FactEvidence(
                    kind="design_reporting_limitation",
                    detail=(
                        f"{uncertainty.field_path}: {uncertainty.reason_zh}"
                    ),
                )
            )
            continue
        value = canonical_values.get(uncertainty.field_path)
        if uncertainty.kind != "confirmable":
            uncertainties.append(uncertainty)
            continue
        if value is not None and uncertainty.field_path in projected_paths:
            candidates = tuple(
                candidate.model_copy(update={"value": value})
                for candidate in uncertainty.candidates
            )
        else:
            projected_candidates = []
            for candidate in uncertainty.candidates:
                candidate_value, projected = _canonical_candidate_value(
                    uncertainty.field_path,
                    candidate.value,
                    cell_zone=cell_zone,
                    inlet_patch=inlet_patch,
                )
                projected_candidates.append(
                    candidate.model_copy(update={"value": candidate_value})
                    if projected
                    else candidate
                )
            candidates = tuple(projected_candidates)
        uncertainties.append(
            uncertainty.model_copy(update={"candidates": candidates})
        )
    return canonical.model_copy(
        update={
            "uncertainties": tuple(uncertainties),
            "reasoning_evidence": (
                *canonical.reasoning_evidence,
                *reporting_limitations,
            ),
        }
    )


def foundation10_porous_descriptor(
    cell_zone: str,
    inlet_patch: str,
) -> CapabilityDescriptor:
    """Describe the exact facts needed for one selected porous cell zone."""

    return CapabilityDescriptor(
        extension_id=FOUNDATION10_POROUS_EXTENSION_ID,
        extension_version="1.0.0",
        protocol_version=1,
        capability_kinds=("physics:porous-explicit",),
        supported_targets=(
            SupportedTarget(distribution="foundation", versions=("10",)),
        ),
        required_executables=("pisoFoam",),
        input_contracts=("foampilot.simulation.CaseDesignProposal:1",),
        required_facts=(
            RequiredFact(
                field_path=f"regions.{cell_zone}.role",
                impact="high",
                description="Confirmed porous-fluid cellZone role",
            ),
            RequiredFact(
                field_path=f"regions.{cell_zone}.porosity_model",
                impact="high",
                description="Foundation v10 porous resistance model",
                resolution="designer_candidate",
            ),
            RequiredFact(
                field_path=f"regions.{cell_zone}.darcy_coefficient",
                impact="high",
                description="Darcy viscous resistance coefficient",
                resolution="designer_candidate",
            ),
            RequiredFact(
                field_path=f"regions.{cell_zone}.forchheimer_coefficient",
                impact="high",
                description="Forchheimer inertial resistance coefficient",
                resolution="designer_candidate",
            ),
            RequiredFact(
                field_path="materials.fluid.nu",
                impact="high",
                description="Kinematic viscosity",
                resolution="designer_candidate",
            ),
            RequiredFact(
                field_path=f"boundaries.{inlet_patch}.velocity",
                impact="high",
                description="Inlet velocity vector",
                resolution="designer_candidate",
            ),
            RequiredFact(
                field_path="time.end",
                impact="high",
                description="Transient end time",
                resolution="designer_candidate",
            ),
            RequiredFact(
                field_path="numerics.delta_t",
                impact="high",
                description="Transient time step",
                resolution="designer_candidate",
            ),
        ),
        output_contracts=("constant/fvModels", "constant/coordinateSystems"),
        required_authored_paths=(
            "constant/coordinateSystems",
            "constant/fvModels",
        ),
        authoring_rules=(
            "Foundation v10 constant/fvModels rule: put selectionMode and "
            "cellZone directly inside explicitPorositySourceCoeffs, beside "
            "type DarcyForchheimer, d, f, and coordinateSystem; do not put "
            "selectionMode or cellZone in the outer explicitPorositySource "
            "model block.",
            "Foundation v10 DarcyForchheimer rule: write plain d and f vectors "
            "such as d (10000 10000 10000); and f (0 0 0); inside "
            "explicitPorositySourceCoeffs; do not add ad-hoc dimension "
            "prefixes.",
        ),
        semantic_validators=(FOUNDATION10_POROUS_VALIDATOR_ID,),
    )


__all__ = [
    "FOUNDATION10_POROUS_EXTENSION_ID",
    "FOUNDATION10_POROUS_VALIDATOR_ID",
    "canonicalize_foundation10_porous_proposal",
    "foundation10_porous_descriptor",
]
