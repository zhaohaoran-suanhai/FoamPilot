"""与具体后端无关的模型请求契约。"""

from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelContextArtifact(StrictModel):
    """Content-free reference to context frozen before a model request."""

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def model_post_init(self, __context: object) -> None:
        parsed = PurePosixPath(self.path)
        if parsed.is_absolute() or ".." in parsed.parts or not parsed.parts:
            raise ValueError("context artifact path must be safe and relative")


class ModelRequest(StrictModel):
    purpose: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    response_schema: dict[str, object] = Field(default_factory=dict)
    context_artifacts: tuple[ModelContextArtifact, ...] = ()
