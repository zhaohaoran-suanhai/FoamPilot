"""Typed repair routing and scoped application of authorized changes."""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from foampilot.authoring.models import CaseBundle
from foampilot.inspection.models import InspectionReport
from foampilot.plans.models import GeneratedFile
from foampilot.simulation.design import ExtensionDecision
from foampilot.simulation.provenance import FactEvidence, ResolvedValue
from foampilot.simulation.risk_gate import CaseDesign

from .envelope import authorize_repair
from .models import (
    DerivedCaseDesignRecord,
    RepairAuthorization,
    RepairCategory,
    RepairDecision,
    RepairPolicy,
    RepairProposal,
)

if TYPE_CHECKING:
    from foampilot.extensions import CapabilityRegistry
    from foampilot.preprocessing import InputMeshFacts


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AuthorizedRepairResult(StrictFrozenModel):
    derived: DerivedCaseDesignRecord
    design: CaseDesign
    bundle: CaseBundle
    conformance: InspectionReport


def _authorize_existing(
    *,
    proposal: RepairProposal,
    design: CaseDesign,
    policy: RepairPolicy,
) -> RepairDecision:
    authorization = authorize_repair(
        proposal=proposal,
        design=design,
        policy=policy,
    )
    state = {
        "AUTHORIZED_AUTOMATIC": "AUTHORIZED_NUMERICAL_PATCH",
        "CONFIRMATION_REQUIRED": "CONFIRMATION_REQUIRED",
        "FINALIZE_FAILED": "FINALIZE_FAILED",
    }[authorization.state]
    return RepairDecision(
        state=state,
        reason_codes=authorization.reason_codes,
        proposal=proposal,
        confirmation_paths=authorization.confirmation_paths,
    )


def coordinate_repair(
    *,
    category: RepairCategory,
    design: CaseDesign,
    policy: RepairPolicy,
    proposal: RepairProposal | None = None,
    gateway: object | None = None,
) -> RepairDecision:
    """Route category before any optional model diagnostic is invoked."""

    if category == "mechanical":
        return RepairDecision(
            state="MECHANICAL_PATCH",
            reason_codes=("DETERMINISTIC_MECHANICAL_REPAIR",),
        )
    if category in {"physical", "capability"}:
        if proposal is None:
            return RepairDecision(
                state="CONFIRMATION_REQUIRED",
                reason_codes=("REPAIR_CATEGORY_REQUIRES_CONFIRMATION",),
            )
        return _authorize_existing(
            proposal=proposal,
            design=design,
            policy=policy,
        )
    if not policy.automatic_numerical_repair:
        return RepairDecision(
            state="FINALIZE_FAILED",
            reason_codes=("AUTOMATIC_NUMERICAL_REPAIR_DISABLED",),
        )
    if proposal is None:
        if not policy.model_diagnostic:
            return RepairDecision(
                state="FINALIZE_FAILED",
                reason_codes=("MODEL_DIAGNOSTIC_DISABLED",),
            )
        if gateway is None or not hasattr(gateway, "propose_repair"):
            return RepairDecision(
                state="FINALIZE_FAILED",
                reason_codes=("REPAIR_PROPOSAL_UNAVAILABLE",),
            )
        candidate = gateway.propose_repair()
        if not isinstance(candidate, RepairProposal):
            raise TypeError("gateway.propose_repair must return RepairProposal")
        proposal = candidate
    return _authorize_existing(
        proposal=proposal,
        design=design,
        policy=policy,
    )


def _updated_fact(fact: ResolvedValue, new_value: object) -> ResolvedValue:
    return fact.model_copy(
        update={
            "value": new_value,
            "source": "deterministic_rule",
            "confirmed": True,
            "evidence": (
                *fact.evidence,
                FactEvidence(
                    kind="repair_authorization",
                    detail="change authorized by the frozen numerical envelope",
                ),
            ),
        }
    )


