from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from foampilot.authoring import (
    AuthorTargetFacts,
    CaseAuthoringError,
    CaseBundle,
    author_case,
)
from foampilot.context import AgentContext
from foampilot.manifests import (
    CaseField,
    CaseManifest,
    CaseModels,
    CaseRegion,
)
from foampilot.models import (
    InMemoryModelTraceSink,
    ModelBudgetLedger,
    ModelResult,
    ModelStage,
)
from foampilot.plans import GeneratedFile
from tests.test_plan_extensions import _context


def _bundle(*, solver: str = "pisoFoam") -> CaseBundle:
    return CaseBundle(
        manifest=CaseManifest(
            solver_executable=solver,
            solver_family="incompressible-laminar",
            regime="transient",
            physics_family="fluid",
            mesh_family="provided",
            dimensionality="2d",
            regions=[
                CaseRegion(
                    name="default",
                    kind="fluid",
                    path_prefix="",
                )
            ],
            fields=[
                CaseField(
                    name="U",
                    region="default",
                    path="0/U",
                    role="velocity",
                    created_by="author",
                )
            ],
            models=CaseModels(transport="Newtonian"),
        ),
        files=[
            GeneratedFile(
                path="system/controlDict",
                content=(
                    "FoamFile { class dictionary; }\n"
                    f"application {solver};\n"
                ),
            ),
            GeneratedFile(
                path="system/fvSchemes",
                content="FoamFile { class dictionary; }\n",
            ),
            GeneratedFile(
                path="system/fvSolution",
                content="FoamFile { class dictionary; }\n",
            ),
            GeneratedFile(
                path="0/U",
                content="FoamFile { class volVectorField; }\n",
            ),
        ],
    )


def _target(**updates) -> AuthorTargetFacts:
    payload = {
        "distribution": "foundation",
        "version": "10",
        "solver_executable": "pisoFoam",
        "required_outputs": ("velocity", "pressure"),
        "required_authored_paths": (
            "system/controlDict",
            "system/fvSchemes",
            "system/fvSolution",
        ),
        "public_asset_install_paths": ("constant/polyMesh",),
        "protected_paths": ("/home/edwin/private/evaluator",),
    }
    payload.update(updates)
    return AuthorTargetFacts(**payload)


def _agent_context() -> AgentContext:
    return AgentContext(
        knowledge_text="authoritative Foundation 10 case guidance",
        skills_text="write one coherent native case bundle",
        knowledge_slots={},
        missing_slots=(),
        selected_knowledge_ids=("knowledge.foundation10",),
        selected_source_hashes={"knowledge.foundation10": "a" * 64},
        skill_names=("openfoam-author-native-case",),
    )


class RecordingGateway:
    primary_backend_id = "recording"
    primary_model = "recording-author"
    policy_sha256 = "b" * 64

    def __init__(self, response) -> None:
        self.response = response
        self.requests = []

    def generate_structured(self, request, schema, *, budget, trace):
        del trace
        assert budget.stage == ModelStage.CASE_AUTHORING
        assert schema is CaseBundle
        self.requests.append(request)
        value = (
            self.response
            if isinstance(self.response, CaseBundle)
            else schema.model_validate(self.response)
        )
        return ModelResult(
            value=value,
            logical_request_id="author-1",
            backend_id=self.primary_backend_id,
            model=self.primary_model,
            transport_attempts=1,
            backend_switches=0,
            elapsed_seconds=0,
        )


def _window():
    return ModelBudgetLedger.start().open_stage(
        ModelStage.CASE_AUTHORING,
        stage_deadline_seconds=60,
        max_transport_attempts=1,
    )


def _author(response=None, *, target=None):
    gateway = RecordingGateway(response or _bundle())
    design = _context().design
    result = author_case(
        design=design,
        mesh_facts=(),
        target_facts=target or _target(),
        context=_agent_context(),
        gateway=gateway,
        budget=_window(),
        trace=InMemoryModelTraceSink(),
    )
    return result, gateway, design


def test_case_author_binds_frozen_design_and_calls_model_once() -> None:
    bundle, gateway, design = _author()

    assert bundle.manifest.solver_executable == "pisoFoam"
    assert len(gateway.requests) == 1
    request = gateway.requests[0]
    payload = json.loads(request.user_prompt)
    assert payload["frozen_case_design"]["design_sha256"] == design.design_sha256
    assert payload["target_facts"]["version"] == "10"
    assert payload["target_facts"]["distribution"] == "foundation"
    assert "commands" not in request.system_prompt.lower()
    assert "execution steps" in request.system_prompt.lower()


def test_case_author_never_exposes_protected_paths_to_model() -> None:
    _, gateway, _ = _author()

    serialized = gateway.requests[0].model_dump_json()
    assert "/home/edwin/private/evaluator" not in serialized


def test_case_bundle_schema_rejects_model_authored_execution_steps() -> None:
    payload = _bundle().model_dump(mode="json")
    payload["commands"] = [
        {
            "step_id": "solve",
            "executable": "pisoFoam",
            "args": [],
        }
    ]

    with pytest.raises(ValidationError):
        _author(payload)


def test_case_author_rejects_manifest_solver_drift() -> None:
    with pytest.raises(CaseAuthoringError, match="AUTHOR_SOLVER_MISMATCH"):
        _author(_bundle(solver="icoFoam"))


@pytest.mark.parametrize(
    "file",
    [
        GeneratedFile(
            path="constant/polyMesh/points",
            content="model must not replace public mesh\n",
        ),
        GeneratedFile(
            path="constant/physicalProperties",
            content="include /home/edwin/private/evaluator/value;\n",
        ),
    ],
)
def test_case_author_rejects_asset_overwrite_and_protected_leak(file) -> None:
    bundle = _bundle().model_copy(
        update={"files": [*_bundle().files, file]}
    )

    with pytest.raises(
        CaseAuthoringError,
        match="AUTHOR_PUBLIC_ASSET_OVERWRITE|AUTHOR_PROTECTED_PATH_LEAK",
    ):
        _author(bundle)


def test_case_author_requires_complete_related_file_response() -> None:
    incomplete = _bundle().model_copy(
        update={
            "files": [
                item
                for item in _bundle().files
                if item.path != "system/fvSolution"
            ]
        }
    )

    with pytest.raises(
        CaseAuthoringError,
        match="AUTHOR_REQUIRED_FILE_MISSING",
    ):
        _author(incomplete)


def test_case_author_requires_every_manifest_authored_field() -> None:
    incomplete = _bundle().model_copy(
        update={
            "files": [item for item in _bundle().files if item.path != "0/U"]
        }
    )

    with pytest.raises(
        CaseAuthoringError,
        match="AUTHOR_MANIFEST_FILE_MISSING",
    ):
        _author(incomplete)
