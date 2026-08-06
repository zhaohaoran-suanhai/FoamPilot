"""Bounded, deterministic evidence selection for one repair request."""

from __future__ import annotations

from hashlib import sha256
from pathlib import PurePosixPath
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from foampilot.plans import ExecutionPlan
from foampilot.tasks import TaskSpec

from .failure import NativeFailureClassification, RepairOperationName


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ContentMode = Literal[
    "full",
    "matching_block",
    "head_tail_excerpt",
    "structure_only",
    "metadata_only",
]


class ScopedFile(StrictModel):
    path: str
    content_mode: ContentMode
    bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    exists: bool
    block: str | None = None
    content: str | None = None


class RepairScope(StrictModel):
    schema_version: Literal[1] = 1
    failure_code: str
    relevant_files: list[ScopedFile] = Field(default_factory=list)
    relevant_commands: list[str] = Field(default_factory=list)
    relevant_knowledge_ids: list[str] = Field(default_factory=list)
    allowed_operations: list[RepairOperationName] = Field(min_length=1)
    earliest_possible_rerun_stage: Literal[
        "inspection",
        "mesh",
        "initialize",
        "solve",
        "postprocess",
    ]
    excluded_file_count: int = Field(ge=0)


class RepairScopeError(ValueError):
    code = "REPAIR_SCOPE_UNRESOLVED"
    message = "无法在上下文预算内确定足够的修复证据。"
    recovery = "请保留原始失败日志并补充可定位的公开文件或命令证据。"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


_MESH_NAMES = {
    "blockMeshDict",
    "snappyHexMeshDict",
    "surfaceFeatureExtractDict",
    "meshQualityDict",
    "topoSetDict",
    "createPatchDict",
    "refineMeshDict",
    "extrudeMeshDict",
    "decomposeParDict",
}


def _is_mesh_path(path: str) -> bool:
    parsed = PurePosixPath(path)
    return (
        parsed.name in _MESH_NAMES
        or path.startswith("constant/polyMesh/")
        or path.startswith("constant/triSurface/")
        or parsed.suffix in {".geo", ".msh"}
    )


