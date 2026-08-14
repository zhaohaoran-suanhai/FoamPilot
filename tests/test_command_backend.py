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
    CommandStateRoot,
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
    assert config.state_root is not None
    assert config.state_root.variable == "CODEX_HOME"
    assert config.state_root.default_home_relative == ".codex"


def test_command_state_root_is_part_of_the_public_model_api() -> None:
    import foampilot.models as model_api

    assert model_api.CommandStateRoot is CommandStateRoot
    assert "CommandStateRoot" in model_api.__all__


def test_command_backend_rejects_unknown_placeholder() -> None:
    with pytest.raises(ValueError, match="unknown command placeholder"):
        CommandBackendConfig(
            backend_id="unsafe",
            model="test",
            argv_template=("runner", "{auth_file}"),
            probe_argv=(("runner", "--version"),),
        )


@pytest.mark.parametrize(
    "default_home_relative",
    ("", "../state", "/tmp/state"),
)
def test_command_state_root_rejects_unsafe_home_relative_default(
    default_home_relative: str,
) -> None:
    with pytest.raises(ValueError):
        CommandStateRoot(
            variable="FOAMPILOT_TEST_STATE_ROOT",
            default_home_relative=default_home_relative,
        )


@pytest.mark.parametrize("variable", ("", "bad-name", "1ROOT"))
def test_command_state_root_rejects_invalid_environment_variable(
    variable: str,
) -> None:
    with pytest.raises(ValueError):
        CommandStateRoot(variable=variable)


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


def test_command_backend_probe_rejects_missing_state_root_before_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing-state-root"
    monkeypatch.setenv("FOAMPILOT_TEST_STATE_ROOT", str(missing))

    def unexpected_run(*args, **kwargs):
        del args, kwargs
        raise AssertionError("probe command must not start")

    monkeypatch.setattr("subprocess.run", unexpected_run)
    backend = CommandBackend(
        CommandBackendConfig(
            backend_id="stateful-command",
            model="test",
            argv_template=("fake-model",),
            probe_argv=(("fake-model", "--probe"),),
            pass_env=("PATH", "FOAMPILOT_TEST_STATE_ROOT"),
            state_root={
                "variable": "FOAMPILOT_TEST_STATE_ROOT",
            },
        )
    )

    health = backend.probe(timeout_seconds=1)

    assert health.state == "misconfigured"
    assert health.code == BackendFailureKind.BACKEND_MISCONFIGURED.value
    assert missing.name not in health.recovery


def test_command_backend_exchange_rejects_missing_state_root_before_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing-state-root"
    monkeypatch.setenv("FOAMPILOT_TEST_STATE_ROOT", str(missing))

    def unexpected_supervisor(*args, **kwargs):
        del args, kwargs
        raise AssertionError("model command must not start")

    monkeypatch.setattr(
        "foampilot.models.command_backend.run_supervised_process",
        unexpected_supervisor,
    )
    backend = CommandBackend(
        CommandBackendConfig(
            backend_id="stateful-command",
            model="test",
            argv_template=("fake-model",),
            probe_argv=(("fake-model", "--probe"),),
            pass_env=("PATH", "FOAMPILOT_TEST_STATE_ROOT"),
            state_root={
                "variable": "FOAMPILOT_TEST_STATE_ROOT",
            },
        )
    )

    with pytest.raises(BackendError) as captured:
        backend.exchange(_request(), timeout_seconds=1)

    assert captured.value.kind == BackendFailureKind.BACKEND_MISCONFIGURED
    assert captured.value.retryable is False


def test_command_backend_probe_accepts_writable_state_root_without_touching_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    opaque_member = state_root / "opaque-existing-member"
    opaque_member.write_text("unchanged", encoding="utf-8")
    monkeypatch.setenv("FOAMPILOT_TEST_STATE_ROOT", str(state_root))
    observed: list[dict[str, str]] = []

    def successful_run(*args, **kwargs):
        del args
        observed.append(dict(kwargs["env"]))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", successful_run)
    backend = CommandBackend(
        CommandBackendConfig(
            backend_id="stateful-command",
            model="test",
            argv_template=("fake-model",),
            probe_argv=(("fake-model", "--probe"),),
            pass_env=("PATH", "FOAMPILOT_TEST_STATE_ROOT"),
            state_root={
                "variable": "FOAMPILOT_TEST_STATE_ROOT",
            },
        )
    )

    health = backend.probe(timeout_seconds=1)

    assert health.state == "available"
    assert observed[0]["FOAMPILOT_TEST_STATE_ROOT"] == str(state_root)
    assert list(state_root.iterdir()) == [opaque_member]
    assert opaque_member.read_text(encoding="utf-8") == "unchanged"


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


@pytest.mark.parametrize(
    ("detail", "expected_kind", "expected_retryable"),
    (
        (
            "failed to initialize in-process app-server client: "
            "Read-only file system (os error 30)",
            BackendFailureKind.BACKEND_MISCONFIGURED,
            False,
        ),
        (
            "Not logged in",
            BackendFailureKind.AUTH_FAILED,
            False,
        ),
        (
            "HTTP 429 rate limit exceeded",
            BackendFailureKind.RATE_LIMITED,
            True,
        ),
        (
            "HTTP 503 service overloaded",
            BackendFailureKind.OVERLOADED,
            True,
        ),
        (
            "failed to connect to websocket; "
            "stream disconnected before completion: Operation not permitted",
            BackendFailureKind.NETWORK_UNAVAILABLE,
            True,
        ),
    ),
)
def test_command_backend_classifies_known_process_failures(
    tmp_path: Path,
    detail: str,
    expected_kind: BackendFailureKind,
    expected_retryable: bool,
) -> None:
    executable = tmp_path / "classified-failure-model"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stderr.write({detail + chr(10)!r})\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    backend = CommandBackend(
        CommandBackendConfig(
            backend_id="classified-failure-command",
            model="test",
            argv_template=(str(executable),),
            probe_argv=((str(executable),),),
        )
    )

    with pytest.raises(BackendError) as captured:
        backend.exchange(_request(), timeout_seconds=2)

    assert captured.value.kind == expected_kind
    assert captured.value.retryable is expected_retryable


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
