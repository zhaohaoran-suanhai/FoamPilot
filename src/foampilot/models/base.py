"""与具体后端无关的模型请求契约。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelRequest(StrictModel):
    purpose: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    response_schema: dict[str, object] = Field(default_factory=dict)
