from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from foampilot.models.schema import strict_response_schema
from foampilot.plans import ExecutionPlan


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = None


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 3
    names: list[str] = Field(default_factory=list, min_length=1)
    nested: _Nested = Field(default_factory=_Nested)


def _objects(value: Any):
    if isinstance(value, dict):
        if value.get("type") == "object" or "properties" in value:
            yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def test_strict_response_schema_requires_every_declared_property() -> None:
    schema = strict_response_schema(_Output.model_json_schema())

    for object_schema in _objects(schema):
        properties = object_schema.get("properties", {})
        assert object_schema["required"] == list(properties)
        assert object_schema["additionalProperties"] is False


def test_strict_response_schema_removes_unsupported_validation_keywords() -> None:
    schema = strict_response_schema(_Output.model_json_schema())
    encoded = str(schema)

    assert "default" not in encoded
    assert "minLength" not in encoded
    assert "title" not in encoded


def test_strict_response_schema_does_not_mutate_pydantic_schema() -> None:
    original = _Output.model_json_schema()

    strict_response_schema(original)

    assert "default" in original["properties"]["schema_version"]


def test_execution_plan_schema_is_strict_for_every_nested_object() -> None:
    schema = strict_response_schema(ExecutionPlan.model_json_schema())

    for object_schema in _objects(schema):
        properties = object_schema.get("properties", {})
        assert object_schema["required"] == list(properties)
        assert object_schema["additionalProperties"] is False
