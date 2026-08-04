#!/usr/bin/env python3
"""Audit FoamPilot source boundaries without reading credentials or copying text.

The optional comparison reports only paths, counts, hashes and short digests.  It
never serializes source lines from either repository.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Iterator


TEXT_SUFFIXES = frozenset(
    {".cfg", ".ini", ".json", ".md", ".py", ".rst", ".toml", ".txt", ".yaml", ".yml"}
)
TEXT_NAMES = frozenset({"AGENTS.md", "LICENSE", "NOTICE", "NOTICE.md"})
EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "site-packages",
        "venv",
    }
)
ALLOWED_FORBIDDEN_REFERENCES = frozenset(
    {
        "PROVENANCE.md",
        "docs/clean-source-model-backend-design.md",
        "docs/foampilot-source-refactor-implementation-plan.md",
        "docs/reports/clean-source-audit.json",
    }
)
GENERATED_REPORTS = frozenset({"docs/reports/clean-source-audit.json"})
EXCLUDED_PREFIXES = ("docs/reports/", "docs/superpowers/")

# Join fragments so this scanner does not match its own implementation.
FORBIDDEN_CORE_TOKENS = (
    "Codex" + "OAuth" + "ProviderClient",
    "tokens." + "access_token",
    ".codex/" + "auth.json",
    "load_codex_" + "access_token",
)

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[\u3400-\u9fff]+")
COMMON_SCHEMA_TOKENS = frozenset(
    {
        "additionalproperties",
        "anyof",
        "array",
        "boolean",
        "default",
        "description",
        "enum",
        "items",
        "null",
        "object",
        "properties",
        "required",
        "schema",
        "string",
        "title",
        "type",
    }
)


@dataclass(frozen=True)
class AuditFinding:
    rule_id: str
    path: str
    line: int | None = None
    compared_path: str | None = None
    count: int | None = None
    score: float | None = None
    fingerprint: str | None = None


@dataclass(frozen=True)
class ProvenanceAuditReport:
    root: str
    root_sha256: str
    scanned_files: int
    compare_root: str | None
    compared_files: int
    forbidden_matches: tuple[AuditFinding, ...]
    long_line_matches: tuple[AuditFinding, ...]
    shingle_matches: tuple[AuditFinding, ...]

    @property
    def passed(self) -> bool:
        return not (
            self.forbidden_matches
            or self.long_line_matches
            or self.shingle_matches
        )

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["passed"] = self.passed
        return payload


@dataclass(frozen=True)
class _TextFile:
    relative: str
    payload: bytes
    text: str


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES


def _iter_text_files(root: Path) -> Iterator[_TextFile]:
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative_path = path.relative_to(root)
        relative = relative_path.as_posix()
        if any(
            part in EXCLUDED_PARTS or part.endswith(".egg-info")
            for part in relative_path.parts
        ):
            continue
        if relative.startswith(EXCLUDED_PREFIXES):
            continue
        if relative in GENERATED_REPORTS:
            continue
        if not _is_text_file(path):
            continue
        payload = path.read_bytes()
        if len(payload) > 4 * 1024 * 1024 or b"\x00" in payload:
            continue
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        yield _TextFile(relative, payload, content)


def _tree_digest(files: Iterable[_TextFile]) -> str:
    digest = sha256()
    for item in files:
        relative = item.relative.encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(item.payload).to_bytes(8, "big"))
        digest.update(item.payload)
    return digest.hexdigest()


def _fingerprint(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:16]


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in TOKEN_RE.findall(text))


def _is_standard_or_schema(tokens: tuple[str, ...]) -> bool:
    if not tokens:
        return True
    if set(tokens).issubset(COMMON_SCHEMA_TOKENS):
        return True
    joined = " ".join(tokens)
    return (
        "permission is hereby granted free of charge" in joined
        or "the software is provided as is without warranty" in joined
        or "foamfile version format class object" in joined
    )


def _is_standard_mit_license(item: _TextFile) -> bool:
    return (
        item.relative.rsplit("/", 1)[-1].startswith("LICENSE")
        and "MIT License" in item.text
        and "Permission is hereby granted, free of charge" in item.text
        and 'THE SOFTWARE IS PROVIDED "AS IS"' in item.text
    )


def _normalized_long_lines(item: _TextFile) -> Iterator[tuple[int, str]]:
    if _is_standard_mit_license(item):
        return
    for line_number, line in enumerate(item.text.splitlines(), start=1):
        normalized_tokens = _tokens(line)
        normalized = " ".join(normalized_tokens)
        if len(normalized) < 80 or _is_standard_or_schema(normalized_tokens):
            continue
        yield line_number, normalized


def _shingles(item: _TextFile, size: int = 12) -> frozenset[str]:
    if _is_standard_mit_license(item):
        return frozenset()
    tokens = _tokens(item.text)
    if len(tokens) < size:
        return frozenset()
    shingles: set[str] = set()
    for start in range(len(tokens) - size + 1):
        group = tokens[start : start + size]
        if _is_standard_or_schema(group):
            continue
        shingles.add(" ".join(group))
    return frozenset(shingles)


def _forbidden_findings(files: tuple[_TextFile, ...]) -> tuple[AuditFinding, ...]:
    findings: list[AuditFinding] = []
    for item in files:
        if item.relative in ALLOWED_FORBIDDEN_REFERENCES:
            continue
        for line_number, line in enumerate(item.text.splitlines(), start=1):
            for token in FORBIDDEN_CORE_TOKENS:
                if token in line:
                    findings.append(
                        AuditFinding(
                            rule_id="FORBIDDEN_PRIVATE_PROVIDER_PROTOCOL",
                            path=item.relative,
                            line=line_number,
                            fingerprint=_fingerprint(token),
                        )
                    )
    return tuple(findings)


def _comparison_findings(
    candidate_files: tuple[_TextFile, ...],
    upstream_files: tuple[_TextFile, ...],
) -> tuple[tuple[AuditFinding, ...], tuple[AuditFinding, ...]]:
    upstream_lines: dict[str, tuple[str, int]] = {}
    upstream_shingles: dict[str, str] = {}
    for item in upstream_files:
        for line_number, normalized in _normalized_long_lines(item):
            upstream_lines.setdefault(normalized, (item.relative, line_number))
        for shingle in _shingles(item):
            upstream_shingles.setdefault(shingle, item.relative)

    line_findings: list[AuditFinding] = []
    shingle_findings: list[AuditFinding] = []
    upstream_shingle_set = frozenset(upstream_shingles)
    for item in candidate_files:
        for line_number, normalized in _normalized_long_lines(item):
            match = upstream_lines.get(normalized)
            if match is None:
                continue
            line_findings.append(
                AuditFinding(
                    rule_id="UNEXPLAINED_LONG_LINE_MATCH",
                    path=item.relative,
                    line=line_number,
                    compared_path=match[0],
                    count=1,
                    fingerprint=_fingerprint(normalized),
                )
            )

        candidate_shingles = _shingles(item)
        if not candidate_shingles:
            continue
        overlap = candidate_shingles & upstream_shingle_set
        score = len(overlap) / len(candidate_shingles)
        if score < 0.05:
            continue
        compare_counts: dict[str, int] = {}
        for shingle in overlap:
            matched_path = upstream_shingles[shingle]
            compare_counts[matched_path] = compare_counts.get(matched_path, 0) + 1
        compared_path = max(
            compare_counts,
            key=lambda path: (compare_counts[path], path),
        )
        digest_input = "\n".join(sorted(overlap))
        shingle_findings.append(
            AuditFinding(
                rule_id="UNEXPLAINED_SHINGLE_CONTAINMENT",
                path=item.relative,
                compared_path=compared_path,
                count=len(overlap),
                score=round(score, 6),
                fingerprint=_fingerprint(digest_input),
            )
        )
    return tuple(line_findings), tuple(shingle_findings)


def audit_repository(
    root: str | Path,
    compare_root: str | Path | None = None,
) -> ProvenanceAuditReport:
    candidate_root = Path(root).resolve()
    if not candidate_root.is_dir():
        raise FileNotFoundError(f"repository root is not a directory: {candidate_root}")
    candidate_files = tuple(_iter_text_files(candidate_root))
    upstream_root = Path(compare_root).resolve() if compare_root is not None else None
    if upstream_root is not None and not upstream_root.is_dir():
        raise FileNotFoundError(f"compare root is not a directory: {upstream_root}")
    upstream_files = (
        tuple(_iter_text_files(upstream_root))
        if upstream_root is not None
        else ()
    )
    long_lines: tuple[AuditFinding, ...] = ()
    shingles: tuple[AuditFinding, ...] = ()
    if upstream_root is not None:
        long_lines, shingles = _comparison_findings(
            candidate_files,
            upstream_files,
        )
    return ProvenanceAuditReport(
        root=str(candidate_root),
        root_sha256=_tree_digest(candidate_files),
        scanned_files=len(candidate_files),
        compare_root=str(upstream_root) if upstream_root is not None else None,
        compared_files=len(upstream_files),
        forbidden_matches=_forbidden_findings(candidate_files),
        long_line_matches=long_lines,
        shingle_matches=shingles,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--compare-root", type=Path)
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = audit_repository(args.root, compare_root=args.compare_root)
    rendered = json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
