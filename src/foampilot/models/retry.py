"""Transport retry policy independent of any model SDK."""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import (
    ModelClient,
    ModelRequest,
    SchemaOutputError,
    TransportError,
)


T = TypeVar("T", bound=BaseModel)


class ModelRetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=5, ge=1)
    delays_seconds: tuple[float, ...] = (2, 5, 15, 30)

    @model_validator(mode="after")
    def delay_count_matches_attempts(self) -> "ModelRetryPolicy":
        if len(self.delays_seconds) != self.max_attempts - 1:
            raise ValueError(
                "delays_seconds must contain max_attempts - 1 values"
            )
        if any(delay < 0 for delay in self.delays_seconds):
            raise ValueError("retry delays must be non-negative")
        return self


def generate_with_retry(
    client: ModelClient,
    request: ModelRequest,
    schema: type[T],
    policy: ModelRetryPolicy | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    active = policy or ModelRetryPolicy()
    for attempt in range(1, active.max_attempts + 1):
        try:
            result = client.generate_structured(request, schema)
            if isinstance(result, schema):
                return result
            try:
                return schema.model_validate(result)
            except Exception as error:
                raise SchemaOutputError(
                    f"model output failed {schema.__name__} validation"
                ) from error
        except TransportError as error:
            if attempt >= active.max_attempts:
                raise TransportError(
                    f"model transport failed after {attempt} attempts"
                ) from error
            sleep(active.delays_seconds[attempt - 1])
        except SchemaOutputError:
            raise
    raise AssertionError("retry loop exhausted without return or error")
