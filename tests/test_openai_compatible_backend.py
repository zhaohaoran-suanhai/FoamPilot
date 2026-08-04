from __future__ import annotations

import json
from urllib import error as urlerror

import pytest

from foampilot.models import BackendError, BackendFailureKind, ModelRequest
from foampilot.models.openai_compatible import (
    OpenAICompatibleBackend,
    OpenAICompatibleConfig,
)


def _request() -> ModelRequest:
    return ModelRequest(
        purpose="generation",
        system_prompt="Return JSON.",
        user_prompt="Set answer to seven.",
        response_schema={"type": "object"},
    )


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.headers = {"x-request-id": "request-test"}
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def getcode(self) -> int:
        return 200

    def read(self) -> bytes:
        return self._payload


def test_openai_compatible_backend_posts_non_streaming_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def exchange(outgoing, *, timeout: float):
        captured["url"] = outgoing.full_url
        captured["authorization"] = outgoing.get_header("Authorization")
        captured["body"] = json.loads(outgoing.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Response(
            {
                "id": "request-test",
                "choices": [{"message": {"content": '{"answer": 7}'}}],
            }
        )

    monkeypatch.setattr(
        "foampilot.models.openai_compatible.urlrequest.urlopen",
        exchange,
    )
    monkeypatch.setenv("LOCAL_TEST_KEY", "secret-test-value")
    backend = OpenAICompatibleBackend(
        OpenAICompatibleConfig(
            backend_id="local-http",
            base_url="http://127.0.0.1:8000/v1",
            model="local-model",
            api_key_env="LOCAL_TEST_KEY",
        )
    )
    response = backend.exchange(_request(), timeout_seconds=2)

    assert response.output_text == '{"answer": 7}'
    assert captured["url"] == "http://127.0.0.1:8000/v1/chat/completions"
    assert captured["authorization"] == "Bearer secret-test-value"
    assert captured["body"]["stream"] is False
    assert captured["timeout"] == 2
    assert "secret-test-value" not in response.model_dump_json()


def test_openai_compatible_backend_maps_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def rate_limited(outgoing, *, timeout: float):
        del timeout
        raise urlerror.HTTPError(
            outgoing.full_url,
            429,
            "busy",
            {"Retry-After": "1"},
            None,
        )

    monkeypatch.setattr(
        "foampilot.models.openai_compatible.urlrequest.urlopen",
        rate_limited,
    )
    backend = OpenAICompatibleBackend(
        OpenAICompatibleConfig(
            backend_id="local-http",
            base_url="http://127.0.0.1:8000/v1",
            model="local-model",
        )
    )
    with pytest.raises(BackendError) as captured:
        backend.exchange(_request(), timeout_seconds=2)

    assert captured.value.kind == BackendFailureKind.RATE_LIMITED
    assert captured.value.retryable is True
    assert captured.value.retry_after_seconds == 1


def test_non_loopback_plain_http_is_rejected() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        OpenAICompatibleConfig(
            backend_id="remote-http",
            base_url="http://models.example.com/v1",
            model="remote-model",
        )


def test_probe_reports_missing_api_key_environment_in_chinese(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISSING_MODEL_KEY", raising=False)
    backend = OpenAICompatibleBackend(
        OpenAICompatibleConfig(
            backend_id="missing-key",
            base_url="http://127.0.0.1:9/v1",
            model="local-model",
            api_key_env="MISSING_MODEL_KEY",
        )
    )

    health = backend.probe(timeout_seconds=0.01)

    assert health.state == "misconfigured"
    assert health.code == "BACKEND_MISCONFIGURED"
    assert health.message == "模型后端配置错误。"
