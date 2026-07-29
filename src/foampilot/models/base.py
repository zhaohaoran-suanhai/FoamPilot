"""Provider-neutral model request contracts."""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field


T = TypeVar("T", bound=BaseModel)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelRequest(StrictModel):
    purpose: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)


class ModelClient(Protocol):
    def generate_structured(
        self,
        request: ModelRequest,
        schema: type[T],
    ) -> T: ...


class ModelError(RuntimeError):
    """Base class for model-boundary failures."""


class TransportError(ModelError):
    """Transient or exhausted model transport failure."""


class SchemaOutputError(ModelError):
    """Model output did not satisfy the requested schema."""
