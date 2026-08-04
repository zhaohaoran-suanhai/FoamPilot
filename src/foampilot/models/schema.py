"""将 Pydantic schema 收敛为模型结构化输出的兼容子集。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_UNSUPPORTED_VALIDATION_KEYS = {
    "$schema",
    "default",
    "examples",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "maxItems",
    "maxLength",
    "maxProperties",
    "maximum",
    "minItems",
    "minLength",
    "minProperties",
    "minimum",
    "multipleOf",
    "pattern",
    "title",
    "uniqueItems",
}


def _normalize(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalized = {
        key: _normalize(item)
        for key, item in value.items()
        if key not in _UNSUPPORTED_VALIDATION_KEYS
    }
    if "const" in normalized:
        constant = normalized.pop("const")
        normalized.setdefault("enum", [constant])

    properties = normalized.get("properties")
    if isinstance(properties, dict):
        normalized["additionalProperties"] = False
        normalized["required"] = list(properties)
    return normalized


def strict_response_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """返回独立、严格且不包含供应商不兼容校验键的 schema。"""

    return _normalize(deepcopy(schema))
