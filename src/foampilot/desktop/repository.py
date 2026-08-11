"""Safe, read-only access to one explicitly opened FoamPilot run."""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path, PurePosixPath
import re

from foampilot.artifacts import ArtifactStore
from foampilot.knowledge import load_knowledge_corpus
from foampilot.workflow import WorkflowEvent

from .viewmodels import (
    KnowledgeReference,
    RunFileView,
    RunSnapshot,
    SkillReference,
    TimelineView,
)
from .telemetry import parse_residual_series


_TELEMETRY_LOG_BYTES = 8 * 1024 * 1024
_TELEMETRY_SAMPLE_LIMIT = 5_000


class RunOpenError(ValueError):
    """The requested run or artifact cannot be opened safely."""


class RunCollectionError(RunOpenError):
    """A run collection was selected where one concrete run is required."""

    def __init__(self, directory: Path, children: tuple[Path, ...]) -> None:
        self.directory = directory
        self.children = children
        super().__init__(
            "selected directory is a run collection; choose one concrete "
            f"child run ({len(children)} available): {directory}"
        )


def _category(relative: str) -> str:
    if "/.foampilot/logs/" in f"/{relative}":
        return "log"
    if relative == "workflow-events.jsonl":
        return "workflow"
    if relative.startswith("attempt-") and "/case/" in relative:
        return "case"
    name = PurePosixPath(relative).name
    if (
        name in {"summary.json", "artifact-manifest.json"}
        or "report" in name
        or "validation" in name
    ):
        return "report"
    return "other"


def _has_symlink_component(root: Path, relative: PurePosixPath) -> bool:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


@lru_cache(maxsize=1)
def _formal_knowledge_by_id() -> dict[str, object]:
    root = Path(__file__).resolve().parents[1] / "knowledge/openfoam10"
    return {entry.id: entry for entry in load_knowledge_corpus(root)}


def _context_stage(relative: str) -> tuple[str, int | None]:
    name = PurePosixPath(relative).name
    stage = "repair" if name == "repair-agent-context.json" else "author"
    match = re.search(r"(?:^|/)attempt-(\d+)(?:/|$)", relative)
    attempt = int(match.group(1)) if match is not None else None
    return stage, attempt


def _context_views(
    directory: Path,
    files: list[RunFileView],
    warnings: list[str],
) -> tuple[list[KnowledgeReference], list[SkillReference]]:
    try:
        knowledge_by_id = _formal_knowledge_by_id()
    except (OSError, ValueError) as error:
        knowledge_by_id = {}
        warnings.append(f"formal knowledge metadata is unavailable: {error}")
    references: list[KnowledgeReference] = []
    skills: list[SkillReference] = []
    context_paths = [
        item.path
        for item in files
        if PurePosixPath(item.path).name
        in {"agent-context.json", "repair-agent-context.json"}
    ]
    for relative in sorted(context_paths):
        path = directory / Path(*PurePosixPath(relative).parts)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("context root must be a mapping")
            slots = payload.get("knowledge_slots", {})
            selected = payload.get("selected_knowledge_ids", [])
            hashes = payload.get("selected_source_hashes", {})
            skill_names = payload.get("skill_names", [])
            if not isinstance(slots, dict):
                raise ValueError("knowledge_slots must be a mapping")
            if not isinstance(selected, list):
                raise ValueError("selected_knowledge_ids must be a list")
            if not isinstance(hashes, dict):
                raise ValueError("selected_source_hashes must be a mapping")
            if not isinstance(skill_names, list):
                raise ValueError("skill_names must be a list")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            warnings.append(f"{relative} is invalid: {error}")
            continue
        stage, attempt = _context_stage(relative)
        slots_by_id: dict[str, list[str]] = {}
        for slot, entry_id in slots.items():
            if isinstance(slot, str) and isinstance(entry_id, str):
                slots_by_id.setdefault(entry_id, []).append(slot)
        for raw_entry_id in selected:
            if not isinstance(raw_entry_id, str):
                warnings.append(f"{relative} contains a non-string knowledge ID")
                continue
            entry = knowledge_by_id.get(raw_entry_id)
            entry_slots = slots_by_id.get(raw_entry_id, ["unassigned"])
            for slot in entry_slots:
                references.append(
                    KnowledgeReference(
                        stage=stage,
                        attempt=attempt,
                        slot=slot,
                        entry_id=raw_entry_id,
                        title=(entry.title if entry is not None else None),
                        knowledge_type=(
                            entry.knowledge_type if entry is not None else None
                        ),
                        source_locator=(
                            entry.source.locator if entry is not None else None
                        ),
                        source_sha256=(
                            str(hashes[raw_entry_id])
                            if raw_entry_id in hashes
                            else None
                        ),
                    )
                )
        for name in skill_names:
            if isinstance(name, str):
                skills.append(
                    SkillReference(stage=stage, attempt=attempt, name=name)
                )
            else:
                warnings.append(f"{relative} contains a non-string Skill name")
    return references, skills


