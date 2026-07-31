from __future__ import annotations

from collections.abc import Iterator

import pytest

from foampilot.models import (
    CodexOAuthProviderClient,
    ModelRequest,
    ProviderError,
    ProviderFailureKind,
)


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        lines: list[bytes] | None = None,
        body: str = "",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._lines = lines or []
        self.text = body
        self.closed = False

    def iter_lines(self) -> Iterator[bytes]:
        yield from self._lines

    def close(self) -> None:
        self.closed = True


def _request() -> ModelRequest:
    return ModelRequest(
        purpose="generation",
        system_prompt="Return structured output.",
        user_prompt="Set ok true.",
        response_schema={"type": "object"},
    )


def test_provider_exchange_returns_text_without_schema_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(
        headers={"x-request-id": "req-1"},
        lines=[
            b'data: {"type":"response.output_text.done","text":"not-json"}',
            b"data: [DONE]",
        ],
    )
    monkeypatch.setattr(
        "requests.post",
        lambda *args, **kwargs: response,
    )

    result = CodexOAuthProviderClient(
        model="gpt-test",
        access_token="secret",
    ).exchange(_request(), timeout_seconds=7)

    assert result.output_text == "not-json"
    assert result.provider_request_id == "req-1"
    assert result.output_bytes == len(b"not-json")
    assert response.closed


@pytest.mark.parametrize(
    ("status", "body", "expected_kind", "retryable"),
    [
        (
            429,
            '{"error":{"code":"rate_limit_exceeded","message":"slow"}}',
            ProviderFailureKind.RATE_LIMITED,
            True,
        ),
        (
            401,
            '{"error":{"code":"invalid_token","message":"secret-token"}}',
            ProviderFailureKind.AUTH_FAILED,
            False,
        ),
        (
            403,
            '{"error":{"code":"forbidden","message":"denied"}}',
            ProviderFailureKind.PERMISSION_DENIED,
            False,
        ),
        (
            503,
            '{"error":{"code":"server_is_overloaded","message":"busy"}}',
            ProviderFailureKind.OVERLOADED,
            True,
        ),
    ],
)
def test_provider_classifies_http_failures_and_closes_response(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    body: str,
    expected_kind: ProviderFailureKind,
    retryable: bool,
) -> None:
    response = FakeResponse(status_code=status, body=body)
    monkeypatch.setattr(
        "requests.post",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(ProviderError) as captured:
        CodexOAuthProviderClient(
            model="gpt-test",
            access_token="secret",
        ).exchange(_request(), timeout_seconds=7)

    assert captured.value.kind == expected_kind
    assert captured.value.retryable is retryable
    assert response.closed
    assert "secret-token" not in str(captured.value)


def test_provider_classifies_incomplete_sse_as_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(
        lines=[
            b'data: {"type":"response.output_text.delta","delta":"{"}',
        ]
    )
    monkeypatch.setattr(
        "requests.post",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(ProviderError) as captured:
        CodexOAuthProviderClient(
            model="gpt-test",
            access_token="secret",
        ).exchange(_request(), timeout_seconds=7)

    assert captured.value.kind == ProviderFailureKind.STREAM_INTERRUPTED
    assert captured.value.partial_output_bytes == 1
    assert response.closed


def test_provider_closes_response_when_stream_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import requests

    class TimedOutResponse(FakeResponse):
        def iter_lines(self):
            raise requests.Timeout("timed out")
            yield b""

    response = TimedOutResponse()
    monkeypatch.setattr(
        "requests.post",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(ProviderError) as captured:
        CodexOAuthProviderClient(
            model="gpt-test",
            access_token="secret",
        ).exchange(_request(), timeout_seconds=7)

    assert (
        captured.value.kind
        == ProviderFailureKind.NETWORK_UNAVAILABLE
    )
    assert captured.value.request_timed_out
    assert response.closed


def test_provider_identity_hash_does_not_contain_account_id() -> None:
    client = CodexOAuthProviderClient(
        model="gpt-test",
        access_token="secret",
        account_id="account-sensitive",
    )

    assert len(client.account_identity_hash) == 64
    assert "account-sensitive" not in client.account_identity_hash
