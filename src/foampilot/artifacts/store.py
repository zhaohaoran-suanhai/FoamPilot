"""Exclusive run directories and content-addressed artifact manifests."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4


_BEARER = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")
_OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_NAMED_SECRET = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password)"
    r"\s*[:=]\s*)[^\s,;]+"
)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def redact_text(text: str) -> str:
    """Remove common bearer, API-key, and named-secret values."""

    text = _BEARER.sub(r"\1[REDACTED]", text)
    text = _OPENAI_KEY.sub("[REDACTED]", text)
    return _NAMED_SECRET.sub(r"\1[REDACTED]", text)


class ArtifactStore:
    """Create exclusive run paths and freeze their content hashes."""

    manifest_name = "artifact-manifest.json"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def create_run(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        for _ in range(10):
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            destination = self.root / f"run-{timestamp}-{uuid4().hex[:8]}"
            try:
                destination.mkdir(exist_ok=False)
            except FileExistsError:
                continue
            return destination
        raise FileExistsError("could not allocate a unique run directory")

    def _run_path(self, run_dir: str | Path) -> Path:
        directory = Path(run_dir).resolve()
        if not directory.is_relative_to(self.root):
            raise ValueError(f"directory is outside artifact store: {directory}")
        return directory

    def _entries(self, run_dir: Path) -> dict[str, dict[str, object]]:
        entries: dict[str, dict[str, object]] = {}
        for path in sorted(run_dir.rglob("*")):
            relative = path.relative_to(run_dir).as_posix()
            if relative == self.manifest_name or path.is_dir():
                continue
            if path.is_symlink():
                entries[relative] = {
                    "type": "symlink",
                    "target": os.readlink(path),
                }
            else:
                entries[relative] = {
                    "type": "file",
                    "bytes": path.stat().st_size,
                    "sha256": _file_sha256(path),
                }
        return entries

    def finalize(self, run_dir: str | Path) -> Path:
        directory = self._run_path(run_dir)
        manifest = directory / self.manifest_name
        started = time.monotonic()
        entries = self._entries(directory)
        payload = {
            "schema_version": 1,
            "build_seconds": max(time.monotonic() - started, 0.0),
            "files": entries,
        }
        descriptor, temporary_name = tempfile.mkstemp(
            dir=directory,
            prefix=f".{self.manifest_name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, manifest)
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
        return manifest

    def verify(self, run_dir: str | Path) -> list[str]:
        directory = self._run_path(run_dir)
        manifest = directory / self.manifest_name
        if not manifest.is_file():
            return [f"missing manifest: {manifest}"]
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return [f"invalid manifest: {type(error).__name__}"]
        if not isinstance(payload, dict):
            return ["invalid manifest: root must be a mapping"]
        expected = payload.get("files")
        if not isinstance(expected, dict):
            return ["invalid manifest: files must be a mapping"]
        actual = self._entries(directory)
        problems: list[str] = []
        for relative in sorted(set(expected) - set(actual)):
            problems.append(f"missing artifact: {relative}")
        for relative in sorted(set(actual) - set(expected)):
            problems.append(f"unexpected artifact: {relative}")
        for relative in sorted(set(actual) & set(expected)):
            if actual[relative] != expected[relative]:
                problems.append(f"hash mismatch: {relative}")
        return problems

    def manifest_sha256(self, run_dir: str | Path) -> str:
        directory = self._run_path(run_dir)
        manifest = directory / self.manifest_name
        if not manifest.is_file():
            raise FileNotFoundError(f"missing manifest: {manifest}")
        return _file_sha256(manifest)

    @staticmethod
    def read_summary(run_dir: str | Path):
        """Load the native typed RunSummary."""

        path = Path(run_dir) / "summary.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("summary root must be a mapping")
        from .models import AttemptSummary, RunSummary
        from foampilot.workflow import (
            FailureDomain,
            FailureRecord,
            ResumeMetadata,
            WorkflowState,
        )

        version = payload.get("schema_version", 1)
        if version == 2:
            return RunSummary.model_validate(payload)
        if version != 1:
            raise ValueError(f"unsupported summary schema version: {version}")
        status = str(payload.get("status", "REQUEST_INCOMPLETE"))
        message = str(payload.get("message", "legacy run summary"))
        native_statuses = {
            "STATIC_INSPECTION_FAILED",
            "MESH_FAILED",
            "MESH_QUALITY_FAILED",
            "INITIALIZATION_FAILED",
            "SOLVER_FAILED",
            "POSTPROCESS_FAILED",
            "PUBLIC_VALIDATION_FAILED",
            "PUBLIC_VALIDATION_PASS",
        }
        if status == "PUBLIC_VALIDATION_PASS":
            workflow_state = WorkflowState.COMPLETED
            native_status = status
            primary_failure = None
        elif status in native_statuses:
            workflow_state = WorkflowState.FAILED
            native_status = status
            primary_failure = FailureRecord(
                domain=FailureDomain.LEGACY,
                code=status,
                detail=message,
            )
        else:
            workflow_state = WorkflowState.FAILED
            native_status = None
            domains = {
                "BLOCKED_ENVIRONMENT": FailureDomain.ENVIRONMENT,
                "PLAN_INVALID": FailureDomain.PLAN,
                "CASE_GENERATION_FAILED": FailureDomain.CASE,
                "REQUEST_INCOMPLETE": FailureDomain.TASK,
            }
            primary_failure = FailureRecord(
                domain=domains.get(status, FailureDomain.LEGACY),
                code=status,
                detail=message,
            )
        attempts = []
        for item in payload.get("attempts", []):
            if not isinstance(item, dict):
                continue
            try:
                attempts.append(AttemptSummary.model_validate(item))
            except ValueError:
                continue
        return RunSummary(
            task_id=str(payload.get("task_id", "legacy-run")),
            workflow_state=workflow_state,
            native_status=native_status,
            attempts=attempts,
            primary_failure=primary_failure,
            resume=ResumeMetadata(
                allowed=False,
                reason="legacy summaries cannot resume",
            ),
            message=message,
        )
