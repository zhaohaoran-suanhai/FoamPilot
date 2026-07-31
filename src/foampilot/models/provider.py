"""Contracts for exactly one provider HTTP/SSE exchange."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from .base import ModelRequest, StrictModel


class ProviderResponse(StrictModel):
    """Sanitized result of one completed provider exchange."""

    provider: str
    model: str
    purpose: str
    output_text: str
    http_status: int
    provider_request_id: str | None = None
    provider_code: str | None = None
    output_bytes: int = Field(ge=0)
    partial_output_bytes: int = Field(default=0, ge=0)


class ProviderClient(Protocol):
    """A provider client performs one exchange and never retries."""

    provider: str
    model: str
    account_identity_hash: str

    def exchange(
        self,
        request: ModelRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse: ...
