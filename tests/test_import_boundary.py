from __future__ import annotations

import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "foampilot"
PROJECT_ROOT = SOURCE_ROOT.parents[1]
FORBIDDEN_ROOTS = {
    "src",
    "evaluation",
    "langchain",
    "faiss",
    "openai",
    "anthropic",
}
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
    "foampilot.plans.legacy",
    "foampilot.renderers",
    "foampilot.solvers",
    "foampilot.specs",
    "foampilot.agent.orchestrator",
    "foampilot.runtime.runner",
    "foampilot.validation.engine",
)


def test_independent_source_has_no_foam_agent_or_llm_imports() -> None:
    violations: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots.extend(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.append(node.module.split(".", 1)[0])
            for root in roots:
                if root in FORBIDDEN_ROOTS:
                    violations.append(
                        f"{path.relative_to(SOURCE_ROOT)}:{node.lineno}: {root}"
                    )
    assert violations == []


def test_core_import_does_not_load_forbidden_dependencies() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import json, sys, foampilot; "
                "forbidden=('src','langchain','faiss','openai','anthropic'); "
                "print(json.dumps([name for name in sys.modules "
                "if any(name == item or name.startswith(item + '.') "
                "for item in forbidden)]))"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []


def test_removed_legacy_modules_are_not_importable() -> None:
    assert {
        module
        for module in FORBIDDEN_MODULES
        if importlib.util.find_spec(module) is not None
    } == set()


def test_production_source_has_no_legacy_imports() -> None:
    violations: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
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
                        f"{path.relative_to(SOURCE_ROOT)}:{node.lineno}: "
                        f"{module}"
                    )
    assert violations == []
