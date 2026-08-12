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