def _residual_views(
    directory: Path,
    files: list[RunFileView],
    warnings: list[str],
):
    samples = []
    for item in files:
        if item.category != "log" or not item.path.endswith(".stdout.log"):
            continue
        relative = PurePosixPath(item.path)
        path = directory / Path(*relative.parts)
        match = re.search(r"(?:^|/)attempt-(\d+)(?:/|$)", item.path)
        attempt = int(match.group(1)) if match is not None else None
        try:
            size = path.stat().st_size
            with path.open("rb") as stream:
                if size > _TELEMETRY_LOG_BYTES:
                    stream.seek(size - _TELEMETRY_LOG_BYTES)
                    stream.readline()
                    warnings.append(
                        f"{item.path} residual view uses the latest "
                        f"{_TELEMETRY_LOG_BYTES} bytes"
                    )
                text = stream.read().decode("utf-8", errors="replace")
        except OSError as error:
            warnings.append(f"{item.path} cannot be read for residuals: {error}")
            continue
        samples.extend(
            parse_residual_series(
                text,
                attempt=attempt,
                source_log=item.path,
            )
        )
    return samples[-_TELEMETRY_SAMPLE_LIMIT:]


def _read_json_projection(
    directory: Path,
    registered: set[str],
    relative: str | None,
    warnings: list[str],
) -> dict[str, object] | None:
    if relative is None or relative not in registered:
        return None
    relative_path = PurePosixPath(relative)
    if _has_symlink_component(directory, relative_path):
        warnings.append(f"{relative} is a symbolic link and was ignored")
        return None
    path = directory / Path(*relative_path.parts)
    try:
        if path.stat().st_size > 2_097_152:
            raise ValueError("artifact exceeds the 2 MiB projection limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON root must be a mapping")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        warnings.append(f"{relative} is invalid: {error}")
        return None
    return payload


def _latest_attempt_artifact(
    registered: set[str],
    name: str,
) -> str | None:
    matches: list[tuple[int, str]] = []
    pattern = re.compile(rf"^attempt-(\d+)/{re.escape(name)}$")
    for relative in registered:
        match = pattern.match(relative)
        if match is not None:
            matches.append((int(match.group(1)), relative))
    if matches:
        return max(matches)[1]
    return name if name in registered else None


