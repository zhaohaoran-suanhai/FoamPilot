"""Generic static inspection for Agent-authored native OpenFOAM cases."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re

from foampilot.plans import (
    ExecutionPlan,
    validate_execution_plan,
)
from foampilot.tasks import TaskSpec

from .models import InspectionIssue, InspectionReport


_COMMENTS = re.compile(r"/\*.*?\*/|//.*?$", re.DOTALL | re.MULTILINE)
_APPLICATION = re.compile(
    r"(?m)^\s*application\s+([A-Za-z0-9_.+-]+)\s*;"
)
_FOAM_CLASS = re.compile(
    r"(?m)^\s*class\s+([A-Za-z0-9_.+-]+)\s*;"
)
_FIELD_MIN_MAX_TYPE = re.compile(
    r"(?m)^\s*type\s+fieldMinMax\s*;"
)
_PATCH = re.compile(
    r"(?m)^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*(?:\n\s*)?\{"
)
_TIME_DIRECTORY = re.compile(r"^[0-9]+(?:\.[0-9]+)?(?:\.orig)?$")
_NON_FOAM_SUFFIXES = {
    ".csv",
    ".dat",
    ".geo",
    ".json",
    ".msh",
    ".obj",
    ".stl",
    ".txt",
    ".vtk",
}


def _issue(
    code: str,
    detail: str,
    path: str | None = None,
) -> InspectionIssue:
    return InspectionIssue(code=code, path=path, detail=detail)


def _without_comments(text: str) -> str:
    return _COMMENTS.sub("", text)


def _balanced(text: str) -> bool:
    pairs = {"}": "{", ")": "(", "]": "["}
    stack: list[str] = []
    quote: str | None = None
    escaped = False
    for character in _without_comments(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character in "{([":
            stack.append(character)
        elif character in pairs:
            if not stack or stack.pop() != pairs[character]:
                return False
    return not stack and quote is None


def _looks_like_openfoam_file(relative: str) -> bool:
    path = PurePosixPath(relative)
    if path.suffix.lower() in _NON_FOAM_SUFFIXES:
        return False
    if not path.parts:
        return False
    return (
        path.parts[0] in {"system", "constant"}
        or bool(_TIME_DIRECTORY.fullmatch(path.parts[0]))
    )


def _balanced_block(
    text: str,
    keyword: str,
    opener: str,
    closer: str,
) -> str | None:
    match = re.search(rf"\b{re.escape(keyword)}\b", text)
    if match is None:
        return None
    start = text.find(opener, match.end())
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(text)):
        character = text[index]
        if character == opener:
            depth += 1
        elif character == closer:
            depth -= 1
            if depth == 0:
                return text[start + 1 : index]
    return None


def _patches(text: str) -> set[str]:
    cleaned = _without_comments(text)
    patches: set[str] = set()
    for keyword, opener, closer in (
        ("boundaryField", "{", "}"),
        ("boundary", "(", ")"),
    ):
        block = _balanced_block(cleaned, keyword, opener, closer)
        if block is not None:
            patches.update(_PATCH.findall(block))
    return patches


def _explicit_patch_block(
    text: str,
    *,
    keyword: str,
    opener: str,
    closer: str,
) -> tuple[set[str], bool]:
    cleaned = _without_comments(text)
    block = _balanced_block(cleaned, keyword, opener, closer)
    if block is None:
        return set(), bool(re.search(r"(?m)^\s*#", cleaned))
    unresolved = bool(
        re.search(r"(?m)^\s*#", block)
        or "$" in block
        or '"' in block
    )
    return set(_PATCH.findall(block)), unresolved


def _root_time_field(relative: str, text: str) -> bool:
    path = PurePosixPath(relative)
    if len(path.parts) != 2 or not _TIME_DIRECTORY.fullmatch(path.parts[0]):
        return False
    match = _FOAM_CLASS.search(_without_comments(text))
    return match is not None and match.group(1).endswith("Field")


def _generated_shells(case_root: Path) -> list[Path]:
    shells: list[Path] = []
    for path in case_root.rglob("*"):
        if not path.is_file() or ".foampilot" in path.parts:
            continue
        if (
            path.name in {"Allrun", "Allclean"}
            or path.suffix == ".sh"
            or (
                os.access(path, os.X_OK)
                and path.read_bytes()[:2] == b"#!"
            )
        ):
            shells.append(path)
    return shells


def inspect_native_case(
    *,
    case_root: str | Path,
    task: TaskSpec,
    plan: ExecutionPlan,
    available_executables: set[str],
) -> InspectionReport:
    """Inspect declared files and plan policy without prescribing a solver."""

    root = Path(case_root).resolve()
    issues = [
        _issue(
            f"PLAN_{item.code}",
            item.detail,
            item.location,
        )
        for item in validate_execution_plan(
            plan,
            task,
            available_executables,
        )
    ]
    texts: dict[str, str] = {}
    observed_patches: set[str] = set()
    advisories: list[InspectionIssue] = []

    for declaration in plan.files:
        relative = declaration.path
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            issues.append(
                _issue(
                    "DECLARED_FILE_ESCAPES",
                    "declared file resolves outside the case",
                    relative,
                )
            )
            continue
        if not path.is_file():
            issues.append(
                _issue(
                    "MISSING_DECLARED_FILE",
                    "declared file does not exist",
                    relative,
                )
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            issues.append(
                _issue(
                    "NON_UTF8_DECLARED_FILE",
                    "declared generated file is not UTF-8 text",
                    relative,
                )
            )
            continue
        texts[relative] = text
        for protected in task.protected_paths:
            if protected in text:
                issues.append(
                    _issue(
                        "PROTECTED_REFERENCE",
                        "generated file references a protected path",
                        relative,
                    )
                )
        if _looks_like_openfoam_file(relative):
            if re.search(r"\bFoamFile\s*\{", _without_comments(text)) is None:
                issues.append(
                    _issue(
                        "MISSING_FOAM_HEADER",
                        "native OpenFOAM file has no FoamFile header",
                        relative,
                    )
                )
            if not _balanced(text):
                issues.append(
                    _issue(
                        "UNBALANCED_DELIMITERS",
                        "native OpenFOAM delimiters are unbalanced",
                        relative,
                    )
                )
        observed_patches.update(_patches(text))

    mesh_text = texts.get("system/blockMeshDict")
    if mesh_text is not None:
        mesh_patches, mesh_unresolved = _explicit_patch_block(
            mesh_text,
            keyword="boundary",
            opener="(",
            closer=")",
        )
        if mesh_unresolved:
            advisories.append(
                _issue(
                    "PATCH_COVERAGE_UNVERIFIED",
                    "blockMesh patch coverage uses constructs that the "
                    "lightweight static parser does not resolve",
                    "system/blockMeshDict",
                )
            )
        elif mesh_patches:
            for relative, text in texts.items():
                if not _root_time_field(relative, text):
                    continue
                field_patches, field_unresolved = _explicit_patch_block(
                    text,
                    keyword="boundaryField",
                    opener="{",
                    closer="}",
                )
                if field_unresolved:
                    advisories.append(
                        _issue(
                            "PATCH_COVERAGE_UNVERIFIED",
                            "field patch coverage uses constructs that the "
                            "lightweight static parser does not resolve",
                            relative,
                        )
                    )
                    continue
                missing = sorted(mesh_patches - field_patches)
                if missing:
                    issues.append(
                        _issue(
                            "MISSING_FIELD_PATCH",
                            "explicit boundaryField does not cover mesh "
                            f"patches: {', '.join(missing)}",
                            relative,
                        )
                    )

    control = texts.get("system/controlDict")
    if control is None:
        issues.append(
            _issue(
                "MISSING_CONTROL_DICT",
                "system/controlDict is required for execution",
                "system/controlDict",
            )
        )
    else:
        match = _APPLICATION.search(_without_comments(control))
        if match is None:
            issues.append(
                _issue(
                    "MISSING_APPLICATION",
                    "controlDict has no top-level application entry",
                    "system/controlDict",
                )
            )
        if (
            task.openfoam_target.distribution == "foundation"
            and task.openfoam_target.version == "10"
            and _FIELD_MIN_MAX_TYPE.search(_without_comments(control))
        ):
            issues.append(
                _issue(
                    "UNSUPPORTED_OF10_FUNCTION_OBJECT",
                    "Foundation OpenFOAM v10 does not provide fieldMinMax; "
                    "use supported volFieldValue function objects with "
                    "operation min and max",
                    "system/controlDict",
                )
            )

    for shell in _generated_shells(root):
        issues.append(
            _issue(
                "GENERATED_SHELL",
                "Agent-authored shell entrypoints are forbidden",
                shell.relative_to(root).as_posix(),
            )
        )

    return InspectionReport(
        issues=issues,
        advisories=advisories,
        observed_patches=sorted(observed_patches),
    )
