from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
import pytest

from foampilot.authoring import CaseBundle
from foampilot.models.schema import strict_response_schema
from foampilot.repair import RepairProposal
from foampilot.simulation import CaseDesignProposal, SimulationIntent


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


def _empty_schema_paths(value: Any, path: str = "$") -> list[str]:
    if isinstance(value, dict):
        paths = [path] if not value else []
        for key, child in value.items():
            paths.extend(_empty_schema_paths(child, f"{path}.{key}"))
        return paths
    if isinstance(value, list):
        paths: list[str] = []
        for index, child in enumerate(value):
            paths.extend(_empty_schema_paths(child, f"{path}[{index}]"))
        return paths
    return []


@pytest.mark.parametrize(
    "response_model",
    [SimulationIntent, CaseDesignProposal],
)
def test_simulation_response_schema_has_no_untyped_value(
    response_model: type[BaseModel],
) -> None:
    schema = strict_response_schema(response_model.model_json_schema())

    assert _empty_schema_paths(schema) == []


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


def test_simulation_intent_provider_schema_describes_quantity_identifier() -> None:
    schema = strict_response_schema(SimulationIntent.model_json_schema())
    quantity = schema["$defs"]["ObservationRequest"]["properties"]["quantity"]

    assert "lower_snake_case" in quantity["description"]
    assert "AvailableObservationContracts" in quantity["description"]


@pytest.mark.parametrize("response_model", [CaseBundle, RepairProposal])
def test_model_response_schema_is_strict_for_every_nested_object(
    response_model: type[BaseModel],
) -> None:
    schema = strict_response_schema(response_model.model_json_schema())

    for object_schema in _objects(schema):
        properties = object_schema.get("properties", {})
        assert object_schema["required"] == list(properties)
        assert object_schema["additionalProperties"] is False

    property_names = {
        name
        for object_schema in _objects(schema)
        for name in object_schema.get("properties", {})
    }
    assert "commands" not in property_names
    assert "args" not in property_names
    assert "mpi_ranks" not in property_names
    assert "timeout_seconds" not in property_names
