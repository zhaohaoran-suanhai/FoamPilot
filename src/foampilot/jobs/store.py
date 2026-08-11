"""Atomic local job receipt, status, control and writer-lock storage."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock

from .models import CancelRequest, JobOperation, JobSpec, JobState, JobStatus


_SECRET = re.compile(
    r"(?i)(?:\bsk-[A-Za-z0-9_-]{8,}\b|"
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|password)\s*[:=])"
)
_SECRET_FLAGS = {
    "--api-key",
    "--access-token",
    "--auth-token",
    "--password",
}
_COMMAND_PREFIX = {
    JobOperation.DRAFT: ("task", "draft"),
    JobOperation.PLAN: ("plan",),
    JobOperation.SOLVE: ("solve",),
    JobOperation.RESUME: ("resume",),
    JobOperation.RERUN: ("rerun",),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    payload = (
        value.model_dump(mode="json")
        if hasattr(value, "model_dump")
        else value
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _exclusive_json(path: Path, value: object) -> None:
    payload = (
        value.model_dump(mode="json")
        if hasattr(value, "model_dump")
        else value
    )
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def build_job_spec(
    *,
    job_root: str | Path,
    project_root: str | Path,
    operation: JobOperation | str,
    arguments: Sequence[str],
) -> JobSpec:
    project = Path(project_root)
    job = Path(job_root)
    if project.is_symlink() or job.is_symlink():
        raise ValueError("job and project roots must not be symbolic links")
    project = project.resolve()
    job = job.resolve()
    if not job.is_relative_to(project):
        raise ValueError("job root is outside project")
    selected_operation = JobOperation(operation)
    normalized = tuple(str(item) for item in arguments)
    prefix = _COMMAND_PREFIX[selected_operation]
    if normalized[: len(prefix)] != prefix:
        raise ValueError("job operation does not match CLI arguments")
    if any("\x00" in item for item in normalized):
        raise ValueError("job argument contains a null byte")
    if any(item.casefold() in _SECRET_FLAGS for item in normalized) or any(
        _SECRET.search(item) for item in normalized
    ):
        raise ValueError("job arguments must not contain a secret")

    inputs: dict[str, str] = {}
    for argument in normalized:
        candidate = Path(argument)
        if not candidate.is_absolute():
            continue
        if candidate.is_symlink():
            raise ValueError("job input must not be a symbolic link")
        if candidate.is_dir():
            candidate = candidate / "artifact-manifest.json"
        if not candidate.is_file():
            continue
        if candidate.is_symlink():
            raise ValueError("job input must not be a symbolic link")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(project):
            raise ValueError(f"job input is outside project: {resolved}")
        relative = resolved.relative_to(project).as_posix()
        inputs[relative] = _sha256(resolved)
    return JobSpec(
        job_id=job.name,
        operation=selected_operation,
        created_at=_utc_now(),
        project_root=project,
        arguments=normalized,
        input_paths=tuple(sorted(inputs)),
        input_sha256={key: inputs[key] for key in sorted(inputs)},
    )


class LocalJobStore:
    """Single-job store; only the writer-lock owner should update status."""

    def __init__(self, root: str | Path) -> None:
        source = Path(root)
        if source.is_symlink():
            raise ValueError(f"job root is a symbolic link: {source}")
        self.root = source.resolve()
        if not self.root.is_dir():
            raise ValueError(f"job root is not a directory: {self.root}")
        self.job_path = self.root / "job.json"
        self.status_path = self.root / "job-status.json"
        self.cancel_path = self.root / "control/cancel-request.json"
        self.lock_path = self.root / "worker.lock"
        self._thread_lock = RLock()

    def create(self, spec: JobSpec) -> None:
        if spec.job_id != self.root.name:
            raise ValueError("job ID does not match job root")
        if not self.root.is_relative_to(spec.project_root):
            raise ValueError("job root is outside project")
        _exclusive_json(self.job_path, spec)

    def read_spec(self) -> JobSpec:
        return JobSpec.model_validate_json(
            self.job_path.read_text(encoding="utf-8")
        )

    def verify_inputs(self) -> None:
        spec = self.read_spec()
        for relative, expected in spec.input_sha256.items():
            path = spec.project_root / relative
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"job input is unavailable: {relative}")
            resolved = path.resolve()
            if not resolved.is_relative_to(spec.project_root):
                raise ValueError(f"job input escaped project: {relative}")
            if _sha256(resolved) != expected:
                raise ValueError(f"job input changed after submission: {relative}")

    def initialize_status(self) -> JobStatus:
        status = JobStatus(
            job_id=self.read_spec().job_id,
            revision=1,
            state=JobState.SUBMITTED,
        )
        _exclusive_json(self.status_path, status)
        return status

    def read_status(self) -> JobStatus:
        return JobStatus.model_validate_json(
            self.status_path.read_text(encoding="utf-8")
        )

    def update_status(self, **updates: object) -> JobStatus:
        with self._thread_lock:
            current = self.read_status()
            revised = current.model_copy(
                update={"revision": current.revision + 1, **updates}
            )
            revised = JobStatus.model_validate(
                revised.model_dump(mode="python")
            )
            _atomic_json(self.status_path, revised)
            return revised

    @property
    def cancel_requested(self) -> bool:
        return self.cancel_path.is_file() and not self.cancel_path.is_symlink()

    def request_cancel(self, *, requested_by: str) -> CancelRequest:
        self.cancel_path.parent.mkdir(exist_ok=True)
        request = CancelRequest(
            job_id=self.read_spec().job_id,
            requested_at=_utc_now(),
            requested_by=requested_by,
        )
        try:
            _exclusive_json(self.cancel_path, request)
        except FileExistsError:
            return CancelRequest.model_validate_json(
                self.cancel_path.read_text(encoding="utf-8")
            )
        return request

    def writer_lock_held(self) -> bool:
        """Inspect the existing worker lock without creating or changing it."""

        if not self.lock_path.is_file() or self.lock_path.is_symlink():
            return False
        with self.lock_path.open("r", encoding="utf-8") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            return False

    @contextmanager
    def writer_lock(self) -> Iterator[None]:
        with self.lock_path.open("a+", encoding="utf-8") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(
                    "JOB_WRITER_LOCKED: another worker owns this job"
                ) from error
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


__all__ = ["LocalJobStore", "build_job_spec"]
