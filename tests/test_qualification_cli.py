from foampilot.cli.main import build_parser


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
