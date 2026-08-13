from __future__ import annotations

import pytest

from foampilot.models.schema import strict_response_schema
from foampilot.taskbuilder.extraction_protocol import _ExtractedTaskDraft
from tests.support.taskbuilder import (
    extraction_payload as _payload,
)


def test_extraction_response_schema_encodes_arbitrary_fact_values_as_json_text() -> None:
    schema = strict_response_schema(_ExtractedTaskDraft.model_json_schema())

    fact_schema = schema["$defs"]["_ExtractedFact"]
    assert fact_schema["properties"]["value"] == {"type": "string"}

    def empty_schemas(value):
        if isinstance(value, dict):
            if not value:
                yield value
            for item in value.values():
                yield from empty_schemas(item)
        elif isinstance(value, list):
            for item in value:
                yield from empty_schemas(item)

    assert list(empty_schemas(schema)) == []


def test_extraction_transport_model_rejects_invalid_domain_path_early() -> None:
    payload = _payload()
    payload["facts"][0]["path"] = "initial_conditions.U"

    with pytest.raises(ValueError, match="literal_error"):
        _ExtractedTaskDraft.model_validate(payload)


def test_extraction_transport_rejects_fact_path_outside_declared_vocabulary() -> None:
    payload = _payload()
    payload["facts"][0]["path"] = "physics.secret_route"

    with pytest.raises(ValueError, match="literal_error"):
        _ExtractedTaskDraft.model_validate(payload)
