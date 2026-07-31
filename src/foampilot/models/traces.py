"""Sanitized model-attempt traces."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import threading
from typing import Literal, Protocol

from pydantic import Field

from .base import StrictModel


class ModelAttemptTrace(StrictModel):
    purpose: str
    provider: str
    model: str
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    logical_request_id: str
    transport_attempt: int = Field(ge=1)
    started_at: datetime
    finished_at: datetime
    elapsed_seconds: float = Field(ge=0)
    prompt_bytes: int = Field(ge=0)
    output_bytes: int = Field(ge=0)
    http_status: int | None = None
    provider_request_id: str | None = None
    provider_error_code: str | None = None
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
