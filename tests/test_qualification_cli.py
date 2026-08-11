from foampilot.cli.main import _resolve_runtime, build_parser
from tests.test_runtime_config import _fake_openfoam


def test_official_six_qualification_command_parses() -> None:
    arguments = build_parser().parse_args(
        [
            "qualify",
            "official-six",
            "--run-root",
            "/tmp/foampilot-six",
            "--workers",
            "2",
            "--json",
        ]
    )
    assert arguments.command == "qualify"
    assert arguments.suite == "official-six"
    assert arguments.workers == 2


def test_generic_qualification_suite_command_parses() -> None:
    arguments = build_parser().parse_args(
        [
            "qualify",
            "suite",
            "--suite-file",
            "/tmp/controlled-learning-15-v1.yaml",
            "--run-root",
            "/tmp/foampilot-fifteen",
            "--workers",
            "2",
            "--json",
        ]
    )

    assert arguments.suite == "suite"
    assert str(arguments.suite_file).endswith(
        "controlled-learning-15-v1.yaml"
    )


def test_qualification_runtime_default_is_required_with_default_provenance(
    tmp_path,
    monkeypatch,
) -> None:
    root = _fake_openfoam(tmp_path / "OpenFOAM-10")
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("FOAMPILOT_OPENFOAM_ROOT", str(root))
    monkeypatch.delenv("FOAMPILOT_RUNTIME_CONFIG", raising=False)
    monkeypatch.delenv("FOAMPILOT_EXECUTION_ISOLATION", raising=False)
    arguments = build_parser().parse_args(
        [
            "qualify",
            "official-six",
            "--run-root",
            str(tmp_path / "runs"),
        ]
    )

    resolution = _resolve_runtime(
        arguments,
        default_isolation="sandbox_required",
    )

    assert resolution.config.isolation == "sandbox_required"
    assert (
        resolution.provenance.fields["execution.isolation"].source
        == "default"
    )
