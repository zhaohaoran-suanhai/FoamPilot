"""非流式 OpenAI-compatible 模型后端。"""

from __future__ import annotations

from hashlib import sha256
import json
import os
import socket
import time
from typing import Literal
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from pydantic import Field, field_validator

from .backend import BackendHealth, BackendResponse
from .base import ModelRequest, StrictModel
from .errors import BackendError, BackendFailureKind
from .messages_zh import backend_error_payload_zh


class OpenAICompatibleConfig(StrictModel):
    """一个不包含秘密值的 OpenAI-compatible endpoint 配置。"""

    schema_version: Literal[1] = 1
    backend_id: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key_env: str | None = None
    priority: int = 100

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlparse.urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        is_loopback = parsed.hostname.lower() in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
        if parsed.scheme == "http" and not is_loopback:
            raise ValueError("non-loopback model endpoints must use HTTPS")
        if parsed.username or parsed.password:
            raise ValueError("base_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain query or fragment")
        return value.rstrip("/")


def _retry_after(headers: object) -> float | None:
    try:
        raw = headers.get("Retry-After")  # type: ignore[attr-defined]
        return float(raw) if raw is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _kind_for_status(status: int) -> tuple[BackendFailureKind, bool]:
    if status in {401, 403}:
        return BackendFailureKind.AUTH_FAILED, False
    if status == 429:
        return BackendFailureKind.RATE_LIMITED, True
    if status in {502, 503, 529}:
        return BackendFailureKind.OVERLOADED, True
    return BackendFailureKind.NETWORK_UNAVAILABLE, status >= 500


class OpenAICompatibleBackend:
    """通过标准库完成一次无重试 chat-completions 交换。"""

    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self.config = config
        self.backend_id = config.backend_id
        self.model = config.model
        canonical = config.model_dump_json(exclude={"priority"})
        self.identity_hash = sha256(canonical.encode("utf-8")).hexdigest()

    def _api_key(self, *, purpose: str) -> str | None:
        if self.config.api_key_env is None:
            return None
        value = os.environ.get(self.config.api_key_env)
        if value:
            return value
        raise BackendError(
            kind=BackendFailureKind.BACKEND_MISCONFIGURED,
            backend_id=self.backend_id,
            model=self.model,
            purpose=purpose,
            detail="configured API key environment variable is not set",
            retryable=False,
        )

    def _headers(self, *, purpose: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = self._api_key(purpose=purpose)
        if api_key is not None:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _http_error(
        self,
        failure: urlerror.HTTPError,
        *,
        purpose: str,
    ) -> BackendError:
        kind, retryable = _kind_for_status(failure.code)
        return BackendError(
            kind=kind,
            backend_id=self.backend_id,
            model=self.model,
            purpose=purpose,
            detail=f"model endpoint returned HTTP {failure.code}",
            retryable=retryable,
            status_code=failure.code,
            request_id=failure.headers.get("x-request-id"),
            retry_after_seconds=_retry_after(failure.headers),
        )

    def _transport_error(
        self,
        failure: BaseException,
        *,
        purpose: str,
    ) -> BackendError:
        reason = failure.reason if isinstance(failure, urlerror.URLError) else failure
        timed_out = isinstance(reason, (TimeoutError, socket.timeout))
        return BackendError(
            kind=(
                BackendFailureKind.TIMEOUT
                if timed_out
                else BackendFailureKind.NETWORK_UNAVAILABLE
            ),
            backend_id=self.backend_id,
            model=self.model,
            purpose=purpose,
            detail=(
                "model endpoint request timed out"
                if timed_out
                else "model endpoint is unreachable"
            ),
            retryable=True,
            request_timed_out=timed_out,
        )

    def probe(self, *, timeout_seconds: float) -> BackendHealth:
        started = time.monotonic()
        try:
            probe = urlrequest.Request(
                self.config.base_url + "/models",
                headers=self._headers(purpose="probe"),
                method="GET",
            )
            with urlrequest.urlopen(probe, timeout=timeout_seconds):
                pass
        except BackendError as failure:
            return self._health(failure, started)
        except urlerror.HTTPError as failure:
            return self._health(
                self._http_error(failure, purpose="probe"),
                started,
            )
        except (urlerror.URLError, TimeoutError, socket.timeout) as failure:
            return self._health(
                self._transport_error(failure, purpose="probe"),
                started,
            )
        return BackendHealth(
            backend_id=self.backend_id,
            model=self.model,
            state="available",
            message="模型后端可用。",
            recovery="无需处理。",
            elapsed_seconds=max(time.monotonic() - started, 0),
        )

    def _health(
        self,
        failure: BackendError,
        started: float,
    ) -> BackendHealth:
        payload = backend_error_payload_zh(failure)
        return BackendHealth(
            backend_id=self.backend_id,
            model=self.model,
            state=(
                "misconfigured"
                if failure.kind
                in {
                    BackendFailureKind.BACKEND_MISCONFIGURED,
                    BackendFailureKind.AUTH_FAILED,
                    BackendFailureKind.POLICY_REJECTED,
                }
                else "unavailable"
            ),
            code=failure.kind.value,
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
        schema_text = json.dumps(
            request.response_schema,
            separators=(",", ":"),
            sort_keys=True,
        )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {
                    "role": "user",
                    "content": (
                        request.user_prompt
                        + "\n\n只返回一个符合下列 JSON Schema 的 JSON 对象，"
                        "不要返回 Markdown 或额外说明：\n"
                        + schema_text
                    ),
                },
            ],
            "stream": False,
        }
        encoded = json.dumps(body).encode("utf-8")
        outgoing = urlrequest.Request(
            self.config.base_url + "/chat/completions",
            data=encoded,
            headers=self._headers(purpose=request.purpose),
            method="POST",
        )
        try:
            with urlrequest.urlopen(outgoing, timeout=timeout_seconds) as response:
                status = response.getcode()
                request_id = response.headers.get("x-request-id")
                raw = response.read()
        except urlerror.HTTPError as failure:
            raise self._http_error(failure, purpose=request.purpose) from failure
        except (urlerror.URLError, TimeoutError, socket.timeout) as failure:
            raise self._transport_error(
                failure,
                purpose=request.purpose,
            ) from failure

        try:
            payload = json.loads(raw)
            choices = payload["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError("response must contain exactly one choice")
            output_text = choices[0]["message"]["content"]
            if not isinstance(output_text, str):
                raise ValueError("choice message content must be a string")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as failure:
            raise BackendError(
                kind=BackendFailureKind.SCHEMA_INVALID,
                backend_id=self.backend_id,
                model=self.model,
                purpose=request.purpose,
                detail="model endpoint returned an invalid response envelope",
                retryable=False,
                status_code=status,
                request_id=request_id,
            ) from failure

        return BackendResponse(
            backend_id=self.backend_id,
            model=self.model,
            purpose=request.purpose,
            output_text=output_text,
            status_code=status,
            request_id=request_id,
            output_bytes=len(output_text.encode("utf-8")),
        )
