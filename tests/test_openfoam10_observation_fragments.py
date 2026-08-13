from __future__ import annotations

import json
from pathlib import Path

import pytest

from foampilot.observations import (
    EvidenceStrategy,
    ObservationItem,
    ObservationPlan,
    ObservationScope,
    TimeSelection,
    audit_observation_field_dimensions,
    collect_foundation10_observation_artifacts,
    compile_foundation10_observations,
    verify_observation_field_dimensions,
)
from foampilot.simulation import FactEvidence


def _item(
    kind: str,
    strategy: str,
    *,
    names=("outlet",),
    scope="patch",
    time="history",
):
    quantity = {
        "flow_rate": "volumetric_flow_rate",
        "pressure_difference": "pressure_difference",
    }.get(kind, kind)
    dimension = {
        "flow_rate": "0 3 -1 0 0 0 0",
        "pressure_difference": "0 2 -2 0 0 0 0",
    }.get(kind, "1")
    return ObservationItem(
        observation_id=f"{kind}-item",
        kind=kind,
        quantity=quantity,
        dimension=dimension,
        scope=ObservationScope(kind=scope, names=names),
        time_selection=TimeSelection(kind=time),
        evidence_strategy=EvidenceStrategy(
            kind=strategy,
            collector_id=(f"foundation10.{kind}" if strategy in {"runtime_configuration", "postprocess_command"} else None),
        ),
        provenance=(FactEvidence(kind="user_quote", detail=kind),),
    )


def test_final_field_metric_does_not_inject_runtime_fragment() -> None:
    fragments = compile_foundation10_observations(
        ObservationPlan(items=(_item("flow_rate", "postprocess_command", time="final"),))
    )

    assert [item.path for item in fragments.system_files] == [
        "system/foampilot-observation-flow_rate-item"
    ]
    assert "regionType  patch;" in fragments.system_files[0].content
    assert "name        outlet;" in fragments.system_files[0].content
    assert "fields      (phi);" in fragments.system_files[0].content
    assert "operation   sum;" in fragments.system_files[0].content
    assert fragments.commands[0].executable == "postProcess"
    assert fragments.commands[0].args == [
        "-dict",
        "system/foampilot-observation-flow_rate-item",
        "-latestTime",
    ]


def test_flow_history_fragment_is_system_owned_and_allowlisted() -> None:
    fragments = compile_foundation10_observations(
        ObservationPlan(items=(_item("flow_rate", "runtime_configuration"),))
    )

    assert fragments.system_owned_paths == ("system/foampilot-observations",)
    text = fragments.system_files[0].content
    assert "surfaceFieldValue" in text
    assert "outlet" in text
    assert 'libs        ("libfieldFunctionObjects.so");' in text
    assert "operation   sum;" in text
    assert "fields      (phi);" in text
    for forbidden in ("#codeStream", "#calc", "systemCall", "executeCalls"):
        assert forbidden not in text


def test_unsupported_collector_or_unsafe_scope_is_rejected() -> None:
    item = _item("flow_rate", "runtime_configuration").model_copy(
        update={
            "evidence_strategy": EvidenceStrategy(
                kind="runtime_configuration",
                collector_id="model.arbitrary",
            )
        }
    )
    with pytest.raises(ValueError, match="OBSERVATION_COLLECTOR_UNSUPPORTED"):
        compile_foundation10_observations(ObservationPlan(items=(item,)))


def test_pressure_difference_and_zone_average_have_complete_templates() -> None:
    pressure = _item(
        "pressure_difference",
        "postprocess_command",
        names=("inlet", "outlet"),
        scope="patch_pair",
        time="final",
    )
    average = _item(
        "region_average",
        "postprocess_command",
        names=("porous",),
        scope="cell_zone",
        time="final",
    ).model_copy(
        update={
            "quantity": "velocity",
            "dimension": "0 1 -1 0 0 0 0",
            "scope": ObservationScope(kind="cell_zone", names=("porous",)),
        }
    )

    fragments = compile_foundation10_observations(
        ObservationPlan(items=(pressure, average))
    )
    text = "\n".join(item.content for item in fragments.system_files)

    assert "type        fieldValueDelta;" in text
    assert "name        inlet;" in text
    assert "name        outlet;" in text
    assert "fields      (p);" in text
    assert "type        volFieldValue;" in text
    assert "regionType  cellZone;" in text
    assert "name        porous;" in text
    assert "operation   volAverage;" in text
    assert "fields      (U);" in text