class RunRepository:
    """Build immutable desktop projections without modifying run artifacts."""

    def open(self, run_dir: str | Path) -> RunSnapshot:
        source = Path(run_dir)
        if source.is_symlink():
            raise RunOpenError(f"run directory is a symbolic link: {source}")
        directory = source.resolve()
        if not directory.is_dir():
            raise RunOpenError(f"run directory does not exist: {directory}")

        direct_control = any(
            (directory / name).exists()
            for name in (
                "task.yaml",
                "summary.json",
                "workflow-events.jsonl",
                ArtifactStore.manifest_name,
            )
        )
        child_runs = tuple(
            child.resolve()
            for child in sorted(directory.iterdir(), key=lambda item: item.name)
            if child.name.startswith("run-")
            and child.is_dir()
            and not child.is_symlink()
        )
        if not direct_control and child_runs:
            raise RunCollectionError(directory, child_runs)

        control_paths = (
            directory / "summary.json",
            directory / "workflow-events.jsonl",
            directory / ArtifactStore.manifest_name,
        )
        for control_path in control_paths:
            if control_path.is_symlink():
                raise RunOpenError(
                    "control artifact is a symbolic link: "
                    f"{control_path.name}"
                )

        warnings: list[str] = []
        summary = None
        summary_path = directory / "summary.json"
        if summary_path.is_file():
            try:
                summary = ArtifactStore.read_summary(directory)
            except (OSError, ValueError) as error:
                raise RunOpenError(f"invalid run summary: {error}") from error

        timeline: list[TimelineView] = []
        events_path = directory / "workflow-events.jsonl"
        if events_path.is_file() and not events_path.is_symlink():
            for line_number, line in enumerate(
                events_path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                if not line.strip():
                    continue
                try:
                    event = WorkflowEvent.model_validate_json(line)
                except ValueError:
                    warnings.append(
                        f"workflow-events.jsonl line {line_number} is invalid"
                    )
                    continue
                timeline.append(
                    TimelineView(
                        sequence=event.sequence,
                        stage=event.stage.value,
                        state=event.state.value,
                        attempt=event.attempt,
                        step_id=event.step_id,
                        detail=event.detail,
                    )
                )
        timeline.sort(key=lambda item: item.sequence)

        files: list[RunFileView] = []
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(directory).as_posix()
            relative_path = PurePosixPath(relative)
            if _has_symlink_component(directory, relative_path):
                continue
            files.append(
                RunFileView(
                    path=relative,
                    bytes=path.stat().st_size,
                    category=_category(relative),
                )
            )

        manifest = directory / ArtifactStore.manifest_name
        if not manifest.is_file():
            manifest_state = "pending"
            manifest_issues: tuple[str, ...] = ()
            registered = {item.path for item in files}
        else:
            try:
                issues = ArtifactStore(directory.parent).verify(directory)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                issues = [f"invalid manifest: {error}"]
            manifest_state = "invalid" if issues else "verified"
            manifest_issues = tuple(issues)
            if manifest_state == "verified":
                try:
                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                    entries = payload["files"]
                    if not isinstance(entries, dict):
                        raise ValueError("manifest files must be a mapping")
                    registered = {
                        str(relative)
                        for relative, metadata in entries.items()
                        if isinstance(relative, str)
                        and isinstance(metadata, dict)
                        and metadata.get("type") == "file"
                    }
                except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
                    manifest_state = "invalid"
                    manifest_issues = (f"invalid manifest: {error}",)
                    registered = set()
            else:
                registered = set()
            if manifest_state == "invalid":
                warnings.append(
                    "finalized run manifest is invalid; runtime security "
                    "projections are untrusted and were hidden"
                )

        trusted_files = [item for item in files if item.path in registered]
        context_references, skill_references = _context_views(
            directory,
            trusted_files,
            warnings,
        )
        residual_samples = _residual_views(directory, trusted_files, warnings)
        runtime_config = _read_json_projection(
            directory,
            registered,
            "runtime-config.json",
            warnings,
        )
        runtime_provenance = _read_json_projection(
            directory,
            registered,
            "runtime-config-provenance.json",
            warnings,
        )
        execution_risk = _read_json_projection(
            directory,
            registered,
            _latest_attempt_artifact(
                registered,
                "execution-risk-report.json",
            ),
            warnings,
        )
        execution_policy = _read_json_projection(
            directory,
            registered,
            _latest_attempt_artifact(
                registered,
                "execution-policy.json",
            ),
            warnings,
        )
        sandbox_probe = _read_json_projection(
            directory,
            registered,
            _latest_attempt_artifact(
                registered,
                "sandbox-probe.json",
            ),
            warnings,
        )

        return RunSnapshot(
            run_dir=directory,
            summary=summary,
            timeline=tuple(timeline),
            files=tuple(files),
            manifest_state=manifest_state,
            manifest_issues=manifest_issues,
            warnings=tuple(warnings),
            context_references=tuple(context_references),
            skill_references=tuple(skill_references),
            residual_samples=tuple(residual_samples),
            runtime_config=runtime_config,
            runtime_provenance=runtime_provenance,
            execution_risk=execution_risk,
            execution_policy=execution_policy,
            sandbox_probe=sandbox_probe,
        )

    def read_text(
        self,
        snapshot: RunSnapshot,
        relative_path: str,
        *,
        max_bytes: int = 2_097_152,
    ) -> str:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise RunOpenError(f"path is outside opened run: {relative_path}")
        if not relative.parts:
            raise RunOpenError("artifact path is empty")
        root = snapshot.run_dir.resolve()
        if _has_symlink_component(root, relative):
            raise RunOpenError(
                f"symbolic links cannot be opened: {relative_path}"
            )
        candidate = (root / Path(*relative.parts)).resolve()
        if not candidate.is_relative_to(root):
            raise RunOpenError(f"path is outside opened run: {relative_path}")
        registered = {item.path for item in snapshot.files}
        if relative.as_posix() not in registered:
            raise RunOpenError(f"artifact is not registered: {relative_path}")
        if not candidate.is_file():
            raise RunOpenError(f"artifact is not a file: {relative_path}")
        size = candidate.stat().st_size
        if size > max_bytes:
            raise RunOpenError(
                f"artifact exceeds display limit ({size} > {max_bytes})"
            )
        return candidate.read_text(encoding="utf-8", errors="replace")
