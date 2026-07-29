from __future__ import annotations

import ast
from pathlib import Path


SOURCE_ROOT = Path(__file__).parents[1] / "src" / "foampilot"
FORBIDDEN_ROOTS = {
    "src",
    "evaluation",
    "langchain",
    "faiss",
    "openai",
    "anthropic",
}


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
