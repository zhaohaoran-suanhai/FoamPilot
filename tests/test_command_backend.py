from __future__ import annotations

import json
from pathlib import Path
from threading import Event, Timer
import time
from types import SimpleNamespace

import pytest

from foampilot.activity import ActivityReporter, OperationCancelled
from foampilot.models import BackendError, BackendFailureKind, ModelRequest
from foampilot.models.command_backend import (
    CommandBackend,
    CommandBackendConfig,
    codex_exec_config,
)


def _request() -> ModelRequest:
    return ModelRequest(
        purpose="generation",
        system_prompt="Return a JSON object.",
        user_prompt="Set answer to seven.",
        response_schema={
            "type": "object",
            "properties": {"answer": {"type": "integer"}},
            "required": ["answer"],
        },
    )


def _fake_executable(tmp_path: Path) -> tuple[Path, Path]:
    executable = tmp_path / "fake-model"
    capture = tmp_path / "capture.json"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

if "--probe" in sys.argv:
    raise SystemExit(0)
schema_path = Path(sys.argv[sys.argv.index("--schema") + 1])
output_path = Path(sys.argv[sys.argv.index("--output") + 1])
capture_path = Path(os.environ["FOAMPILOT_TEST_CAPTURE"])
payload = {
    "argv": sys.argv,
    "stdin": sys.stdin.read(),
    "schema": json.loads(schema_path.read_text(encoding="utf-8")),
    "secret_visible": "FOAMPILOT_TEST_SECRET" in os.environ,
}
capture_path.write_text(json.dumps(payload), encoding="utf-8")
output_path.write_text('{"answer": 7}', encoding="utf-8")
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable, capture


def test_command_backend_uses_fixed_argv_and_output_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, capture = _fake_executable(tmp_path)
    monkeypatch.setenv("FOAMPILOT_TEST_CAPTURE", str(capture))
    monkeypatch.setenv("FOAMPILOT_TEST_SECRET", "must-not-leak")
    backend = CommandBackend(
        CommandBackendConfig(
            backend_id="fake-command",
            model="fake-model",
            argv_template=(
                str(executable),
                "--schema",
                "{schema_file}",
                "--output",
                "{output_file}",
                "--model",
                "{model}",
            ),
            probe_argv=((str(executable), "--probe"),),
            pass_env=("PATH", "FOAMPILOT_TEST_CAPTURE"),
        )
    )

    response = backend.exchange(_request(), timeout_seconds=2)
    recorded = json.loads(capture.read_text(encoding="utf-8"))

    assert response.output_text == '{"answer": 7}'
    assert recorded["argv"][0] == str(executable)
    assert recorded["secret_visible"] is False
    assert recorded["schema"]["properties"]["answer"]["type"] == "integer"
    assert "Return a JSON object." in recorded["stdin"]


def test_command_backend_uses_shared_supervised_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_supervisor(argv, **kwargs):
        observed["argv"] = list(argv)
        observed["kwargs"] = kwargs
        output_index = argv.index("--output") + 1
        Path(argv[output_index]).write_text('{"answer": 7}', encoding="utf-8")
        return SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
            timed_out=False,
        )

    monkeypatch.setattr(
        "foampilot.models.command_backend.run_supervised_process",
        fake_supervisor,
    )
    backend = CommandBackend(
        CommandBackendConfig(
            backend_id="fake-command",
            model="fake-model",
            argv_template=(
                "fake-model",
                "--output",
                "{output_file}",
            ),
            probe_argv=(("fake-model", "--probe"),),
        )
    )

    response = backend.exchange(_request(), timeout_seconds=12)

    assert response.output_text == '{"answer": 7}'
    assert observed["argv"][0] == "fake-model"
    assert observed["kwargs"]["timeout_seconds"] == 12
    assert "系统要求" in observed["kwargs"]["stdin_text"]


def test_codex_preset_never_mentions_auth_files() -> None:
    config = codex_exec_config(model="gpt-test")
    rendered = " ".join(config.argv_template)

    assert "auth.json" not in rendered
    assert "access_token" not in rendered
    assert "--ephemeral" in rendered
    assert "--output-schema" in rendered
    assert "--output-last-message" in rendered
    assert config.probe_argv == (
        ("codex", "--version"),
        ("codex", "login", "status"),
    )


