"""Reconcile model-extracted facts with source and evidence authority."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import re

from .extraction_protocol import _ExtractedFact
from .models import FactSource, TaskFact


_VALUE_ALIASES = {
    "steady": ("steady", "稳态"),
    "transient": ("transient", "瞬态", "非稳态"),
    "incompressible": ("incompressible", "不可压缩"),
    "compressible": ("compressible", "可压缩"),
    "single_phase": ("single_phase", "single phase", "单相"),
    "multiphase": ("multiphase", "multi-phase", "多相"),
    "laminar": ("laminar", "层流"),
    "two_d": ("two_d", "two-dimensional", "2d", "二维"),
    "three_d": ("three_d", "three-dimensional", "3d", "三维"),
    "axisymmetric": ("axisymmetric", "轴对称"),
    "inlet": ("inlet", "入口"),
    "outlet": ("outlet", "出口"),
    "wall": ("wall", "壁面", "墙面"),
    "symmetry": ("symmetry", "对称"),
    "empty": ("empty", "二维前后"),
    "fluid": ("fluid", "流体"),
    "solid": ("solid", "固体"),
    "porous": ("porous", "多孔"),
}

_EXCLUSIVE_VALUE_GROUPS = (
    frozenset({"steady", "transient"}),
    frozenset({"incompressible", "compressible"}),
    frozenset({"single_phase", "multiphase"}),
    frozenset({"two_d", "three_d", "axisymmetric"}),
)
_NUMBER_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_.])"
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    r"(?![A-Za-z0-9_.])"
)
_NEGATION_PREFIX = re.compile(
    r"(?:not(?:\s+(?:an?|the))?|no|non|without)\s*$",
    flags=re.IGNORECASE,
)


def _text_has_alias(text: str, alias: str) -> bool:
    if alias.isascii():
        for match in re.finditer(
            rf"(?<![A-Za-z0-9_]){re.escape(alias)}"
            rf"(?![A-Za-z0-9_])",
            text,
            flags=re.IGNORECASE,
        ):
            if not _NEGATION_PREFIX.search(
                text[max(0, match.start() - 16) : match.start()]
            ):
                return True
        return False
    start = 0
    while (index := text.find(alias, start)) >= 0:
        prefix = text[max(0, index - 4) : index]
        if not any(marker in prefix for marker in ("不", "非", "无", "未")):
            return True
        start = index + 1
    return False


def _text_has_value(text: str, value: object) -> bool:
    if isinstance(value, str):
        for group in _EXCLUSIVE_VALUE_GROUPS:
            if value not in group:
                continue
            if any(
                _text_has_alias(text, alias)
                for other in group - {value}
                for alias in _VALUE_ALIASES.get(other, (other,))
            ):
                return False
        candidates = _VALUE_ALIASES.get(value, (value,))
        return any(_text_has_alias(text, candidate) for candidate in candidates)
    if isinstance(value, bool):
        return bool(
            re.search(
                rf"(?<![A-Za-z0-9_]){str(value)}(?![A-Za-z0-9_])",
                text,
                flags=re.IGNORECASE,
            )
        )
    if isinstance(value, (int, float)):
        try:
            expected = Decimal(str(value))
        except InvalidOperation:
            return False
        if not expected.is_finite():
            return False
        for match in _NUMBER_TOKEN.finditer(text):
            try:
                observed = Decimal(match.group())
            except InvalidOperation:
                continue
            if observed.is_finite() and observed == expected:
                return True
        return False
    return value is None


def _scalar_leaves(value: object):
    if isinstance(value, dict):
        for item in value.values():
            yield from _scalar_leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _scalar_leaves(item)
    elif isinstance(value, (str, int, float, bool)):
        yield value


_NON_SEMANTIC_KEYS = {
    "value",
    "unit",
    "condition",
    "type",
    "model",
    "role",
    "strategy",
    "enabled",
}


def _semantic_keys(value: object):
    if isinstance(value, dict):
        for key, item in value.items():
            if key not in _NON_SEMANTIC_KEYS:
                yield key
            yield from _semantic_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _semantic_keys(item)


def _user_fact_value_supported(path: str, value: object, evidence: str) -> bool:
    """Conservatively bind a model-extracted value to its quoted evidence."""

    if path == "geometry" and isinstance(value, dict):
        checks: list[bool] = []
        for key in ("dimensionality", "length_unit"):
            if value.get(key) is not None:
                checks.append(_text_has_value(evidence, value[key]))
        for role_key in ("patch_roles", "region_roles"):
            for item in value.get(role_key, []) or []:
                if isinstance(item, dict):
                    checks.extend(
                        (
                            _text_has_value(evidence, item.get("name")),
                            _text_has_value(evidence, item.get("role")),
                        )
                    )
        for name, parameter in (value.get("parameters") or {}).items():
            if isinstance(parameter, dict):
                checks.extend(
                    (
                        _text_has_value(evidence, name),
                        _text_has_value(evidence, parameter.get("value")),
                        _text_has_value(evidence, parameter.get("unit")),
                    )
                )
        return bool(checks) and all(checks)
    if isinstance(value, (list, dict)):
        scalar_values = list(_scalar_leaves(value))
        semantic_keys = list(_semantic_keys(value))
        return bool(scalar_values) and all(
            _text_has_value(evidence, item) for item in scalar_values
        ) and all(_text_has_value(evidence, key) for key in semantic_keys)
    return _text_has_value(evidence, value)


def geometry_component_supported(
    value: object,
    evidence: str,
    *,
    trusted_confirmation: bool,
) -> bool:
    return trusted_confirmation or _text_has_value(evidence, value)


_EVIDENCE_QUOTES = {
    '"': '"',
    "'": "'",
    "“": "”",
    "‘": "’",
}


def verified_user_evidence(evidence: str, request: str) -> bool:
    candidate = evidence.strip()
    if len(candidate) >= 2:
        closing = _EVIDENCE_QUOTES.get(candidate[0])
        if closing is not None and candidate[-1] == closing:
            candidate = candidate[1:-1].strip()
    return bool(candidate) and candidate in request


def _normalized_extracted_facts(
    facts: list[_ExtractedFact],
) -> list[_ExtractedFact]:
    """Collapse harmless repeats and fail closed on conflicting values."""

    by_path: dict[str, list[_ExtractedFact]] = {}
    for item in facts:
        by_path.setdefault(item.path, []).append(item)
    normalized: list[_ExtractedFact] = []
    for path in sorted(by_path):
        candidates = by_path[path]
        signatures = {
            (
                json.dumps(
                    json.loads(item.value_json),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                item.source,
                item.impact,
            )
            for item in candidates
        }
        if len(signatures) == 1:
            normalized.append(
                min(candidates, key=lambda item: (len(item.evidence), item.evidence))
            )
            continue
        normalized.append(
            candidates[0].model_copy(
                update={
                    "source": FactSource.MODEL_INFERENCE,
                    "evidence": f"conflicting duplicate model facts for {path}",
                    "confirmed": False,
                }
            )
        )
    return normalized


def reconcile_extracted_facts(
    extracted: list[_ExtractedFact],
    request: str,
) -> list[TaskFact]:
    facts: list[TaskFact] = []
    for item in _normalized_extracted_facts(extracted):
        source = item.source
        confirmed = item.confirmed
        if source in {
            FactSource.USER_CONFIRMATION,
            FactSource.SYSTEM_DEFAULT,
        }:
            source = FactSource.MODEL_INFERENCE
        if (
            source == FactSource.USER_TEXT
            and not verified_user_evidence(item.evidence, request)
        ):
            source = FactSource.MODEL_INFERENCE
        if source == FactSource.PUBLIC_ASSET:
            source = FactSource.MODEL_INFERENCE
        if source == FactSource.USER_TEXT:
            confirmed = _user_fact_value_supported(
                item.path,
                json.loads(item.value_json),
                item.evidence,
            )
        if source == FactSource.MODEL_INFERENCE and item.impact in {"medium", "high"}:
            confirmed = False
        facts.append(
            TaskFact(
                path=item.path,
                value=json.loads(item.value_json),
                source=source,
                evidence=item.evidence,
                impact=item.impact,
                confirmed=confirmed,
            )
        )
    return facts


__all__ = [
    "geometry_component_supported",
    "reconcile_extracted_facts",
    "verified_user_evidence",
]
