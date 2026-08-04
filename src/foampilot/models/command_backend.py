"""通过固定 argv 调用外部已认证模型运行器。"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
from string import Formatter
import subprocess
import tempfile
import time
from typing import Literal

from pydantic import field_validator

from .backend import BackendHealth, BackendResponse
from .base import ModelRequest, StrictModel
from .errors import BackendError, BackendFailureKind
from .messages_zh import backend_error_payload_zh


_ALLOWED_PLACEHOLDERS = {
    "model",
    "schema_file",
    "output_file",
    "work_dir",
}
_SECRET_PATTERNS = (
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)(?:api[_-]?key|access[_-]?token|auth[_-]?token|password)"
        r"\s*[:=]\s*[^\s,;]+"
    ),
)


class CommandBackendConfig(StrictModel):
    schema_version: Literal[1] = 1
    backend_id: str
    model: str
    argv_template: tuple[str, ...]
    probe_argv: tuple[tuple[str, ...], ...]
    pass_env: tuple[str, ...] = (
        "PATH",
        "HOME",
        "CODEX_HOME",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    )

    @field_validator("argv_template")
    @classmethod
    def validate_argv_template(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not value:
            raise ValueError("argv_template must not be empty")
        formatter = Formatter()
        for argument in value:
            for _, field_name, _, _ in formatter.parse(argument):
                if field_name is None:
                    continue
                if field_name not in _ALLOWED_PLACEHOLDERS:
                    raise ValueError(
                        f"unknown command placeholder: {field_name}"
                    )
        return value

    @field_validator("probe_argv")
    @classmethod
    def validate_probe_argv(
        cls,
        value: tuple[tuple[str, ...], ...],
    ) -> tuple[tuple[str, ...], ...]:
        if not value or any(not command for command in value):
            raise ValueError("probe_argv must contain non-empty commands")
        return value


def _sanitize(value: str) -> str:
    text = " ".join(value.split())
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    if len(text) <= 480:
        return text
    return text[:160] + " ...[truncated]... " + text[-300:]


def _child_environment(names: tuple[str, ...]) -> dict[str, str]:
    return {
        name: os.environ[name]
        for name in names
        if name in os.environ
    }


def _prompt(request: ModelRequest) -> str:
    return (
        "系统要求：\n"
        + request.system_prompt
        + "\n\n用户任务：\n"
        + request.user_prompt
        + "\n\n只返回符合所给 JSON Schema 的 JSON 对象，"
        "不要返回 Markdown 或额外说明。\n"
    )


def _command_failure_kind(detail: str) -> tuple[BackendFailureKind, bool]:
    normalized = detail.casefold()
    if (
        "invalid_json_schema" in normalized
        or "invalid json schema" in normalized
    ):
        return BackendFailureKind.SCHEMA_INVALID, False
    return BackendFailureKind.PROCESS_INTERRUPTED, True


class CommandBackend:
    """完成一次无 shell、无重试的外部命令模型交换。"""

    def __init__(self, config: CommandBackendConfig) -> None:
        self.config = config
        self.backend_id = config.backend_id
        self.model = config.model
        canonical = config.model_dump_json()
        self.identity_hash = sha256(canonical.encode("utf-8")).hexdigest()

    def _error(
        self,
        *,
        kind: BackendFailureKind,
        purpose: str,
        detail: str,
        retryable: bool,
        request_timed_out: bool = False,
    ) -> BackendError:
        return BackendError(
            kind=kind,
            backend_id=self.backend_id,
            model=self.model,
            purpose=purpose,
            detail=_sanitize(detail),
            retryable=retryable,
            request_timed_out=request_timed_out,
        )

    def probe(self, *, timeout_seconds: float) -> BackendHealth:
        started = time.monotonic()
        for index, command in enumerate(self.config.probe_argv):
            try:
                completed = subprocess.run(
                    list(command),
                    shell=False,
                    text=True,
                    capture_output=True,
                    env=_child_environment(self.config.pass_env),
                    timeout=timeout_seconds,
                    check=False,
                )
            except FileNotFoundError as error:
                failure = self._error(
                    kind=BackendFailureKind.BACKEND_UNAVAILABLE,
                    purpose="probe",
                    detail=str(error),
                    retryable=False,
                )
                return self._health(failure, started)
            except subprocess.TimeoutExpired:
                failure = self._error(
                    kind=BackendFailureKind.TIMEOUT,
                    purpose="probe",
                    detail="command probe timed out",
                    retryable=True,
                    request_timed_out=True,
                )
                return self._health(failure, started)
            if completed.returncode != 0:
                kind = (
                    BackendFailureKind.AUTH_FAILED
                    if index > 0
                    else BackendFailureKind.PROCESS_INTERRUPTED
                )
                failure = self._error(
                    kind=kind,
                    purpose="probe",
                    detail=completed.stderr or completed.stdout,
                    retryable=False,
                )
                return self._health(failure, started)
        return BackendHealth(
            backend_id=self.backend_id,
            model=self.model,
            state="available",
            code=None,
            message="模型后端可用。",
            recovery="无需处理。",
            elapsed_seconds=max(time.monotonic() - started, 0),
        )

    def _health(
        self,
        error: BackendError,
        started: float,
    ) -> BackendHealth:
        payload = backend_error_payload_zh(error)
        return BackendHealth(
            backend_id=self.backend_id,
            model=self.model,
            state=(
                "misconfigured"
                if error.kind
                in {
                    BackendFailureKind.BACKEND_MISCONFIGURED,
                    BackendFailureKind.AUTH_FAILED,
                    BackendFailureKind.POLICY_REJECTED,
                }
                else "unavailable"
            ),
            code=error.kind.value,
            message=str(payload["message"]),
            recovery=str(payload["recovery"]),
            elapsed_seconds=max(time.monotonic() - started, 0),
        )

    def exchange(
        self,
        request: ModelRequest,
        *,
        timeout_seconds: float,
    ) -> BackendResponse:
        with tempfile.TemporaryDirectory(
            prefix="foampilot-model-"
        ) as raw_work_dir:
            work_dir = Path(raw_work_dir)
            schema_file = work_dir / "response-schema.json"
            output_file = work_dir / "last-message.json"
            schema_file.write_text(
                json.dumps(
                    request.response_schema,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            replacements = {
                "model": self.model,
                "schema_file": str(schema_file),
                "output_file": str(output_file),
                "work_dir": str(work_dir),
            }
            argv = [
                argument.format_map(replacements)
                for argument in self.config.argv_template
            ]
            try:
                completed = subprocess.run(
                    argv,
                    shell=False,
                    text=True,
                    input=_prompt(request),
                    capture_output=True,
                    cwd=work_dir,
                    env=_child_environment(self.config.pass_env),
                    timeout=timeout_seconds,
                    check=False,
                )
            except FileNotFoundError as error:
                raise self._error(
                    kind=BackendFailureKind.BACKEND_UNAVAILABLE,
                    purpose=request.purpose,
                    detail=str(error),
                    retryable=False,
                ) from error
            except subprocess.TimeoutExpired as error:
                raise self._error(
                    kind=BackendFailureKind.TIMEOUT,
                    purpose=request.purpose,
                    detail="external model command timed out",
                    retryable=True,
                    request_timed_out=True,
                ) from error
            if completed.returncode != 0:
                detail = completed.stderr or completed.stdout
                kind, retryable = _command_failure_kind(detail)
                raise self._error(
                    kind=kind,
                    purpose=request.purpose,
                    detail=detail,
                    retryable=retryable,
                )
            if not output_file.is_file():
                raise self._error(
                    kind=BackendFailureKind.SCHEMA_INVALID,
                    purpose=request.purpose,
                    detail="external command produced no output file",
                    retryable=False,
                )
            output_text = output_file.read_text(encoding="utf-8")
            return BackendResponse(
                backend_id=self.backend_id,
                model=self.model,
                purpose=request.purpose,
                output_text=output_text,
                status_code=completed.returncode,
                output_bytes=len(output_text.encode("utf-8")),
            )


def codex_exec_config(*, model: str) -> CommandBackendConfig:
    """基于 Codex CLI 公开非交互接口构造命令配置。"""

    return CommandBackendConfig(
        backend_id="codex-cli",
        model=model,
        argv_template=(
            "codex",
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--model",
            "{model}",
            "--config",
            'model_reasoning_effort="medium"',
            "--output-schema",
            "{schema_file}",
            "--output-last-message",
            "{output_file}",
            "-",
        ),
        probe_argv=(
            ("codex", "--version"),
            ("codex", "login", "status"),
        ),
    )