def test_command_backend_rejects_unknown_placeholder() -> None:
    with pytest.raises(ValueError, match="unknown command placeholder"):
        CommandBackendConfig(
            backend_id="unsafe",
            model="test",
            argv_template=("runner", "{auth_file}"),
            probe_argv=(("runner", "--version"),),
        )


def test_command_backend_maps_missing_executable() -> None:
    backend = CommandBackend(
        CommandBackendConfig(
            backend_id="missing",
            model="test",
            argv_template=("/definitely/missing/foampilot-runner",),
            probe_argv=(("/definitely/missing/foampilot-runner",),),
        )
    )

    with pytest.raises(BackendError) as captured:
        backend.exchange(_request(), timeout_seconds=1)

    assert captured.value.kind == BackendFailureKind.BACKEND_UNAVAILABLE
    assert captured.value.retryable is False


def test_command_backend_redacts_error_and_preserves_terminal_cause(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "failing-model"
    executable.write_text(
        """#!/usr/bin/env python3
import sys
sys.stderr.write("START_HEADER " + ("context " * 80))
sys.stderr.write("api_key=very-secret FINAL_NETWORK_CAUSE\\n")
raise SystemExit(7)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    backend = CommandBackend(
        CommandBackendConfig(
            backend_id="failing-command",
            model="test",
            argv_template=(str(executable),),
            probe_argv=((str(executable),),),
        )
    )

    with pytest.raises(BackendError) as captured:
        backend.exchange(_request(), timeout_seconds=2)

    assert "START_HEADER" in captured.value.detail
    assert "FINAL_NETWORK_CAUSE" in captured.value.detail
    assert "[REDACTED]" in captured.value.detail
    assert "very-secret" not in captured.value.detail


def test_command_backend_classifies_invalid_output_schema_without_retry(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "invalid-schema-model"
    executable.write_text(
        """#!/usr/bin/env python3
import sys
sys.stderr.write("HTTP 400 invalid_json_schema: required must include fields\\n")
raise SystemExit(1)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    backend = CommandBackend(
        CommandBackendConfig(
            backend_id="invalid-schema-command",
            model="test",
            argv_template=(str(executable),),
            probe_argv=((str(executable),),),
        )
    )

    with pytest.raises(BackendError) as captured:
        backend.exchange(_request(), timeout_seconds=2)

    assert captured.value.kind == BackendFailureKind.SCHEMA_INVALID
    assert captured.value.retryable is False


def test_command_backend_timeout_kills_descendants_holding_output_pipes(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "model-with-child"
    executable.write_text(
        """#!/usr/bin/env python3
import subprocess
import sys
import time
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
time.sleep(5)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    backend = CommandBackend(
        CommandBackendConfig(
            backend_id="child-command",
            model="test",
            argv_template=(str(executable),),
            probe_argv=((str(executable),),),
        )
    )

    started = time.monotonic()
    with pytest.raises(BackendError) as captured:
        backend.exchange(_request(), timeout_seconds=0.1)
    elapsed = time.monotonic() - started

    assert captured.value.kind == BackendFailureKind.TIMEOUT
    assert captured.value.request_timed_out is True
    assert elapsed < 2


def test_command_backend_propagates_cooperative_cancellation(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "slow-model"
    executable.write_text(
        "#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    backend = CommandBackend(
        CommandBackendConfig(
            backend_id="slow-command",
            model="test",
            argv_template=(str(executable),),
            probe_argv=((str(executable),),),
        )
    )
    cancel = Event()
    reporter = ActivityReporter(
        operation_id="cancel-model",
        cancel_requested=cancel.is_set,
    )
    timer = Timer(0.08, cancel.set)
    timer.start()
    try:
        with pytest.raises(OperationCancelled):
            backend.exchange(
                _request(),
                timeout_seconds=10,
                activity=reporter,
            )
    finally:
        timer.cancel()
