"""Leakage-safe prompt for one complete native OpenFOAM case bundle."""

from __future__ import annotations

import json

from foampilot.environment import EnvironmentSnapshot
from foampilot.tasks import TaskSpec


_BUNDLE_SYSTEM = """You are an OpenFOAM engineering agent.
Starting from an empty case directory, return every complete native OpenFOAM
file and every typed command needed to solve the public task. Choose the
installed solver, mesh workflow, initialization utilities, numerical settings,
and controls needed by the physics.
Generate only files and commands required to solve the case.
Do not add function objects, sampling, extrema, or residualControl solely to produce evaluation evidence.
The evaluator derives measurements from solver logs and written fields after a successful solve.
Do not assume access to a tutorial, golden result, private evaluator, shell, or
deterministic case renderer.

Commands execute with cwd=/case. Return executable and argv separately; never
return shell syntax, an Allrun script, or external paths. Keep MPI ranks and
the sum of command timeouts within the public resource budget. Each generated
file must have a safe case-relative path and complete UTF-8 content. Use
Foundation OpenFOAM v10 syntax and make field boundary patches match the mesh.
For MPI, set the solver executable and mpi_ranks; never emit mpirun or orterun.
Use plain checkMesh unless the public task explicitly requires stricter flags;
do not add -allGeometry or -allTopology as an extra qualification gate.
The public request and acceptance requirements are authoritative."""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def bundle_request_text(
    task: TaskSpec,
    environment: EnvironmentSnapshot,
    knowledge_text: str,
    skills_text: str,
) -> tuple[str, str]:
    user = "\n\n".join(
        (
            "PUBLIC TASK\n" + _json(task.agent_payload()),
            "INSTALLED ENVIRONMENT\n" + _json(environment.agent_payload()),
            "DYNAMIC PUBLIC KNOWLEDGE\n" + knowledge_text,
            "PORTABLE WORKFLOW SKILL\n" + skills_text,
        )
    )
    for protected in task.protected_paths:
        if protected in user:
            raise ValueError("model prompt contains a protected path")
    return _BUNDLE_SYSTEM, user
