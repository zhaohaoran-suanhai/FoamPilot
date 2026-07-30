"""Optional ChatGPT/Codex OAuth model provider."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .base import (
    ModelRequest,
    SchemaOutputError,
    TransportError,
)


T = TypeVar("T", bound=BaseModel)


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


def _stream_output_text(response: object) -> str:
    fragments: list[str] = []
    completed = ""
    for raw_line in response.iter_lines():
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", errors="replace")
        else:
            line = str(raw_line)
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
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
        elif event_type == "response.completed":
            final = event.get("response")
            if isinstance(final, dict):
                completed = _output_text(final) or completed
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
                    raise TransportError(
                        f"Codex stream error {code}: {message}"
                    )
            raise TransportError(
                f"Codex stream ended with event {event_type}"
            )
    return completed or "".join(fragments)


class CodexOAuthModelClient:
    """Minimal optional provider; the core does not import requests."""

    def __init__(
        self,
        *,
        model: str,
        access_token: str,
        account_id: str | None = None,
        base_url: str = "https://chatgpt.com/backend-api/codex",
        timeout_seconds: int = 300,
    ) -> None:
        self.model = model
        self._access_token = access_token
        self.account_id = account_id
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def generate_structured(
        self,
        request: ModelRequest,
        schema: type[T],
    ) -> T:
        try:
            import requests
        except ImportError as error:
            raise TransportError(
                "Codex OAuth provider requires the optional codex extra"
            ) from error
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "foampilot",
        }
        if self.account_id:
            headers["ChatGPT-Account-Id"] = self.account_id
        schema_text = json.dumps(schema.model_json_schema(), sort_keys=True)
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
        try:
            response = requests.post(
                f"{self.base_url}/responses",
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
                stream=True,
            )
            response.raise_for_status()
            text = _stream_output_text(response).strip()
        except (requests.RequestException, json.JSONDecodeError) as error:
            raise TransportError(
                f"Codex transport failed: {type(error).__name__}"
            ) from error
        try:
            return schema.model_validate_json(text)
        except Exception as error:
            raise SchemaOutputError(
                f"Codex output failed {schema.__name__} validation"
            ) from error
