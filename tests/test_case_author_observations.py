from __future__ import annotations

import pytest

from foampilot.authoring import CaseAuthoringError
from foampilot.observations import (
    EvidenceStrategy,
    ObservationItem,
    ObservationPlan,
    ObservationScope,
    TimeSelection,
    inject_observation_fragments,
)
from foampilot.plans import GeneratedFile
from foampilot.simulation import FactEvidence
from tests.test_case_author import _bundle


def _history_plan() -> ObservationPlan:
    return ObservationPlan(
        items=(
            ObservationItem(
                observation_id="outlet-flow",
                kind="flow_rate",
                quantity="volumetric_flow_rate",
                dimension="L3/T",
                scope=ObservationScope(kind="patch", names=("outlet",)),
                time_selection=TimeSelection(kind="history"),
                evidence_strategy=EvidenceStrategy(
                    kind="runtime_configuration",
                    collector_id="foundation10.flow_rate",
                ),
                provenance=(FactEvidence(kind="user_quote", detail="outlet flow"),),
            ),
        )
    )


def test_system_fragment_is_injected_after_model_authoring() -> None:
    bundle, fragments = inject_observation_fragments(_bundle(), _history_plan())

    assert "system/foampilot-observations" in {item.path for item in bundle.files}
    control = next(item.content for item in bundle.files if item.path == "system/controlDict")
    assert '#include "foampilot-observations"' in control
    assert fragments.system_owned_paths == ("system/foampilot-observations",)


def test_model_collision_with_system_owned_fragment_is_rejected() -> None:
    bundle = _bundle().model_copy(
        update={
            "files": [
                *_bundle().files,
                GeneratedFile(path="system/foampilot-observations", content="model-owned\n"),
            ]
        }
    )
    with pytest.raises(CaseAuthoringError, match="OBSERVATION_SYSTEM_PATH_COLLISION"):
        inject_observation_fragments(bundle, _history_plan())


def test_model_authored_runtime_functions_are_rejected_before_injection() -> None:
    authored = _bundle().model_copy(
        update={
            "files": [
                item.model_copy(
                    update={
                        "content": item.content
                        + "\nfunctions\n{\n    modelOwned { type probes; }\n}\n"
                    }
                )
                if item.path == "system/controlDict"
                else item
                for item in _bundle().files
            ]
        }
    )

    with pytest.raises(
        CaseAuthoringError,
        match="OBSERVATION_FUNCTIONS_OWNERSHIP_COLLISION",
    ):
        inject_observation_fragments(authored, _history_plan())


def test_model_authored_runtime_functions_are_rejected_with_empty_plan() -> None:
    authored = _bundle().model_copy(
        update={
            "files": [
                item.model_copy(
                    update={
                        "content": item.content
                        + "\nfunctions\n{\n    modelOwned { type probes; }\n}\n"
                    }
                )
                if item.path == "system/controlDict"
                else item
                for item in _bundle().files
            ]
        }
    )

    with pytest.raises(
        CaseAuthoringError,
        match="OBSERVATION_FUNCTIONS_OWNERSHIP_COLLISION",
    ):
        inject_observation_fragments(authored, ObservationPlan(items=()))


def test_model_authored_functions_in_included_file_are_rejected() -> None:
    base = _bundle()
    authored = base.model_copy(
        update={
            "files": [
                *base.files,
                GeneratedFile(
                    path="system/model-functions",
                    content="functions\n{\n    modelOwned { type probes; }\n}\n",
                ),
            ]
        }
    )

    with pytest.raises(
        CaseAuthoringError,
        match="OBSERVATION_FUNCTIONS_OWNERSHIP_COLLISION",
    ):
        inject_observation_fragments(authored, ObservationPlan(items=()))


def test_functions_text_in_comment_is_not_an_ownership_collision() -> None:
    authored = _bundle().model_copy(
        update={
            "files": [
                item.model_copy(
                    update={
                        "content": item.content
                        + "\n// functions { documentation only }\n"
                    }
                )
                if item.path == "system/controlDict"
                else item
                for item in _bundle().files
            ]
        }
    )

    injected, _ = inject_observation_fragments(authored, _history_plan())

    control = next(
        item.content
        for item in injected.files
        if item.path == "system/controlDict"
    )
    assert '#include "foampilot-observations"' in control


def test_final_only_observation_does_not_add_missing_runtime_include() -> None:
    plan = _history_plan().model_copy(
        update={
            "items": (
                _history_plan().items[0].model_copy(
                    update={
                        "time_selection": TimeSelection(kind="final"),
                        "evidence_strategy": EvidenceStrategy(
                            kind="postprocess_command",
                            collector_id="foundation10.flow_rate",
                        ),
                    }
                ),
            )
        }
    )

    bundle, fragments = inject_observation_fragments(_bundle(), plan)

    control = next(
        item.content for item in bundle.files if item.path == "system/controlDict"
    )
    assert '#include "foampilot-observations"' not in control
    assert "system/foampilot-observations" not in fragments.system_owned_paths
