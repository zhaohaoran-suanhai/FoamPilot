"""Strict contracts for public and development OpenFOAM knowledge."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


PILOT_FAMILIES = frozenset(
    {
        "buoyant-cavity",
        "compressible-shock-tube",
        "laminar-cavity",
        "multiphase-dam-break",
        "potential-cylinder",
        "rans-pitzdaily",
    }
)

StableID = Annotated[
    str,
    StringConstraints(pattern=r"^of10\.[a-z0-9]+(?:[.-][a-z0-9]+)*$"),
]
LowerToken = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeApplicability(StrictModel):
    conditions: Annotated[list[NonEmpty], Field(min_length=1)]
    not_applicable: Annotated[list[NonEmpty], Field(min_length=1)]


class KnowledgeSource(StrictModel):
    kind: Literal[
        "official_source",
        "official_documentation",
        "reviewed_engineering",
        "pilot_derived",
    ]
    title: NonEmpty
    locator: NonEmpty
    sha256: Sha256
    license_spdx: NonEmpty


class KnowledgeLeakage(StrictModel):
    visibility: Literal["public", "development_only"]
    families: list[LowerToken] = Field(default_factory=list)
    contains_target_case_solution: Literal[False]

    @model_validator(mode="after")
    def unique_families(self) -> "KnowledgeLeakage":
        if len(self.families) != len(set(self.families)):
            raise ValueError("leakage families must be unique")
        return self


class KnowledgeContent(StrictModel):
    summary: NonEmpty
    rules: Annotated[list[NonEmpty], Field(min_length=1)]
    failure_signals: list[NonEmpty] = Field(default_factory=list)
    validation: Annotated[list[NonEmpty], Field(min_length=1)]


class KnowledgeEntry(StrictModel):
    """One focused, provenance-bearing Foundation v10 knowledge topic."""

    schema_version: Literal["1.0.0"]
    id: StableID
    title: NonEmpty
    fork: Literal["foundation"]
    version: Literal["10"]
    knowledge_type: Literal[
        "solver_guide",
        "mesh_pattern",
        "boundary_condition",
        "physics_model",
        "numerics",
        "error_playbook",
        "parallel_execution",
        "validation_pattern",
    ]
    solvers: list[NonEmpty] = Field(default_factory=list)
    models: list[NonEmpty] = Field(default_factory=list)
    tags: Annotated[list[LowerToken], Field(min_length=1)]
    activation_terms: list[NonEmpty] = Field(default_factory=list)
    applicability: KnowledgeApplicability
    source: KnowledgeSource
    leakage: KnowledgeLeakage
    content: KnowledgeContent

    @model_validator(mode="after")
    def governance_invariants(self) -> "KnowledgeEntry":
        for label, values in (
            ("solvers", self.solvers),
            ("models", self.models),
            ("tags", self.tags),
            ("activation_terms", self.activation_terms),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
        pilot_families = set(self.leakage.families) & PILOT_FAMILIES
        if self.source.kind == "pilot_derived":
            if self.leakage.visibility != "development_only":
                raise ValueError(
                    "pilot_derived knowledge must be development_only"
                )
            if not pilot_families:
                raise ValueError(
                    "pilot_derived knowledge requires a pilot leakage family"
                )
        if (
            self.leakage.visibility == "public"
            and pilot_families
        ):
            raise ValueError(
                "a public entry cannot claim a pilot leakage family"
            )
        return self
