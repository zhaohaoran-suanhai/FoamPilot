import json
from importlib.resources import files

import pytest

from foampilot.qualification.runner import qualification_data_path
from foampilot.tasks import load_task_spec


CASE_IDS = (
    "laminar-cavity",
    "potential-cylinder",
    "rans-pitzdaily",
    "multiphase-dam-break",
    "compressible-shock-tube",
    "buoyant-cavity",
)

DEVELOPMENT_CASE_IDS = (
    "scalar-transport-pitzdaily",
    "laminar-planar-poiseuille",
    "porous-angled-duct",
    "compressible-blocked-channel",
    "cht-cooling-cylinder",
    "srf-rotor",
)

HOLDOUT_CASE_IDS = (
    "mhd-hartmann",
    "multiphase-capillary-rise",
    "solid-plate-hole",
)


def test_official_six_assets_are_complete() -> None:
    data = files("foampilot.qualification").joinpath("data")
    for case_id in CASE_IDS:
        task = data.joinpath("tasks", f"{case_id}.yaml")
        validation = data.joinpath("validation", f"{case_id}.yaml")
        reference = data.joinpath("references", f"{case_id}.json")
        assert task.is_file()
        assert validation.is_file()
        assert reference.is_file()
        assert load_task_spec(task).task_id == case_id


@pytest.mark.parametrize("case_id", DEVELOPMENT_CASE_IDS)
def test_development_task_is_public_and_protected(case_id: str) -> None:
    task = load_task_spec(qualification_data_path("tasks", case_id))

    assert task.task_id == case_id
    assert task.openfoam_target.version == "10"
    assert task.protected_paths == [
        "/home/edwin/workplace/OpenFOAM-10/tutorials"
    ]
    visible = json.dumps(task.agent_payload())
    assert "tutorials/" not in visible
    assert "golden" not in visible.lower()


def test_blocked_channel_uses_an_installed_native_initializer() -> None:
    task = load_task_spec(
        qualification_data_path("tasks", "compressible-blocked-channel")
    )
    initializer = next(
        check
        for check in task.public_checks
        if check.name == "regional-initialization"
    )

    assert initializer.parameters["executable"] == "postProcess"


def test_srf_task_defines_the_rotor_blade_geometry() -> None:
    task = load_task_spec(qualification_data_path("tasks", "srf-rotor"))

    assert "four radial zero-thickness blades" in task.prompt
    assert "hub to the 0.02 m tip radius" in task.prompt


@pytest.mark.parametrize("case_id", HOLDOUT_CASE_IDS)
def test_holdout_task_is_public_and_protected(case_id: str) -> None:
    task = load_task_spec(qualification_data_path("tasks", case_id))

    assert task.task_id == case_id
    assert task.openfoam_target.version == "10"
    assert task.protected_paths == [
        "/home/edwin/workplace/OpenFOAM-10/tutorials"
    ]
    visible = json.dumps(task.agent_payload())
    assert "tutorials/" not in visible
    assert "golden" not in visible.lower()
