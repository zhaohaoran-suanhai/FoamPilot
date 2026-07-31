"""Structured, provider-neutral model boundary failures."""

from __future__ import annotations

from enum import StrEnum


class ProviderFailureKind(StrEnum):
    """Stable failure codes exposed above provider-specific clients."""

    OVERLOADED = "PROVIDER_OVERLOADED"
    RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    PERMISSION_DENIED = "PROVIDER_PERMISSION_DENIED"
    NETWORK_UNAVAILABLE = "PROVIDER_NETWORK_UNAVAILABLE"
    STREAM_INTERRUPTED = "PROVIDER_STREAM_INTERRUPTED"
    SCHEMA_INVALID = "PROVIDER_SCHEMA_INVALID"
    UNKNOWN = "PROVIDER_UNKNOWN"


class ProviderError(RuntimeError):
    """One sanitized failure from one provider exchange."""

    def __init__(
        self,
        *,
        kind: ProviderFailureKind,
        provider: str,
        model: str,
        purpose: str,
        detail: str,
        retryable: bool,
        http_status: int | None = None,
        provider_code: str | None = None,
        provider_request_id: str | None = None,
        retry_after_seconds: float | None = None,
        partial_output_bytes: int = 0,
        request_timed_out: bool = False,
    ) -> None:
        super().__init__(detail)
        self.kind = kind
        self.provider = provider
        self.model = model
        self.purpose = purpose
        self.detail = detail
        self.retryable = retryable
        self.http_status = http_status
        self.provider_code = provider_code
        self.provider_request_id = provider_request_id
        self.retry_after_seconds = retry_after_seconds
        self.partial_output_bytes = partial_output_bytes
        self.request_timed_out = request_timed_out
