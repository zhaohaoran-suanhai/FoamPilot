"""不保存 prompt、响应正文、header 或环境值的模型调用 trace。"""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import threading
from typing import Literal, Protocol

from pydantic import Field

from .base import StrictModel


class ModelAttemptTrace(StrictModel):
    schema_version: Literal[2] = 2
    purpose: str
    backend_id: str
    model: str
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    logical_request_id: str
    transport_attempt: int = Field(ge=1)
    backend_ordinal: int = Field(ge=1)
    backend_attempt: int = Field(ge=1)
    switch_reason: str | None = None
    started_at: datetime
    finished_at: datetime
    elapsed_seconds: float = Field(ge=0)
    request_bytes: int = Field(ge=0)
    output_bytes: int = Field(ge=0)
    status_code: int | None = None
    request_id: str | None = None
    error_code: str | None = None
    retryable: bool | None = None
    partial_output_bytes: int = Field(default=0, ge=0)
    deadline_reason: Literal[
        "REQUEST_TIMEOUT",
        "STAGE_DEADLINE",
        "TOTAL_MODEL_DEADLINE",
    ] | None = None


class ModelTraceSink(Protocol):
    def record(self, attempt: ModelAttemptTrace) -> None: ...


class InMemoryModelTraceSink:
    def __init__(self) -> None:
        self.attempts: list[ModelAttemptTrace] = []

    def record(self, attempt: ModelAttemptTrace) -> None:
        self.attempts.append(attempt)


class JsonlModelTraceSink:
    """Append and fsync one complete JSON object per transport attempt."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def record(self, attempt: ModelAttemptTrace) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(attempt.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
