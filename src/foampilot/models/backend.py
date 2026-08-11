"""与具体供应商无关的模型后端契约。"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field

from foampilot.activity import ActivityReporter

from .base import ModelRequest, StrictModel


class BackendResponse(StrictModel):
    """一次完整后端交换的脱敏结果。"""

    backend_id: str
    model: str
    purpose: str
    output_text: str
    status_code: int | None = None
    request_id: str | None = None
    output_bytes: int = Field(ge=0)
    partial_output_bytes: int = Field(default=0, ge=0)


class BackendHealth(StrictModel):
    """一次快速探测的中文可读结果。"""

    backend_id: str
    model: str
    state: Literal["available", "unavailable", "misconfigured"]
    code: str | None = None
    message: str
    recovery: str
    elapsed_seconds: float = Field(ge=0)


class ModelBackend(Protocol):
    """后端只执行一次交换，不负责重试或降级。"""

    backend_id: str
    model: str
    identity_hash: str

    def probe(self, *, timeout_seconds: float) -> BackendHealth: ...

    def exchange(
        self,
        request: ModelRequest,
        *,
        timeout_seconds: float,
        activity: ActivityReporter | None = None,
    ) -> BackendResponse: ...