@pytest.mark.parametrize(
    ("quantity", "dimension", "field", "unit"),
    [
        ("temperature", "0 0 0 1 0 0 0", "T", "K"),
        ("kinematic_pressure", "0 2 -2 0 0 0 0", "p", "m2/s2"),
        ("density", "1 -3 0 0 0 0 0", "rho", "kg/m3"),
    ],
)
def test_region_average_field_and_unit_are_selected_from_frozen_contract(
    tmp_path: Path,
    quantity: str,
    dimension: str,
    field: str,
    unit: str,
) -> None:
    item = _item(
        "region_average",
        "postprocess_command",
        names=("porous",),
        scope="cell_zone",
        time="final",
    ).model_copy(update={"quantity": quantity, "dimension": dimension})

    fragments = compile_foundation10_observations(ObservationPlan(items=(item,)))
    assert f"fields      ({field});" in fragments.system_files[0].content

    output = (
        tmp_path
        / "postProcessing"
        / item.observation_id
        / "1"
        / "volFieldValue.dat"
    )
    output.parent.mkdir(parents=True)
    output.write_text("# Time value\n1 2.5\n", encoding="utf-8")
    artifacts = collect_foundation10_observation_artifacts(
        tmp_path,
        ObservationPlan(items=(item,)),
    )
    assert json.loads(artifacts[item.observation_id].read_text())["unit"] == unit


def test_unknown_region_average_contract_is_rejected_before_execution() -> None:
    item = _item(
        "region_average",
        "postprocess_command",
        names=("porous",),
        scope="cell_zone",
        time="final",
    ).model_copy(
        update={"quantity": "arbitrary_field", "dimension": "9 9 9 9 9 9 9"}
    )

    with pytest.raises(ValueError, match="OBSERVATION_QUANTITY_UNSUPPORTED"):
        compile_foundation10_observations(ObservationPlan(items=(item,)))


