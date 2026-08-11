"""Safe project inputs for the interactive desktop workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
from uuid import uuid4

import yaml

from foampilot.taskbuilder import TaskDraft


class DesktopWorkspaceError(ValueError):
    """The selected workspace or task draft is unsafe or invalid."""


def _atomic_text(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@dataclass(frozen=True, slots=True)
class DesktopWorkspace:
    root: Path
    requests_dir: Path
    drafts_dir: Path
    tasks_dir: Path
    runs_dir: Path

    @classmethod
    def open(cls, path: str | Path) -> "DesktopWorkspace":
        source = Path(path)
        if source.is_symlink():
            raise DesktopWorkspaceError(
                f"workspace root is a symbolic link: {source}"
            )
        source.mkdir(parents=True, exist_ok=True)
        root = source.resolve()
        if not root.is_dir():
            raise DesktopWorkspaceError(f"workspace is not a directory: {root}")
        directories: list[Path] = []
        for name in ("requests", "drafts", "tasks", "runs"):
            directory = root / name
            if directory.is_symlink():
                raise DesktopWorkspaceError(
                    f"workspace subdirectory is a symbolic link: {directory}"
                )
            directory.mkdir(exist_ok=True)
            directories.append(directory)
        return cls(root, *directories)

    @staticmethod
    def _next(directory: Path, stem: str, suffix: str) -> Path:
        for index in range(1, 10_000):
            candidate = directory / f"{stem}-{index:03d}{suffix}"
            if not candidate.exists():
                return candidate
        raise DesktopWorkspaceError(f"no version slot remains for {stem}")

    def save_request(self, text: str) -> Path:
        normalized = text.strip()
        if not normalized:
            raise DesktopWorkspaceError("request is blank")
        path = self._next(self.requests_dir, "request", ".md")
        _atomic_text(path, normalized + "\n")
        return path

    def save_draft(self, text: str) -> Path:
        if not text.strip():
            raise DesktopWorkspaceError("TaskDraft is blank")
        path = self._next(self.drafts_dir, "task-draft", ".yaml")
        _atomic_text(path, text)
        return path

    def save_task(self, text: str) -> Path:
        if not text.strip():
            raise DesktopWorkspaceError("TaskSpec is blank")
        path = self._next(self.tasks_dir, "task", ".yaml")
        _atomic_text(path, text)
        return path

    def reserve_draft_path(self) -> Path:
        return self._next(self.drafts_dir, "task-draft", ".yaml")

    def reserve_task_path(self) -> Path:
        return self._next(self.tasks_dir, "task", ".yaml")

    def create_job_root(self) -> Path:
        for _ in range(10):
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            path = self.runs_dir / f"job-{timestamp}-{uuid4().hex[:8]}"
            try:
                path.mkdir(exist_ok=False)
            except FileExistsError:
                continue
            return path
        raise DesktopWorkspaceError("could not allocate a unique desktop job")


def _answer_value(raw: object, candidate: object, question_id: str) -> object:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if candidate is None:
            raise DesktopWorkspaceError(
                f"answer is required for question {question_id}"
            )
        return candidate
    if isinstance(raw, str):
        value = yaml.safe_load(raw)
        if value is None and raw.strip() not in {"null", "~"}:
            raise DesktopWorkspaceError(
                f"answer is required for question {question_id}"
            )
        return value
    return raw


def confirm_task_draft(text: str, answers: dict[str, object]) -> str:
    """Apply explicit desktop confirmations to one auditable TaskDraft."""

    try:
        payload = yaml.safe_load(text)
        if not isinstance(payload, dict):
            raise ValueError("TaskDraft root must be a mapping")
        draft = TaskDraft.model_validate(payload)
    except (ValueError, yaml.YAMLError) as error:
        raise DesktopWorkspaceError(f"TaskDraft is invalid: {error}") from error

    result = draft.model_dump(mode="json")
    facts = result["facts"]
    assert isinstance(facts, list)
    by_path = {
        item["path"]: item
        for item in facts
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    for item in facts:
        if not isinstance(item, dict):
            continue
        if item.get("impact") in {"medium", "high"} and not item.get(
            "confirmed"
        ):
            item["source"] = "user_confirmation"
            item["confirmed"] = True
            item["evidence"] = (
                str(item.get("evidence", "")).strip()
                + "; desktop user confirmed"
            ).strip("; ")

    assumptions = result["assumptions"]
    assert isinstance(assumptions, list)
    retained_assumptions: list[object] = []
    for assumption in assumptions:
        if (
            isinstance(assumption, dict)
            and assumption.get("source") == "model_inference"
            and assumption.get("impact") in {"medium", "high"}
        ):
            path = str(assumption["path"])
            if path not in by_path:
                fact = {
                    "path": path,
                    "value": assumption["value"],
                    "source": "user_confirmation",
                    "evidence": "desktop user confirmed model assumption",
                    "impact": assumption["impact"],
                    "confirmed": True,
                }
                facts.append(fact)
                by_path[path] = fact
        else:
            retained_assumptions.append(assumption)
    result["assumptions"] = retained_assumptions

    questions = result["unresolved_questions"]
    assert isinstance(questions, list)
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = str(question["question_id"])
        value = _answer_value(
            answers.get(question_id),
            question.get("candidate"),
            question_id,
        )
        path = str(question["path"])
        fact = {
            "path": path,
            "value": value,
            "source": "user_confirmation",
            "evidence": f"desktop answer to {question_id}",
            "impact": "high" if question.get("kind") == "blocking" else "medium",
            "confirmed": True,
        }
        if path in by_path:
            by_path[path].update(fact)
        else:
            facts.append(fact)
            by_path[path] = fact
    result["unresolved_questions"] = []
    result["status"] = "confirmed"
    try:
        confirmed = TaskDraft.model_validate(result)
    except ValueError as error:
        raise DesktopWorkspaceError(
            f"confirmed TaskDraft is invalid: {error}"
        ) from error
    return yaml.safe_dump(
        confirmed.model_dump(mode="json"),
        sort_keys=False,
        allow_unicode=True,
    )


__all__ = [
    "DesktopWorkspace",
    "DesktopWorkspaceError",
    "confirm_task_draft",
]
