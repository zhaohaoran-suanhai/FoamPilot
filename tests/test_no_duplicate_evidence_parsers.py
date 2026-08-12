from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src/foampilot"
CANONICAL_CONSUMERS = (
    "agent",
    "acceptance",
    "postprocessing",
    "workflow",
    "qualification",
    "cli",
    "desktop",
)


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


def test_only_evidence_package_owns_openfoam_log_extraction() -> None:
    violations: list[str] = []
    for relative_root in CANONICAL_CONSUMERS:
        for path in sorted((SOURCE_ROOT / relative_root).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            imports = _imports(path)
            if "parse_openfoam_log" in source:
                violations.append(str(path.relative_to(SOURCE_ROOT)))
            if any(
                module == "foampilot.validation.native"
                or module.startswith("foampilot.validation.native.")
                or module == "foampilot.validation.public_checks"
                or module.startswith("foampilot.validation.public_checks.")
                for module in imports
            ):
                violations.append(str(path.relative_to(SOURCE_ROOT)))

    assert violations == []


def test_legacy_validation_is_not_imported_by_canonical_workflow() -> None:
    forbidden = {
        "foampilot.validation",
        "foampilot.validation.models",
        "foampilot.validation.native",
        "foampilot.validation.public_checks",
    }
    violations: list[str] = []
    for relative_root in (
        "agent",
        "acceptance",
        "postprocessing",
        "workflow",
    ):
        for path in sorted((SOURCE_ROOT / relative_root).rglob("*.py")):
            if any(
                module in forbidden or module.startswith("foampilot.validation.")
                for module in _imports(path)
            ):
                violations.append(str(path.relative_to(SOURCE_ROOT)))

    assert violations == []