def _derive_design(
    design: CaseDesign,
    proposal: RepairProposal,
) -> CaseDesign:
    replacements = {
        item.field_path: item.new_value for item in proposal.design_changes
    }
    source = design.proposal
    updates: dict[str, object] = {}
    for section in (
        "physical_models",
        "materials",
        "boundary_designs",
        "initial_conditions",
        "time_design",
        "numerical_design",
        "region_models",
    ):
        updates[section] = tuple(
            _updated_fact(item, replacements[item.field_path])
            if item.field_path in replacements
            else item
            for item in getattr(source, section)
        )
    updates["solver_family"] = (
        _updated_fact(source.solver_family, replacements["solver.family"])
        if "solver.family" in replacements
        else source.solver_family
    )
    decisions: list[ExtensionDecision] = []
    for decision in source.extension_decisions:
        decisions.append(
            decision.model_copy(
                update={
                    "values": tuple(
                        _updated_fact(item, replacements[item.field_path])
                        if item.field_path in replacements
                        else item
                        for item in decision.values
                    )
                }
            )
        )
    updates["extension_decisions"] = tuple(decisions)
    derived_proposal = source.model_copy(update=updates)
    observed_paths = {
        item.field_path for item in derived_proposal.iter_values()
    }
    missing = sorted(set(replacements) - observed_paths)
    if missing:
        raise ValueError("REPAIR_DESIGN_PATH_UNKNOWN: " + ", ".join(missing))

    payload = {
        "schema_version": design.schema_version,
        "proposal": derived_proposal,
        "intent_sha256": design.intent_sha256,
        "proposal_sha256": __import__(
        "foampilot.simulation.io", fromlist=["canonical_sha256"]
        ).canonical_sha256(derived_proposal),
        "confirmation_ids": design.confirmation_ids,
        "extension_identities": design.extension_identities,
        "numerical_repair_envelope": design.numerical_repair_envelope,
    }
    canonical = json.dumps(
        CaseDesign.model_construct(
            **payload,
            design_sha256="0" * 64,
        ).model_dump(mode="json", exclude={"design_sha256"}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return CaseDesign(
        **payload,
        design_sha256=sha256(canonical).hexdigest(),
    )


def _statement_pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?m)^(?P<indent>\s*){re.escape(keyword)}\s+[^;\n]+;(?P<trail>\s*(?://[^\n]*)?)$"
    )


def _semantic_remainder(content: str, keywords: tuple[str, ...]) -> str:
    normalized = content
    for keyword in sorted(keywords):
        pattern = _statement_pattern(keyword)
        if len(pattern.findall(normalized)) != 1:
            raise ValueError(
                f"REPAIR_AUTHORIZED_KEYWORD_AMBIGUOUS: {keyword}"
            )
        normalized = pattern.sub(
            lambda match: f"{match.group('indent')}{keyword} <AUTHORIZED>;{match.group('trail')}",
            normalized,
        )
    return normalized


def apply_authorized_repair(
    *,
    proposal: RepairProposal,
    authorization: RepairAuthorization,
    design: CaseDesign,
    bundle: CaseBundle,
    mesh_facts: tuple[InputMeshFacts, ...],
    extensions: CapabilityRegistry,
    public_asset_install_paths: tuple[str, ...],
    protected_paths: tuple[str, ...],
) -> AuthorizedRepairResult:
    """Apply only declared file effects, then re-run design conformance."""

    if authorization.state != "AUTHORIZED_AUTOMATIC":
        raise ValueError("REPAIR_NOT_AUTHORIZED")
    if set(authorization.authorized_paths) != {
        item.field_path for item in proposal.design_changes
    }:
        raise ValueError("REPAIR_AUTHORIZATION_PATH_MISMATCH")

    rules = {
        item.field_path: item
        for item in design.numerical_repair_envelope.rules
    }
    allowed_files: dict[str, list[str]] = {}
    for path in authorization.authorized_paths:
        rule = rules[path]
        for authored in rule.authored_paths:
            allowed_files.setdefault(authored, []).append(rule.dictionary_keyword)

    current = {item.path: item for item in bundle.files}
    order = [item.path for item in bundle.files]
    for operation in proposal.file_operations:
        if operation.path not in allowed_files:
            raise ValueError(f"REPAIR_FILE_NOT_AUTHORIZED: {operation.path}")
        if operation.operation != "replace" or operation.path not in current:
            raise ValueError(f"REPAIR_FILE_OPERATION_NOT_AUTHORIZED: {operation.path}")
        if any(path in operation.content for path in protected_paths):
            raise ValueError("REPAIR_PROTECTED_PATH_LEAK")
        if any(
            operation.path == asset or operation.path.startswith(f"{asset}/")
            for asset in public_asset_install_paths
        ):
            raise ValueError("REPAIR_PUBLIC_ASSET_OVERWRITE")
        previous = current[operation.path].content
        keywords = tuple(allowed_files[operation.path])
        if _semantic_remainder(previous, keywords) != _semantic_remainder(
            operation.content,
            keywords,
        ):
            raise ValueError(f"UNDECLARED_SEMANTIC_CHANGE: {operation.path}")
        current[operation.path] = GeneratedFile(
            path=operation.path,
            content=operation.content,
        )

    if set(allowed_files) != {item.path for item in proposal.file_operations}:
        raise ValueError("REPAIR_AUTHORIZED_FILE_MISSING")

    derived = _derive_design(design, proposal)
    revised_bundle = bundle.model_copy(
        update={"files": [current[path] for path in order]}
    )
    from foampilot.inspection.design_conformance import verify_design_conformance

    conformance = verify_design_conformance(
        design=derived,
        bundle=revised_bundle,
        mesh_facts=mesh_facts,
        extensions=extensions,
    )
    if not conformance.passed:
        raise ValueError(
            "REPAIR_DESIGN_CONFORMANCE_FAILED: "
            + ", ".join(item.code for item in conformance.issues)
        )
    return AuthorizedRepairResult(
        derived=DerivedCaseDesignRecord(
            parent_design_sha256=design.design_sha256,
            design_sha256=derived.design_sha256,
            changed_paths=authorization.authorized_paths,
        ),
        design=derived,
        bundle=revised_bundle,
        conformance=conformance,
    )


__all__ = [
    "AuthorizedRepairResult",
    "apply_authorized_repair",
    "coordinate_repair",
]
