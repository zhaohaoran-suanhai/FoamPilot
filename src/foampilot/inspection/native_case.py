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
        observed_patches=sorted(observed_patches),
    )
