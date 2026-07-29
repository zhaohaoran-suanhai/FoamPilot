from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import foampilot
from foampilot.cli.main import main


def test_core_import_does_not_load_foam_agent_modules() -> None:
    assert foampilot.__version__ == "0.1.0"
    project = Path(__file__).parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project / "src")
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import json, sys, foampilot; "
                "forbidden=('src','langchain','faiss','openai','anthropic'); "
                "print(json.dumps([name for name in sys.modules "
                "if any(name == item or name.startswith(item + '.') "
                "for item in forbidden)]))"
            ),
        ],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []


def test_cli_help_is_available(capsys) -> None:
    assert main(["--help"]) == 0
    output = capsys.readouterr().out
    assert "FoamPilot" in output
    assert "preflight" in output
    assert "casespec" not in output
    assert "agent" not in output
