from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from foampilot.cli.main import COMMANDS


SOURCE = Path(__file__).parents[1] / "src" / "foampilot"
FORBIDDEN_MODULES = (
    "foampilot.authoring",
    "foampilot.run_service",
    "foampilot.capabilities",
    "foampilot.casespec",
    "foampilot.contracts",
    "foampilot.dictionaries",
    "foampilot.lint",
    "foampilot.mesh",
    "foampilot.pipelines",
    "foampilot.renderers",
    "foampilot.solvers",
    "foampilot.specs",
    "foampilot.agent.orchestrator",
    "foampilot.runtime.runner",
    "foampilot.validation.engine",
)
SUPPORTED_COMMANDS = (
    "validate",
    "plan",
    "solve",
    "inspect",
    "report",
    "preflight",
    "knowledge",
    "skill",
    "audit",
    "qualify",
    "improve",
)


def test_legacy_modules_are_not_importable() -> None:
    assert {
        module
        for module in FORBIDDEN_MODULES
        if importlib.util.find_spec(module) is not None
    } == set()


def test_production_source_has_no_legacy_imports() -> None:
    violations: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            for module in modules:
                if any(
                    module == forbidden
                    or module.startswith(f"{forbidden}.")
                    for forbidden in FORBIDDEN_MODULES
                ):
                    violations.append(
                        f"{path.relative_to(SOURCE)}:{node.lineno}: {module}"
                    )
    assert violations == []


def test_cli_exposes_only_the_lean_command_surface() -> None:
    assert COMMANDS == SUPPORTED_COMMANDS
