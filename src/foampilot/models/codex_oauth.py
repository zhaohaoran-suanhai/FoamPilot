"""Optional ChatGPT/Codex OAuth model provider."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from .base import ModelRequest
from .errors import ProviderError, ProviderFailureKind
from .provider import ProviderResponse

def load_codex_access_token(path: str | Path) -> str:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    candidates: list[str] = []
    if isinstance(payload, dict):
        for key in ("access_token", "token"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
        for key in ("tokens", "auth", "credentials", "session"):
            nested = payload.get(key)
            if not isinstance(nested, dict):
                continue
            for token_key in ("access_token", "token"):
                value = nested.get(token_key)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())
    if not candidates:
        raise ValueError(
            f"Codex credential file has no supported access token: {source}"
        )
    return candidates[0]


def _output_text(payload: dict[str, object]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str):
        return direct
    fragments: list[str] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and isinstance(block.get("text"), str)
                ):
                    fragments.append(block["text"])
    return "".join(fragments)


class _StreamFailure(RuntimeError):
    def __init__(
        self,
        *,
        code: str | None,
        message: str,
        partial_output_bytes: int,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.partial_output_bytes = partial_output_bytes


def _stream_output_text(response: object) -> tuple[str, bool]:
    fragments: list[str] = []
    completed = ""
    complete = False
    for raw_line in response.iter_lines():
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", errors="replace")
        else:
            line = str(raw_line)
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            complete = True
            continue
        event = json.loads(data)
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                fragments.append(delta)
        elif event_type == "response.output_text.done":
            text = event.get("text")
            if isinstance(text, str):
                completed = text
                complete = True
        elif event_type == "response.completed":
            final = event.get("response")
            if isinstance(final, dict):
                completed = _output_text(final) or completed
            complete = True
        elif event_type in {"error", "response.failed"}:
            detail = event.get("error")
            if not isinstance(detail, dict):
                response_detail = event.get("response")
                if isinstance(response_detail, dict):
                    detail = response_detail.get("error")
            if isinstance(detail, dict):
                code = detail.get("code") or detail.get("type")
                message = detail.get("message")
                if isinstance(code, str) and isinstance(message, str):
                    raise _StreamFailure(
                        code=code,
                        message=message,
                        partial_output_bytes=len(
                            "".join(fragments).encode("utf-8")
                        ),
                    )
            raise _StreamFailure(
                code=str(event_type),
                message=f"Codex stream ended with event {event_type}",
                partial_output_bytes=len(
                    "".join(fragments).encode("utf-8")
                ),
            )
    return completed or "".join(fragments), complete


def _provider_error_payload(response: object) -> tuple[str | None, str]:
    text = str(getattr(response, "text", "") or "")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None, ""
    if not isinstance(payload, dict):
        return None, ""
    detail = payload.get("error")
    if not isinstance(detail, dict):
        return None, ""
    code = detail.get("code") or detail.get("type")
    message = detail.get("message")
    return (
        str(code) if code is not None else None,
        str(message) if message is not None else "",
    )


def _http_failure_kind(
    status: int,
    provider_code: str | None,
) -> tuple[ProviderFailureKind, bool]:
    code = (provider_code or "").lower()
    if status == 401:
        return ProviderFailureKind.AUTH_FAILED, False
    if status == 403:
        return ProviderFailureKind.PERMISSION_DENIED, False
    if status == 429:
        return ProviderFailureKind.RATE_LIMITED, True
    if "overload" in code or "service_unavailable" in code:
        return ProviderFailureKind.OVERLOADED, True
    if status >= 500:
        return ProviderFailureKind.NETWORK_UNAVAILABLE, True
    return ProviderFailureKind.UNKNOWN, False


def _retry_after_seconds(response: object) -> float | None:
    headers = getattr(response, "headers", {})
    if not hasattr(headers, "get"):
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _request_id(response: object) -> str | None:
    headers = getattr(response, "headers", {})
    if not hasattr(headers, "get"):
        return None
    for name in ("x-request-id", "request-id", "openai-request-id"):
        value = headers.get(name)
        if isinstance(value, str) and value:
            return value
    return None


def _stream_failure_kind(code: str | None) -> ProviderFailureKind:
    normalized = (code or "").lower()
    if "overload" in normalized or "service_unavailable" in normalized:
        return ProviderFailureKind.OVERLOADED
    return ProviderFailureKind.STREAM_INTERRUPTED


class CodexOAuthProviderClient:
    """One Codex OAuth HTTP/SSE exchange without retry or schema validation."""

    provider = "codex-oauth"

    def __init__(
        self,
        *,
        model: str,
        access_token: str,
        account_id: str | None = None,
        base_url: str = "https://chatgpt.com/backend-api/codex",
    ) -> None:
        self.model = model
        self._access_token = access_token
        self.account_id = account_id
        self.base_url = base_url.rstrip("/")
        identity = account_id or "suite-default"
        self.account_identity_hash = sha256(
            f"{self.provider}\0{identity}".encode("utf-8")
        ).hexdigest()

    def exchange(
        self,
        request: ModelRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        try:
            import requests
        except ImportError as error:
            raise ProviderError(
                kind=ProviderFailureKind.NETWORK_UNAVAILABLE,
                provider=self.provider,
                model=self.model,
                purpose=request.purpose,
                detail="Codex OAuth provider requires the codex extra",
                retryable=False,
            ) from error
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "foampilot",
        }
        if self.account_id:
            headers["ChatGPT-Account-Id"] = self.account_id
        schema_text = json.dumps(request.response_schema, sort_keys=True)
        payload = {
            "model": self.model,
            "instructions": (
                f"{request.system_prompt}\nReturn only JSON matching this "
                f"schema:\n{schema_text}"
            ),
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": request.user_prompt,
                        }
                    ],
                }
            ],
            "tools": [],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "reasoning": {"summary": "auto"},
            "store": False,
            "stream": True,
        }
        response: object | None = None
        try:
            response = requests.post(
                f"{self.base_url}/responses",
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
                stream=True,
            )
            status = int(getattr(response, "status_code", 0))
            request_id = _request_id(response)
            if status < 200 or status >= 300:
                provider_code, _ = _provider_error_payload(response)
                kind, retryable = _http_failure_kind(
                    status,
                    provider_code,
                )
                raise ProviderError(
                    kind=kind,
                    provider=self.provider,
                    model=self.model,
                    purpose=request.purpose,
                    detail=(
                        f"Codex provider returned HTTP {status}"
                        + (
                            f" ({provider_code})"
                            if provider_code
                            else ""
                        )
                    ),
                    retryable=retryable,
                    http_status=status,
                    provider_code=provider_code,
                    provider_request_id=request_id,
                    retry_after_seconds=_retry_after_seconds(response),
                )
            try:
                text, complete = _stream_output_text(response)
            except _StreamFailure as error:
                kind = _stream_failure_kind(error.code)
                raise ProviderError(
                    kind=kind,
                    provider=self.provider,
                    model=self.model,
                    purpose=request.purpose,
                    detail=(
                        f"Codex stream error {error.code}: "
                        f"{error.message}"
                    ),
                    retryable=True,
                    http_status=status,
                    provider_code=error.code,
                    provider_request_id=request_id,
                    partial_output_bytes=error.partial_output_bytes,
                ) from error
            if not complete:
                raise ProviderError(
                    kind=ProviderFailureKind.STREAM_INTERRUPTED,
                    provider=self.provider,
                    model=self.model,
                    purpose=request.purpose,
                    detail="Codex stream ended before completion",
                    retryable=True,
                    http_status=status,
                    provider_request_id=request_id,
                    partial_output_bytes=len(text.encode("utf-8")),
                )
            return ProviderResponse(
                provider=self.provider,
                model=self.model,
                purpose=request.purpose,
                output_text=text.strip(),
                http_status=status,
                provider_request_id=request_id,
                output_bytes=len(text.strip().encode("utf-8")),
            )
        except ProviderError:
            raise
        except requests.RequestException as error:
            raise ProviderError(
                kind=ProviderFailureKind.NETWORK_UNAVAILABLE,
                provider=self.provider,
                model=self.model,
                purpose=request.purpose,
                detail=f"Codex transport failed: {type(error).__name__}",
                retryable=True,
                request_timed_out=isinstance(error, requests.Timeout),
            ) from error
        except json.JSONDecodeError as error:
            raise ProviderError(
                kind=ProviderFailureKind.STREAM_INTERRUPTED,
                provider=self.provider,
                model=self.model,
                purpose=request.purpose,
                detail="Codex stream contained invalid JSON",
                retryable=True,
            ) from error
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
