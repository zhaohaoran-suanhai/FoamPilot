from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from foampilot.models import (
    CodexOAuthModelClient,
    ModelRequest,
    ModelRetryPolicy,
    SchemaOutputError,
    TransportError,
    generate_with_retry,
    load_codex_access_token,
)
from pydantic import BaseModel


class Probe(BaseModel):
    ok: bool


class SequenceClient:
    def __init__(self, outcomes: Sequence[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate_structured(self, request, schema):
        del request, schema
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def model_request() -> ModelRequest:
    return ModelRequest(
        purpose="normalize-simulation-request",
        system_prompt="Return a typed public simulation request.",
        user_prompt="Solve a side-driven rectangular enclosure.",
    )


def test_transport_errors_use_configured_retry_delays() -> None:
    expected = Probe(ok=True)
    client = SequenceClient(
        [TransportError("tls eof"), TransportError("tls eof"), expected]
    )
    sleeps: list[float] = []
    result = generate_with_retry(
        client,
        model_request(),
        Probe,
        ModelRetryPolicy(max_attempts=3, delays_seconds=(2, 5)),
        sleep=sleeps.append,
    )
    assert result == expected
    assert client.calls == 3
    assert sleeps == [2, 5]


def test_default_transport_backoff_allows_overload_to_recover() -> None:
    assert ModelRetryPolicy().delays_seconds == (5, 15, 45, 90)


def test_transport_retry_does_not_retry_schema_error() -> None:
    client = SequenceClient([SchemaOutputError("invalid")])
    with pytest.raises(SchemaOutputError):
        generate_with_retry(
            client,
            model_request(),
            Probe,
            ModelRetryPolicy(max_attempts=3, delays_seconds=(2, 5)),
            sleep=lambda _: None,
        )
    assert client.calls == 1


def test_exhausted_transport_error_reports_attempt_count() -> None:
    client = SequenceClient(
        [
            TransportError("first"),
            TransportError("second"),
            TransportError("third"),
        ]
    )
    with pytest.raises(
        TransportError,
        match="after 3 attempts: third",
    ):
        generate_with_retry(
            client,
            model_request(),
            Probe,
            ModelRetryPolicy(max_attempts=3, delays_seconds=(0, 0)),
            sleep=lambda _: None,
        )


def test_codex_auth_reads_nested_tokens_without_logging_token(
    tmp_path: Path,
) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(
        '{"tokens":{"access_token":"secret-value"}}',
        encoding="utf-8",
    )
    assert load_codex_access_token(auth) == "secret-value"


def test_codex_auth_error_does_not_echo_file_contents(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(
        '{"unrelated":"do-not-echo-this"}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as captured:
        load_codex_access_token(auth)
    assert "do-not-echo-this" not in str(captured.value)


class StreamingResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        yield b'data: {"type":"response.created"}'
        yield (
            b'data: {"type":"response.output_text.delta",'
            b'"delta":"{\\\"ok\\\":"}'
        )
        yield (
            b'data: {"type":"response.output_text.delta",'
            b'"delta":"true}"}'
        )
        yield b"data: [DONE]"


class OverloadedStreamingResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        yield b'data: {"type":"response.created"}'
        yield (
            b'data: {"type":"error","error":'
            b'{"type":"service_unavailable_error",'
            b'"code":"server_is_overloaded",'
            b'"message":"Our servers are currently overloaded. '
            b'Please try again later."}}'
        )


def test_codex_oauth_preserves_sse_error_detail(monkeypatch) -> None:
    monkeypatch.setattr(
        "requests.post",
        lambda *args, **kwargs: OverloadedStreamingResponse(),
    )

    client = CodexOAuthModelClient(
        model="gpt-5.6-sol",
        access_token="secret",
    )
    with pytest.raises(
        TransportError,
        match=(
            "server_is_overloaded.*"
            "Our servers are currently overloaded"
        ),
    ):
        client.generate_structured(
            ModelRequest(
                purpose="probe",
                system_prompt="Return structured output.",
                user_prompt="Set ok true.",
            ),
            Probe,
        )


def test_codex_oauth_uses_required_sse_streaming_protocol(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_post(url, *, headers, json, timeout, stream):
        observed.update(
            {
                "url": url,
                "headers": headers,
                "payload": json,
                "timeout": timeout,
                "stream": stream,
            }
        )
        return StreamingResponse()

    monkeypatch.setattr("requests.post", fake_post)
    result = CodexOAuthModelClient(
        model="gpt-5.6-sol",
        access_token="secret",
    ).generate_structured(
        ModelRequest(
            purpose="probe",
            system_prompt="Return structured output.",
            user_prompt="Set ok true.",
        ),
        Probe,
    )

    assert result == Probe(ok=True)
    assert observed["stream"] is True
    assert observed["payload"]["stream"] is True
    assert observed["headers"]["Accept"] == "text/event-stream"