def test_authored_field_dimensions_are_verified_before_collection(
    tmp_path: Path,
) -> None:
    item = _item(
        "flow_rate",
        "postprocess_command",
        time="final",
    )
    plan = ObservationPlan(items=(item,))
    flux = tmp_path / "0" / "phi"
    flux.parent.mkdir(parents=True)
    flux.write_text(
        "FoamFile{}\ndimensions [0 3 -1 0 0 0 0];\n",
        encoding="utf-8",
    )
    verify_observation_field_dimensions(tmp_path, plan)

    flux.write_text(
        "FoamFile{}\ndimensions [1 0 -1 0 0 0 0];\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="OBSERVATION_FIELD_DIMENSION_MISMATCH"):
        verify_observation_field_dimensions(tmp_path, plan)


def test_large_field_body_does_not_block_bounded_header_verification(
    tmp_path: Path,
) -> None:
    item = _item("flow_rate", "postprocess_command", time="final")
    flux = tmp_path / "0" / "phi"
    flux.parent.mkdir(parents=True)
    with flux.open("wb") as stream:
        stream.write(b"FoamFile{}\ndimensions [0 3 -1 0 0 0 0];\n")
        stream.seek(40 * 1024 * 1024)
        stream.write(b"\n")

    verify_observation_field_dimensions(
        tmp_path,
        ObservationPlan(items=(item,)),
    )


def test_field_dimension_audit_is_isolated_by_observation(tmp_path: Path) -> None:
    flow = _item("flow_rate", "postprocess_command", time="final")
    pressure = _item(
        "pressure_difference",
        "postprocess_command",
        names=("inlet", "outlet"),
        scope="patch_pair",
        time="final",
    )
    pressure_field = tmp_path / "1" / "p"
    pressure_field.parent.mkdir(parents=True)
    pressure_field.write_text(
        "FoamFile{}\ndimensions [0 2 -2 0 0 0 0];\n",
        encoding="utf-8",
    )

    failures = audit_observation_field_dimensions(
        tmp_path,
        ObservationPlan(items=(flow, pressure)),
    )

    assert set(failures) == {flow.observation_id}
    assert "HEADER_MISSING" in failures[flow.observation_id]


def test_field_dimension_verification_uses_bound_region(tmp_path: Path) -> None:
    base = _item(
        "region_average",
        "postprocess_command",
        time="final",
    )
    item = base.model_copy(
        update={
            "quantity": "temperature",
            "dimension": "0 0 0 1 0 0 0",
            "scope": ObservationScope(
                kind="region",
                names=("solid",),
                region="solid",
            ),
        }
    )
    field = tmp_path / "solid" / "0" / "T"
    field.parent.mkdir(parents=True)
    field.write_text(
        "FoamFile{}\ndimensions [0 0 0 1 0 0 0];\n",
        encoding="utf-8",
    )
    verify_observation_field_dimensions(tmp_path, ObservationPlan(items=(item,)))


def test_named_region_is_bound_in_dictionary_command_and_output_path(
    tmp_path: Path,
) -> None:
    item = _item(
        "region_average",
        "postprocess_command",
        time="final",
    ).model_copy(
        update={
            "quantity": "temperature",
            "dimension": "0 0 0 1 0 0 0",
            "scope": ObservationScope(
                kind="region",
                names=("solid",),
                region="solid",
            ),
        }
    )
    plan = ObservationPlan(items=(item,))

    fragments = compile_foundation10_observations(plan)

    assert "region      solid;" in fragments.system_files[0].content
    assert fragments.commands[0].args == [
        "-dict",
        "system/foampilot-observation-region_average-item",
        "-region",
        "solid",
        "-latestTime",
    ]
    output = (
        tmp_path
        / "postProcessing"
        / "solid"
        / item.observation_id
        / "1"
        / "volFieldValue.dat"
    )
    output.parent.mkdir(parents=True)
    output.write_text("# Time average\n1 300\n", encoding="utf-8")
    artifacts = collect_foundation10_observation_artifacts(tmp_path, plan)
    assert item.observation_id in artifacts


def test_patch_region_binding_is_applied_to_dictionary_command_and_output(
    tmp_path: Path,
) -> None:
    item = _item(
        "flow_rate",
        "postprocess_command",
        time="final",
    ).model_copy(
        update={
            "scope": ObservationScope(
                kind="patch",
                names=("inlet",),
                region="fluid",
            )
        }
    )
    plan = ObservationPlan(items=(item,))
    fragments = compile_foundation10_observations(plan)

    assert "region      fluid;" in fragments.system_files[0].content
    assert fragments.commands[0].args == [
        "-dict",
        "system/foampilot-observation-flow_rate-item",
        "-region",
        "fluid",
        "-latestTime",
    ]


def test_time_range_compiles_and_filters_declared_output(tmp_path: Path) -> None:
    item = _item(
        "flow_rate",
        "postprocess_command",
        time="final",
    ).model_copy(
        update={"time_selection": TimeSelection(kind="time_range", start=1, end=2)}
    )
    plan = ObservationPlan(items=(item,))
    fragments = compile_foundation10_observations(plan)
    assert fragments.commands[0].args[-2:] == ["-time", "1:2"]

    output = (
        tmp_path
        / "postProcessing"
        / item.observation_id
        / "0"
        / "surfaceFieldValue.dat"
    )
    output.parent.mkdir(parents=True)
    output.write_text(
        "# Time sum(phi)\n0 -1\n1 -2\n2 -3\n3 -4\n",
        encoding="utf-8",
    )
    artifacts = collect_foundation10_observation_artifacts(tmp_path, plan)
    assert json.loads(artifacts[item.observation_id].read_text())["samples"] == [
        {"time": 1.0, "value": -2.0},
        {"time": 2.0, "value": -3.0},
    ]


def test_dotted_observation_id_has_a_valid_system_owned_path() -> None:
    item = _item(
        "flow_rate",
        "postprocess_command",
        time="final",
    ).model_copy(update={"observation_id": "inlet.flow"})
    fragments = compile_foundation10_observations(ObservationPlan(items=(item,)))
    assert fragments.system_files[0].path == "system/foampilot-observation-inlet.flow"


def test_collector_reads_bounded_history_and_numeric_time_order(tmp_path: Path) -> None:
    item = _item("flow_rate", "runtime_configuration")
    plan = ObservationPlan(items=(item,))
    for time in ("10", "2"):
        output = (
            tmp_path
            / "postProcessing"
            / item.observation_id
            / time
            / "surfaceFieldValue.dat"
        )
        output.parent.mkdir(parents=True)
        start = 1001 if time == "10" else 0
        output.write_text(
            "# Time sum(phi)\n"
            + "".join(f"{index} {-index}\n" for index in range(start, start + 1001)),
            encoding="utf-8",
        )

    artifacts = collect_foundation10_observation_artifacts(tmp_path, plan)
    payload = json.loads(artifacts[item.observation_id].read_text())
    samples = payload["samples"]
    assert len(samples) == 1000
    assert samples[-1]["time"] == 2001.0
    assert payload["status"] == "PARTIAL"
    assert "1000" in payload["detail"]


def test_declared_openfoam_outputs_are_normalized_once(tmp_path: Path) -> None:
    plan = ObservationPlan(
        items=(
            _item("flow_rate", "postprocess_command", time="final"),
            _item(
                "pressure_difference",
                    "postprocess_command",
                    names=("inlet", "outlet"),
                    scope="patch_pair",
                    time="final",
                ),
            _item(
                "region_average",
                    "postprocess_command",
                    names=("porous",),
                    scope="cell_zone",
                    time="final",
            ).model_copy(
                update={
                    "quantity": "velocity",
                    "dimension": "0 1 -1 0 0 0 0",
                    "scope": ObservationScope(kind="cell_zone", names=("porous",)),
                }
            ),
        )
    )
    outputs = {
        "flow_rate-item": ("surfaceFieldValue.dat", "# Time sum(phi)\n1 -0.1\n"),
        "pressure_difference-item": (
            "fieldValueDelta.dat",
            "# Time subtract(areaAverage(p),areaAverage(p))\n1 0.25\n",
        ),
        "region_average-item": (
            "volFieldValue.dat",
            "# Time volAverage(U)\n1 (0.08 0 0)\n",
        ),
    }
    for observation_id, (filename, content) in outputs.items():
        path = tmp_path / "postProcessing" / observation_id / "0" / filename
        path.parent.mkdir(parents=True)
        path.write_text(content, encoding="utf-8")

    artifacts = collect_foundation10_observation_artifacts(tmp_path, plan)

    assert set(artifacts) == {
        "flow_rate-item",
        "pressure_difference-item",
        "region_average-item",
    }
    assert json.loads(artifacts["flow_rate-item"].read_text())["samples"] == [
        {"time": 1.0, "value": -0.1}
    ]
    assert json.loads(artifacts["region_average-item"].read_text())["samples"] == [
        {"time": 1.0, "value": [0.08, 0.0, 0.0]}
    ]


@pytest.mark.parametrize("bad_token", ["1e309", "-1e309"])
def test_nonfinite_declared_output_becomes_unavailable(
    tmp_path: Path,
    bad_token: str,
) -> None:
    item = _item("flow_rate", "postprocess_command", time="final")
    output = (
        tmp_path
        / "postProcessing"
        / item.observation_id
        / "0"
        / "surfaceFieldValue.dat"
    )
    output.parent.mkdir(parents=True)
    output.write_text(f"# Time value\n1 {bad_token}\n", encoding="utf-8")

    artifact = collect_foundation10_observation_artifacts(
        tmp_path,
        ObservationPlan(items=(item,)),
    )[item.observation_id]
    payload = json.loads(artifact.read_text())
    assert payload["status"] == "UNAVAILABLE"
    assert "PARSE_FAILED" in payload["detail"]


def test_collector_rejects_symlinked_internal_destination(tmp_path: Path) -> None:
    item = _item("flow_rate", "postprocess_command", time="final")
    output = (
        tmp_path
        / "postProcessing"
        / item.observation_id
        / "0"
        / "surfaceFieldValue.dat"
    )
    output.parent.mkdir(parents=True)
    output.write_text("# Time value\n1 -0.1\n", encoding="utf-8")
    external = tmp_path.parent / f"{tmp_path.name}-external"
    external.mkdir()
    internal = tmp_path / ".foampilot"
    internal.mkdir()
    (internal / "observations").symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="OBSERVATION_DESTINATION_UNSAFE"):
        collect_foundation10_observation_artifacts(
            tmp_path,
            ObservationPlan(items=(item,)),
        )

    assert tuple(external.iterdir()) == ()


