from importlib.resources import files

from foampilot.tasks import load_task_spec


CASE_IDS = (
    "laminar-cavity",
    "potential-cylinder",
    "rans-pitzdaily",
    "multiphase-dam-break",
    "compressible-shock-tube",
    "buoyant-cavity",
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
