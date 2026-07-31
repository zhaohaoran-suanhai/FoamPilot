"""One-call native case-bundle authoring and atomic materialization."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import tempfile

from foampilot.environment import EnvironmentSnapshot
from foampilot.models import (
    ModelBudgetWindow,
    ModelGateway,
    ModelRequest,
    ModelTraceSink,
)
from foampilot.plans import ExecutionPlan
from foampilot.routing import CapabilityProfile
from foampilot.tasks import TaskSpec

from .prompts import bundle_request_text


def author_case_bundle(
    task: TaskSpec,
    environment: EnvironmentSnapshot,
    capability: CapabilityProfile,
    gateway: ModelGateway,
    knowledge_text: str,
    skills_text: str,
    *,
    budget: ModelBudgetWindow,
    trace: ModelTraceSink,
) -> ExecutionPlan:
    """Ask the model once for every case file and typed command."""

    system, user = bundle_request_text(
        task,
        environment,
        capability,
        knowledge_text,
        skills_text,
    )
    return gateway.generate_structured(
        ModelRequest(
            purpose="author-openfoam-case-bundle",
            system_prompt=system,
            user_prompt=user,
        ),
        ExecutionPlan,
        budget=budget,
        trace=trace,
    ).value


def _safe_relative(relative: str) -> bool:
    parsed = PurePosixPath(relative)
    return (
        bool(relative)
        and not parsed.is_absolute()
        and ".." not in parsed.parts
    )


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def materialize_case(
    plan: ExecutionPlan,
    task: TaskSpec,
    case_root: str | Path,
) -> list[Path]:
    """Write one prevalidated model bundle without overwriting user assets."""

    root = Path(case_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    public_paths = {asset.path for asset in task.public_assets}
    existing = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    unexpected = existing - public_paths
    if unexpected:
        raise ValueError(
            "case directory is non-empty: " + ", ".join(sorted(unexpected))
        )

    seen: set[str] = set()
    for generated in plan.files:
        if not _safe_relative(generated.path):
            raise ValueError(
                f"generated file path must be safe relative: {generated.path}"
            )
        if generated.path in seen:
            raise ValueError(f"duplicate generated file: {generated.path}")
        if generated.path in public_paths:
            raise ValueError(
                f"generated file overlaps public asset: {generated.path}"
            )
        if any(
            protected in generated.content
            for protected in task.protected_paths
        ):
            raise ValueError("generated file contains a protected path")
        seen.add(generated.path)

    written: list[Path] = []
    for generated in plan.files:
        target = (root / generated.path).resolve()
        if not target.is_relative_to(root):
            raise ValueError("generated target escapes case")
        _write_atomic(target, generated.content)
        written.append(target)
    return written
