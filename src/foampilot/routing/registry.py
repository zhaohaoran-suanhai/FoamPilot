"""Small solver-family facts used only for routing consistency."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SolverCapability:
    family: str
    regime: str = "unknown"
    compressibility: str = "unknown"
    phase_family: str = "unknown"
    energy: str = "unknown"
    turbulence: str = "unknown"
    physics_family: str = "fluid"


SOLVER_CAPABILITIES: dict[str, SolverCapability] = {
    "icoFoam": SolverCapability(
        family="incompressible-laminar",
        regime="transient",
        compressibility="incompressible",
        phase_family="single_phase",
        energy="disabled",
        turbulence="laminar",
    ),
    "pisoFoam": SolverCapability(
        family="incompressible-transient",
        regime="transient",
        compressibility="incompressible",
        phase_family="single_phase",
        energy="disabled",
    ),
    "pimpleFoam": SolverCapability(
        family="incompressible-transient",
        regime="transient",
        compressibility="incompressible",
        phase_family="single_phase",
        energy="disabled",
    ),
    "simpleFoam": SolverCapability(
        family="incompressible-rans",
        regime="steady",
        compressibility="incompressible",
        phase_family="single_phase",
        energy="disabled",
        turbulence="rans",
    ),
    "potentialFoam": SolverCapability(
        family="incompressible-potential",
        regime="steady",
        compressibility="incompressible",
        phase_family="single_phase",
        energy="disabled",
        turbulence="laminar",
    ),
    "interFoam": SolverCapability(
        family="incompressible-vof",
        regime="transient",
        compressibility="incompressible",
        phase_family="vof",
        energy="disabled",
    ),
    "rhoCentralFoam": SolverCapability(
        family="compressible-density-based",
        regime="transient",
        compressibility="compressible",
        phase_family="single_phase",
        energy="enabled",
    ),
    "rhoPimpleFoam": SolverCapability(
        family="compressible-transient",
        regime="transient",
        compressibility="compressible",
        phase_family="single_phase",
        energy="enabled",
    ),
    "rhoSimpleFoam": SolverCapability(
        family="compressible-rans",
        regime="steady",
        compressibility="compressible",
        phase_family="single_phase",
        energy="enabled",
        turbulence="rans",
    ),
    "buoyantFoam": SolverCapability(
        family="compressible-buoyant",
        compressibility="compressible",
        phase_family="single_phase",
        energy="enabled",
    ),
    "chtMultiRegionFoam": SolverCapability(
        family="conjugate-heat-transfer",
        compressibility="compressible",
        phase_family="single_phase",
        energy="enabled",
        physics_family="conjugate_heat_transfer",
    ),
    "shallowWaterFoam": SolverCapability(
        family="shallow-water",
        regime="transient",
        compressibility="incompressible",
        phase_family="single_phase",
        energy="disabled",
        physics_family="shallow_water",
    ),
    "solidDisplacementFoam": SolverCapability(
        family="solid-mechanics",
        physics_family="solid_mechanics",
    ),
    "solidEquilibriumDisplacementFoam": SolverCapability(
        family="solid-mechanics",
        regime="steady",
        energy="disabled",
        physics_family="solid_mechanics",
    ),
    "electrostaticFoam": SolverCapability(
        family="electrostatics",
        regime="steady",
        energy="disabled",
        physics_family="electromagnetics",
    ),
    "mhdFoam": SolverCapability(
        family="magnetohydrodynamics",
        regime="transient",
        compressibility="incompressible",
        phase_family="single_phase",
        physics_family="magnetohydrodynamics",
    ),
}


def capability_for_solver(solver: str | None) -> SolverCapability | None:
    if solver is None:
        return None
    return SOLVER_CAPABILITIES.get(solver)