def _is_initial_field(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if len(parts) < 2:
        return False
    try:
        return float(parts[0]) == 0.0
    except ValueError:
        return False


def _named_block(text: str, name: str) -> str | None:
    match = re.search(rf"\b{re.escape(name)}\b", text)
    if match is None:
        return None
    opening = text.find("{", match.end())
    if opening < 0:
        return None
    depth = 0
    for index in range(opening, len(text)):
        character = text[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[match.start() : index + 1]
    return None


def _excerpt(text: str, limit: int = 2048) -> str:
    if len(text) <= 2 * limit:
        return text
    return text[:limit] + "\n...<SCOPED_OMISSION>...\n" + text[-limit:]


def _structure(text: str) -> str:
    names = re.findall(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_.:-]*)\s*\{", text)
    return "dictionary_blocks: " + ", ".join(dict.fromkeys(names[:128]))


def _fallback_paths(
    classification: NativeFailureClassification,
    current_files: dict[str, str],
) -> list[str]:
    if classification.domain.value == "mesh":
        return [path for path in current_files if _is_mesh_path(path)]
    if classification.domain.value == "initialization":
        return [
            path
            for path in current_files
            if _is_initial_field(path)
            or PurePosixPath(path).name
            in {"setFieldsDict", "setExprFieldsDict"}
        ]
    if classification.domain.value == "postprocess":
        return [
            path
            for path in current_files
            if "sample" in PurePosixPath(path).name.lower()
            or "probe" in PurePosixPath(path).name.lower()
            or path == "system/controlDict"
        ]
    if classification.domain.value in {"solver", "validation"}:
        preferred = (
            "system/controlDict",
            "system/fvSchemes",
            "system/fvSolution",
        )
        return [path for path in preferred if path in current_files]
    return []


def _stage(
    classification: NativeFailureClassification,
) -> str:
    if classification.domain.value == "inspection":
        return "inspection"
    if (
        classification.domain.value not in {"mesh", "initialization"}
        and any(
            operation in {"add_file", "replace_file"}
            for operation in classification.allowed_operations
        )
    ):
        return "inspection"
    if classification.failed_stage in {
        "mesh",
        "initialize",
        "solve",
        "postprocess",
    }:
        return classification.failed_stage
    if classification.failed_stage == "check":
        return "mesh"
    return "solve"


def build_repair_scope(
    *,
    classification: NativeFailureClassification,
    task: TaskSpec,
    plan: ExecutionPlan,
    current_files: dict[str, str],
    knowledge_ids: tuple[str, ...] | list[str],
    max_full_file_bytes: int = 16 * 1024,
    metadata_only_bytes: int = 1024 * 1024,
) -> RepairScope:
    """Select the smallest useful public representation for repair."""

    del plan  # Reserved for manifest/include dependency refinement.
    if max_full_file_bytes < 1 or metadata_only_bytes <= max_full_file_bytes:
        raise ValueError("repair scope byte thresholds are invalid")
    public_assets = {item.path for item in task.public_assets}
    hinted = list(dict.fromkeys(classification.scope_hints.files))
    selected_paths = hinted or _fallback_paths(classification, current_files)
    selected_paths = [
        path for path in selected_paths if path not in public_assets
    ]
    if not selected_paths and not classification.scope_hints.commands:
        raise RepairScopeError("classification has no locatable evidence")

    block_names = classification.scope_hints.dictionary_blocks
    scoped: list[ScopedFile] = []
    for path in selected_paths:
        content = current_files.get(path)
        if content is None:
            scoped.append(
                ScopedFile(
                    path=path,
                    content_mode="metadata_only",
                    bytes=0,
                    sha256=sha256(b"").hexdigest(),
                    exists=False,
                )
            )
            continue
        encoded = content.encode("utf-8")
        digest = sha256(encoded).hexdigest()
        if any(protected in content for protected in task.protected_paths):
            scoped.append(
                ScopedFile(
                    path=path,
                    content_mode="metadata_only",
                    bytes=len(encoded),
                    sha256=digest,
                    exists=True,
                )
            )
            continue
        block_content: str | None = None
        block_name: str | None = None
        for candidate in block_names:
            if (extracted := _named_block(content, candidate)) is not None:
                block_content = extracted
                block_name = candidate
                break
        if block_content is not None and len(encoded) > max_full_file_bytes:
            scoped.append(
                ScopedFile(
                    path=path,
                    content_mode="matching_block",
                    bytes=len(encoded),
                    sha256=digest,
                    exists=True,
                    block=block_name,
                    content=block_content,
                )
            )
        elif len(encoded) >= metadata_only_bytes:
            scoped.append(
                ScopedFile(
                    path=path,
                    content_mode="metadata_only",
                    bytes=len(encoded),
                    sha256=digest,
                    exists=True,
                )
            )
        elif len(encoded) > max_full_file_bytes:
            representation = (
                _structure(content)
                if "{" in content and "}" in content
                else _excerpt(content)
            )
            mode: ContentMode = (
                "structure_only"
                if representation.startswith("dictionary_blocks:")
                else "head_tail_excerpt"
            )
            scoped.append(
                ScopedFile(
                    path=path,
                    content_mode=mode,
                    bytes=len(encoded),
                    sha256=digest,
                    exists=True,
                    content=representation,
                )
            )
        else:
            scoped.append(
                ScopedFile(
                    path=path,
                    content_mode="full",
                    bytes=len(encoded),
                    sha256=digest,
                    exists=True,
                    content=content,
                )
            )

    if not scoped and not classification.scope_hints.commands:
        raise RepairScopeError("all candidate evidence is protected")
    return RepairScope(
        failure_code=classification.code,
        relevant_files=scoped,
        relevant_commands=list(
            dict.fromkeys(classification.scope_hints.commands)
        ),
        relevant_knowledge_ids=list(dict.fromkeys(knowledge_ids)),
        allowed_operations=classification.allowed_operations,
        earliest_possible_rerun_stage=_stage(classification),
        excluded_file_count=max(0, len(current_files) - len(scoped)),
    )


__all__ = [
    "ContentMode",
    "RepairScope",
    "RepairScopeError",
    "ScopedFile",
    "build_repair_scope",
]