def test_collector_rejects_symlinked_internal_parent(tmp_path: Path) -> None:
    item = _item("flow_rate", "postprocess_command", time="final")
    output = (
        tmp_path
        / "postProcessing"
        / item.observation_id
        / "0"
        / "surfaceFieldValue.dat"
    )
    output.parent.mkdir(parents=True)
    output.write_text("# Time value\n1 -0.1\n", encoding="utf-8")
    external = tmp_path.parent / f"{tmp_path.name}-parent-external"
    external.mkdir()
    (tmp_path / ".foampilot").symlink_to(external, target_is_directory=True)

    with pytest.raises(ValueError, match="OBSERVATION_DESTINATION_UNSAFE"):
        collect_foundation10_observation_artifacts(
            tmp_path,
            ObservationPlan(items=(item,)),
        )

    assert tuple(external.iterdir()) == ()


def test_collector_rejects_partially_numeric_value_tokens(tmp_path: Path) -> None:
    item = _item("flow_rate", "postprocess_command", time="final")
    output = (
        tmp_path
        / "postProcessing"
        / item.observation_id
        / "0"
        / "surfaceFieldValue.dat"
    )
    output.parent.mkdir(parents=True)
    output.write_text("# Time value\n1 -0.1junk\n", encoding="utf-8")

    artifact = collect_foundation10_observation_artifacts(
        tmp_path,
        ObservationPlan(items=(item,)),
    )[item.observation_id]

    payload = json.loads(artifact.read_text())
    assert payload["status"] == "UNAVAILABLE"
    assert "PARSE_FAILED" in payload["detail"]


def test_collector_rejects_symlinked_observation_artifact(tmp_path: Path) -> None:
    item = _item("flow_rate", "postprocess_command", time="final")
    output = (
        tmp_path
        / "postProcessing"
        / item.observation_id
        / "0"
        / "surfaceFieldValue.dat"
    )
    output.parent.mkdir(parents=True)
    output.write_text("# Time value\n1 -0.1\n", encoding="utf-8")
    destination = tmp_path / ".foampilot/observations"
    destination.mkdir(parents=True)
    external = tmp_path.parent / f"{tmp_path.name}-artifact-external.json"
    external.write_text("sentinel\n", encoding="utf-8")
    (destination / f"{item.observation_id}.json").symlink_to(external)

    with pytest.raises(ValueError, match="OBSERVATION_DESTINATION_UNSAFE"):
        collect_foundation10_observation_artifacts(
            tmp_path,
            ObservationPlan(items=(item,)),
        )

    assert external.read_text(encoding="utf-8") == "sentinel\n"
