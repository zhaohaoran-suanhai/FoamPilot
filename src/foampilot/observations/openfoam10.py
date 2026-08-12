"""System-owned Foundation OpenFOAM 10 observation fragments."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from foampilot.authoring import CaseAuthoringError, CaseBundle
from foampilot.plans import GeneratedFile, NativeCommand

from .models import ObservationItem, ObservationPlan


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObservationConfigFragment(_StrictFrozenModel):
    path: Literal["system/foampilot-observations"] = "system/foampilot-observations"
    content: str


class CompiledObservationFragments(_StrictFrozenModel):
    system_files: tuple[ObservationConfigFragment, ...] = ()
    commands: tuple[NativeCommand, ...] = ()
    system_owned_paths: tuple[str, ...] = ()


_RUNTIME_TYPES = {
    "flow_rate": "surfaceFieldValue",
    "pressure_difference": "surfaceFieldValue",
    "region_average": "volFieldValue",
    "force": "forces",
    "heat_flux": "wallHeatFlux",
}
_POSTPROCESS_FUNCTIONS = {
    "flow_rate": "surfaceFieldValue",
    "pressure_difference": "surfaceFieldValue",
    "region_average": "volFieldValue",
    "force": "forces",
    "heat_flux": "wallHeatFlux",
}


def _expected_collector(item: ObservationItem) -> str:
    return f"foundation10.{item.kind}"


def _runtime_block(item: ObservationItem) -> str:
    collector = item.evidence_strategy.collector_id
    if collector != _expected_collector(item) or item.kind not in _RUNTIME_TYPES:
        raise ValueError(
            f"OBSERVATION_COLLECTOR_UNSUPPORTED: {collector or item.kind}"
        )
    type_name = _RUNTIME_TYPES[item.kind]
    names = " ".join(item.scope.names)
    source_kind = {
        "patch": "patch",
        "patch_pair": "patch",
        "cell_zone": "cellZone",
        "region": "all",
        "global": "all",
    }[item.scope.kind]
    source_line = (
        f"    regionType {source_kind};\n    name ({names});\n"
        if names
        else ""
    )
    return (
        f"{item.observation_id}\n{{\n"
        f"    type {type_name};\n"
        "    libs (fieldFunctionObjects);\n"
        "    writeControl timeStep;\n"
        "    writeInterval 1;\n"
        f"{source_line}"
        "}\n"
    )


def _postprocess_command(item: ObservationItem) -> NativeCommand:
    collector = item.evidence_strategy.collector_id
    function = _POSTPROCESS_FUNCTIONS.get(item.kind)
    if collector != _expected_collector(item) or function is None:
        raise ValueError(
            f"OBSERVATION_COLLECTOR_UNSUPPORTED: {collector or item.kind}"
        )
    return NativeCommand(
        step_id=f"observe-{item.observation_id}".replace(".", "-").replace("_", "-"),
        stage="postprocess",
        executable="postProcess",
        args=["-func", function],
        mpi_ranks=1,
        timeout_seconds=20,
    )


def compile_foundation10_observations(
    plan: ObservationPlan,
) -> CompiledObservationFragments:
    blocks: list[str] = []
    commands: list[NativeCommand] = []
    for item in plan.items:
        strategy = item.evidence_strategy.kind
        if strategy == "runtime_configuration":
            blocks.append(_runtime_block(item))
        elif strategy == "postprocess_command":
            commands.append(_postprocess_command(item))
    system_files: tuple[ObservationConfigFragment, ...] = ()
    owned: tuple[str, ...] = ()
    if blocks:
        system_files = (
            ObservationConfigFragment(
                content="functions\n{\n" + "\n".join(blocks) + "}\n",
            ),
        )
        owned = ("system/foampilot-observations",)
    return CompiledObservationFragments(
        system_files=system_files,
        commands=tuple(commands),
        system_owned_paths=owned,
    )


def inject_observation_fragments(
    bundle: CaseBundle,
    plan: ObservationPlan,
) -> tuple[CaseBundle, CompiledObservationFragments]:
    fragments = compile_foundation10_observations(plan)
    existing = {item.path for item in bundle.files}
    collisions = sorted(existing & set(fragments.system_owned_paths))
    if collisions:
        raise CaseAuthoringError(
            "OBSERVATION_SYSTEM_PATH_COLLISION: " + ", ".join(collisions)
        )
    files = list(bundle.files)
    if fragments.system_files:
        control_index = next(
            (index for index, item in enumerate(files) if item.path == "system/controlDict"),
            None,
        )
        if control_index is None:
            raise CaseAuthoringError("OBSERVATION_CONTROL_DICT_MISSING")
        control = files[control_index]
        if "foampilot-observations" in control.content:
            raise CaseAuthoringError("OBSERVATION_INCLUDE_COLLISION")
        files[control_index] = control.model_copy(
            update={
                "content": control.content.rstrip()
                + '\n#include "foampilot-observations"\n'
            }
        )
        files.extend(
            GeneratedFile(path=item.path, content=item.content)
            for item in fragments.system_files
        )
    return bundle.model_copy(update={"files": files}), fragments


__all__ = [
    "CompiledObservationFragments",
    "ObservationConfigFragment",
    "compile_foundation10_observations",
    "inject_observation_fragments",
]
