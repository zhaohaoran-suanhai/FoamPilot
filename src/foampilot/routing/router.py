"""Evidence-first routing before public context retrieval."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from foampilot.environment import EnvironmentSnapshot
from foampilot.knowledge import (
    KnowledgeEntry,
    KnowledgeQuery,
    select_knowledge,
)
from foampilot.models import (
    ModelBudgetWindow,
    ModelGateway,
    ModelRequest,
    ModelTraceSink,
)
from foampilot.tasks import TaskSpec

from .confidence import RouteEvidenceState, calculate_confidence
from .models import (
    CapabilityConfidence,
    CapabilityProfile,
    RouteEvidence,
    RouteSuggestion,
    RoutingError,
)
from .registry import SOLVER_CAPABILITIES, capability_for_solver


def _public_text(task: TaskSpec) -> str:
    return " ".join(
        (
            task.title,
            task.prompt,
            *task.required_outputs,
            *task.acceptance_requirements,
        )
    )


def _contains(text: str, *patterns: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) is not None for pattern in patterns)


def _fact_value(
    text: str,
    *,
    options: tuple[tuple[str, tuple[str, ...]], ...],
) -> str:
    for value, patterns in options:
        if _contains(text, *patterns):
            return value
    return "unknown"


def _task_facts(task: TaskSpec) -> dict[str, str | bool]:
    text = _public_text(task)
    regime = _fact_value(
        text,
        options=(
            ("transient", (r"\btransient\b", r"\bunsteady\b", r"time-dependent")),
            ("steady", (r"\bsteady\b", r"steady-state")),
        ),
    )
    compressibility = _fact_value(
        text,
        options=(
            (
                "incompressible",
                (r"\bincompressible\b", r"\bimmiscible\b"),
            ),
            ("compressible", (r"\bcompressible\b", r"shock[- ]tube")),
        ),
    )
    phase_family = _fact_value(
        text,
        options=(
            (
                "vof",
                (
                    r"\bvof\b",
                    r"free[- ]surface",
                    r"\bimmiscible\b",
                    r"two[- ]phase",
                    r"two (?:incompressible )?fluids",
                ),
            ),
            ("multiphase", (r"\bmultiphase\b",)),
            (
                "single_phase",
                (
                    r"single[- ]phase",
                    r"perfect[- ]gas",
                    r"\bdiaphragm\b",
                    r"\bnewtonian fluid\b",
                    r"\bfluid is newtonian\b",
                ),
            ),
        ),
    )
    energy = _fact_value(
        text,
        options=(
            (
                "enabled",
                (
                    r"\btemperature\b",
                    r"\bthermal\b",
                    r"\bheat\b",
                    r"\bbuoyant\b",
                    r"conjugate",
                ),
            ),
            ("disabled", (r"\bisothermal\b",)),
        ),
    )
    turbulence = _fact_value(
        text,
        options=(
            ("laminar", (r"\blaminar\b",)),
            ("rans", (r"\brans\b", r"\bk[- ]?epsilon\b", r"\bk[- ]?omega\b")),
            ("les", (r"\bles\b", r"large[- ]eddy")),
        ),
    )
    mesh_family = _fact_value(
        text,
        options=(
            ("blockMesh", (r"\bblockmesh\b", r"structured hexa")),
            ("snappyHexMesh", (r"\bsnappyhexmesh\b",)),
            ("gmsh", (r"\bgmsh\b",)),
            ("extrudeMesh", (r"\bextrudemesh\b",)),
        ),
    )
    physics_family = _fact_value(
        text,
        options=(
            ("conjugate_heat_transfer", (r"conjugate", r"\bcht\b")),
            ("solid_mechanics", (r"solid displacement", r"\belastic")),
            (
                "magnetohydrodynamics",
                (r"magnetohydro", r"\bmhd\b"),
            ),
            ("electromagnetics", (r"electrostatic",)),
            ("shallow_water", (r"shallow water",)),
            ("fluid", (r"\bflow\b", r"\bfluid\b", r"\bpressure\b")),
        ),
    )
    if (
        phase_family == "unknown"
        and physics_family == "fluid"
        and compressibility != "unknown"
    ):
        phase_family = "single_phase"
    return {
        "regime": regime,
        "compressibility": compressibility,
        "phase_family": phase_family,
        "energy": energy,
        "turbulence": turbulence,
        "mesh_family": mesh_family,
        "physics_family": physics_family,
        "parallel_expected": _contains(text, r"\bparallel\b", r"\bmpi\b"),
    }


def _mesh_fact(task: TaskSpec, lexical_value: str) -> tuple[str, RouteEvidence | None]:
    if task.mesh is not None and task.mesh.strategy != "auto":
        value = task.mesh.strategy
        return value, RouteEvidence(
            source="task.mesh",
            fact=f"explicit mesh strategy {value}",
        )
    if task.geometry is None:
        return lexical_value, None
    by_mode = {
        "parametric": "blockMesh",
        "surface": "snappyHexMesh",
        "gmsh": "gmsh",
        "openfoam_mesh": "provided",
    }
    value = by_mode[task.geometry.mode]
    return value, RouteEvidence(
        source="task.geometry",
        fact=f"geometry mode {task.geometry.mode} selects mesh strategy {value}",
    )


def _required_mesh_executables(mesh_family: str) -> set[str]:
    return {
        "blockMesh": {"blockMesh"},
        "snappyHexMesh": {"blockMesh", "snappyHexMesh"},
        "gmsh": {"gmsh", "gmshToFoam"},
        "provided": {"checkMesh"},
    }.get(mesh_family, set())


def _known_solvers(corpus: Sequence[KnowledgeEntry]) -> set[str]:
    result = set(SOLVER_CAPABILITIES)
    for entry in corpus:
        if entry.knowledge_type == "solver_guide":
            result.update(entry.solvers)
    return result


def _explicit_solver(
    text: str,
    corpus: Sequence[KnowledgeEntry],
) -> str | None:
    matches = [
        solver
        for solver in _known_solvers(corpus)
        if re.search(
            rf"(?<![A-Za-z0-9_.+-]){re.escape(solver)}"
            rf"(?![A-Za-z0-9_+-])",
            text,
            flags=re.IGNORECASE,
        )
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _knowledge_candidates(
    task: TaskSpec,
    corpus: Sequence[KnowledgeEntry],
    installed: set[str],
) -> list[str]:
    matches = select_knowledge(
        list(corpus),
        KnowledgeQuery(
            text=_public_text(task),
            knowledge_types=("solver_guide",),
            formal=True,
            limit=20,
        ),
    )
    if not matches:
        return []
    by_id = {entry.id: entry for entry in corpus}
    facts = _task_facts(task)
    scored: list[tuple[int, str]] = []
    for match in matches:
        entry = by_id[match.entry_id]
        for solver in entry.solvers:
            if solver in installed and (match.score, solver) not in scored:
                scored.append((match.score, solver))

    compatible_known = [
        (score, solver)
        for score, solver in scored
        if (
            (capability := capability_for_solver(solver)) is not None
            and all(
                facts[field] == "unknown"
                or getattr(capability, field) == "unknown"
                or facts[field] == getattr(capability, field)
                for field in (
                    "regime",
                    "compressibility",
                    "phase_family",
                    "energy",
                    "turbulence",
                    "physics_family",
                )
            )
        )
    ]
    pool = compatible_known or scored
    if not pool:
        return []
    top_score = max(score for score, _solver in pool)
    return [
        solver
        for score, solver in pool
        if score == top_score
    ]


def _merge_solver_facts(
    facts: dict[str, str | bool],
    solver: str | None,
) -> tuple[dict[str, str | bool], list[str]]:
    merged = dict(facts)
    conflicts: list[str] = []
    capability = capability_for_solver(solver)
    if capability is None:
        return merged, conflicts
    for field in (
        "regime",
        "compressibility",
        "phase_family",
        "energy",
        "turbulence",
        "physics_family",
    ):
        task_value = str(merged[field])
        registry_value = str(getattr(capability, field))
        if task_value == "unknown":
            merged[field] = registry_value
        elif registry_value != "unknown" and task_value != registry_value:
            conflicts.append(
                f"task {field}={task_value} conflicts with "
                f"{solver} {field}={registry_value}"
            )
    return merged, conflicts


def _solver_family(solver: str | None) -> str | None:
    capability = capability_for_solver(solver)
    if capability is not None:
        return capability.family
    if solver:
        return solver
    return None


def _profile(
    *,
    facts: dict[str, str | bool],
    solver: str | None,
    confidence: CapabilityConfidence,
    evidence: list[RouteEvidence],
    unresolved: list[str],
) -> CapabilityProfile:
    return CapabilityProfile(
        physics_family=str(facts["physics_family"]),
        regime=str(facts["regime"]),
        compressibility=str(facts["compressibility"]),
        phase_family=str(facts["phase_family"]),
        energy=str(facts["energy"]),
        turbulence=str(facts["turbulence"]),
        solver_family=_solver_family(solver),
        solver_executable=solver,
        mesh_family=str(facts["mesh_family"]),
        parallel_expected=bool(facts["parallel_expected"]),
        confidence=confidence,
        evidence=evidence,
        unresolved_questions=unresolved,
    )


def route_capability(
    task: TaskSpec,
    environment: EnvironmentSnapshot,
    corpus: Sequence[KnowledgeEntry],
    *,
    gateway: ModelGateway | None = None,
    budget: ModelBudgetWindow | None = None,
    trace: ModelTraceSink | None = None,
) -> CapabilityProfile:
    """Resolve a capability profile or fail instead of silently guessing."""

    text = _public_text(task)
    installed = environment.executable_names
    explicit = _explicit_solver(text, corpus)
    candidates = (
        [explicit]
        if explicit is not None
        else _knowledge_candidates(task, corpus, installed)
    )
    evidence: list[RouteEvidence] = []
    if explicit is not None:
        evidence.append(
            RouteEvidence(
                source="task.prompt",
                fact=f"explicit solver {explicit}",
            )
        )
    elif len(candidates) == 1:
        evidence.append(
            RouteEvidence(
                source="knowledge",
                fact=f"unique compatible solver candidate {candidates[0]}",
            )
        )

    used_model = False
    selected = candidates[0] if len(candidates) == 1 else explicit
    model_unresolved: list[str] = []
    if len(candidates) > 1 and gateway is not None:
        if budget is None or trace is None:
            raise ValueError("route model requires budget and trace")
        used_model = True
        suggestion = gateway.generate_structured(
            ModelRequest(
                purpose="route-openfoam-capability",
                system_prompt=(
                    "只能依据公开任务和已安装 candidate 选择一个 candidate。返回 "
                    "candidate、evidence 与 unresolved_questions；不要返回 confidence。"
                ),
                user_prompt=json.dumps(
                    {
                        "task": task.agent_payload(),
                        "installed_candidates": candidates,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
            RouteSuggestion,
            budget=budget,
            trace=trace,
        ).value
        if suggestion.candidate in candidates:
            selected = suggestion.candidate
        evidence.extend(suggestion.evidence)
        model_unresolved.extend(suggestion.unresolved_questions)

    public_facts = _task_facts(task)
    mesh_family, mesh_evidence = _mesh_fact(
        task,
        str(public_facts["mesh_family"]),
    )
    public_facts["mesh_family"] = mesh_family
    if mesh_evidence is not None:
        evidence.append(mesh_evidence)
    facts, conflicts = _merge_solver_facts(public_facts, selected)
    missing_mesh_executables = sorted(
        _required_mesh_executables(mesh_family)
        - environment.available_executable_names
    )
    public_critical_complete = all(
        public_facts[field] != "unknown"
        for field in ("regime", "compressibility", "phase_family")
    )
    critical_complete = explicit is not None or public_critical_complete
    installed_selected = selected is not None and selected in installed
    state = RouteEvidenceState(
        explicit_solver=explicit is not None,
        solver_installed=installed_selected,
        has_conflict=bool(conflicts or missing_mesh_executables),
        compatible_candidate_count=len(candidates),
        critical_physics_complete=critical_complete,
        used_model_route=used_model,
    )
    confidence = calculate_confidence(state)
    unresolved = list(model_unresolved)
    unresolved.extend(conflicts)
    unresolved.extend(
        f"required mesh executable is unavailable: {name}"
        for name in missing_mesh_executables
    )
    if explicit is not None and explicit not in installed:
        unresolved.append(f"explicit solver is not installed: {explicit}")
    if not critical_complete:
        unresolved.extend(
            f"public task does not resolve {field}"
            for field in ("regime", "compressibility", "phase_family")
            if public_facts[field] == "unknown"
        )
    if len(candidates) > 1:
        unresolved.append(
            "multiple compatible solver candidates remain: "
            + ", ".join(candidates)
        )
    if selected is None and critical_complete:
        unresolved.append("no compatible installed solver candidate")

    profile = _profile(
        facts=facts,
        solver=selected,
        confidence=confidence,
        evidence=evidence,
        unresolved=unresolved,
    )
    if confidence != CapabilityConfidence.LOW:
        return profile
    code = (
        "REQUEST_INCOMPLETE"
        if not critical_complete
        else "ROUTING_UNRESOLVED"
    )
    raise RoutingError(
        code,
        profile,
        model_route_used=used_model,
    )
