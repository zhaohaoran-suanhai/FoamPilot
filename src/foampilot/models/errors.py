"""与具体模型后端无关的结构化失败。"""

from __future__ import annotations

from enum import StrEnum


class BackendFailureKind(StrEnum):
    """模型边界对外暴露的稳定错误码。"""

    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    BACKEND_MISCONFIGURED = "BACKEND_MISCONFIGURED"
    AUTH_FAILED = "AUTH_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    OVERLOADED = "OVERLOADED"
    NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    PROCESS_INTERRUPTED = "PROCESS_INTERRUPTED"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    POLICY_REJECTED = "POLICY_REJECTED"


class BackendError(RuntimeError):
    """一次后端交换产生的脱敏错误。"""

    def __init__(
        self,
        *,
        kind: BackendFailureKind,
        backend_id: str,
        model: str,
        purpose: str,
        detail: str,
        retryable: bool,
        status_code: int | None = None,
        request_id: str | None = None,
        retry_after_seconds: float | None = None,
        partial_output_bytes: int = 0,
        request_timed_out: bool = False,
        allows_schema_correction: bool = False,
    ) -> None:
        super().__init__(detail)
        self.kind = kind
        self.backend_id = backend_id
        self.model = model
        self.purpose = purpose
        self.detail = detail
        self.retryable = retryable
        self.status_code = status_code
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds
        self.partial_output_bytes = partial_output_bytes
        self.request_timed_out = request_timed_out
        self.allows_schema_correction = allows_schema_correction
