#!/usr/bin/env python3
"""Freeze a bounded, secret-scanned replay fixture from one verified run."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any

import yaml

from foampilot.artifacts import ArtifactStore
from foampilot.plans import ExecutionPlan


KINDS = (
    "single_region_success",
    "mpi_success",
    "include_success",
    "buoyant_success",
    "multi_region_success",
    "known_failure",
)
_BEARER = re.compile(
    rb"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"
)
_OPENAI_KEY = re.compile(rb"\bsk-[A-Za-z0-9_-]{8,}\b")
_NAMED_SECRET = re.compile(
    rb"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password)"
    rb"\s*[:=]\s*[^\s,;]+"
)


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _reject_secret(path: Path, payload: bytes) -> None:
    if any(
        pattern.search(payload)
        for pattern in (_BEARER, _OPENAI_KEY, _NAMED_SECRET)
    ):
        raise ValueError(f"secret-like content found in {path}")


def _copy_bytes(
    *,
    payload: bytes,
    relative: str,
    destination: Path,
) -> dict[str, object]:
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or ".." in parsed.parts:
        raise ValueError(f"unsafe fixture path: {relative}")
    _reject_secret(Path(relative), payload)
    target = destination / parsed
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return {
        "path": parsed.as_posix(),
        "bytes": len(payload),
        "sha256": _digest(payload),
    }


def _tail_lines(path: Path, limit: int = 200) -> bytes:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)[-limit:]
    return "".join(lines).encode("utf-8")


def _portable_run_result(path: Path) -> bytes:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"run-result root must be a mapping: {path}")
    payload["case_dir"] = "case"
    steps = payload.get("steps", [])
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            for key in ("stdout_path", "stderr_path"):
                value = step.get(key)
                if isinstance(value, str):
                    step[key] = (
                        "case/.foampilot/logs/"
                        + Path(value).name
                    )
    return (
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _load_index(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "fixtures": []}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("fixtures"), list)
    ):
        raise ValueError(f"invalid replay index: {path}")
    return payload


def freeze_fixture(
    *,
    source_run: str | Path,
    fixture_kind: str,
    output_root: str | Path,
    fixture_id: str | None = None,
    replace: bool = False,
) -> Path:
    source = Path(source_run).resolve()
    if fixture_kind not in KINDS:
        raise ValueError(f"unsupported fixture kind: {fixture_kind}")
    if any(part.lower() == "tutorials" for part in source.parts):
        raise ValueError("tutorial sources are forbidden")
    store = ArtifactStore(source.parent)
    issues = store.verify(source)
    if issues:
        raise ValueError("source manifest failed: " + "; ".join(issues))
    summary = store.read_summary(source)
    if not summary.attempts:
        raise ValueError("source run has no native attempt")
    attempt = summary.attempts[-1].attempt
    attempt_root = source / f"attempt-{attempt:02d}"
    plan_path = attempt_root / "execution-plan.json"
    if not plan_path.is_file():
        plan_path = source / "execution-plan.json"
    plan = ExecutionPlan.model_validate_json(
        plan_path.read_text(encoding="utf-8")
    )

    root = Path(output_root).resolve()
    active_id = fixture_id or fixture_kind.replace("_", "-")
    if re.fullmatch(r"[a-z0-9][a-z0-9-]*", active_id) is None:
        raise ValueError("fixture id must be one lowercase safe path segment")
    destination = root / active_id
    if destination.exists():
        if not replace:
            raise FileExistsError(f"fixture already exists: {destination}")
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    files: list[dict[str, object]] = []
    source_files = {
        "summary.json": source / "summary.json",
        "execution-plan.json": plan_path,
        "static-inspection.json": (
            attempt_root / "static-inspection.json"
        ),
        "run-assessment.json": (
            attempt_root / "run-assessment.json"
        ),
        "result-report.json": (
            attempt_root / "result-report.json"
        ),
        "run-result.json": attempt_root / "run-result.json",
    }
    files.append(
        _copy_bytes(
            payload=(
                plan.manifest.model_dump_json(indent=2) + "\n"
            ).encode("utf-8"),
            relative="case-manifest-overlay.json",
            destination=destination,
        )
    )
    for relative, path in source_files.items():
        if path.is_file():
            payload = (
                _portable_run_result(path)
                if relative == "run-result.json"
                else path.read_bytes()
            )
            files.append(
                _copy_bytes(
                    payload=payload,
                    relative=relative,
                    destination=destination,
                )
            )

    case_root = attempt_root / "case"
    for generated in plan.files:
        path = case_root / generated.path
        if not path.is_file():
            raise ValueError(
                f"declared case file is missing: {generated.path}"
            )
        files.append(
            _copy_bytes(
                payload=path.read_bytes(),
                relative=f"case/{generated.path}",
                destination=destination,
            )
        )
    log_root = case_root / ".foampilot/logs"
    if log_root.is_dir():
        for path in sorted(log_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(case_root).as_posix()
            files.append(
                _copy_bytes(
                    payload=_tail_lines(path),
                    relative=f"case/{relative}",
                    destination=destination,
                )
            )

    index_path = root / "index.yaml"
    index = _load_index(index_path)
    fixtures = index["fixtures"]
    assert isinstance(fixtures, list)
    existing = [
        item
        for item in fixtures
        if isinstance(item, dict) and item.get("fixture_id") == active_id
    ]
    if existing and not replace:
        shutil.rmtree(destination)
        raise ValueError(f"duplicate fixture id: {active_id}")
    fixtures[:] = [
        item
        for item in fixtures
        if not (
            isinstance(item, dict)
            and item.get("fixture_id") == active_id
        )
    ]
    fixtures.append(
        {
            "fixture_id": active_id,
            "kind": fixture_kind,
            "source_manifest_sha256": store.manifest_sha256(source),
            "expected": {
                "artifact_valid": True,
                "native_status": summary.status,
            },
            "files": sorted(files, key=lambda item: str(item["path"])),
        }
    )
    fixtures.sort(key=lambda item: str(item["fixture_id"]))
    root.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        yaml.safe_dump(
            index,
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return destination


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument(
        "--fixture-kind",
        required=True,
        choices=KINDS,
    )
    parser.add_argument("--fixture-id")
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--replace", action="store_true")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    destination = freeze_fixture(
        source_run=arguments.source_run,
        fixture_kind=arguments.fixture_kind,
        output_root=arguments.output_root,
        fixture_id=arguments.fixture_id,
        replace=arguments.replace,
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
