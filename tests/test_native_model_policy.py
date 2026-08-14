from __future__ import annotations

from foampilot.agent.model_policy import (
    AUTHOR_MODEL_POLICY,
    DESIGN_MODEL_POLICY,
    INTENT_MODEL_POLICY,
    NATIVE_MODEL_LINEAGE_ATTEMPT_LIMIT,
    NATIVE_MODEL_TOTAL_DEADLINE_SECONDS,
    REPAIR_MODEL_POLICY,
    ROUTING_MODEL_POLICY,
)
from foampilot.models import ModelBudgetLedger
from foampilot.workflow.confirmation import (
    ConfirmationModelUsage,
    ConfirmationResumeInput,
)
from foampilot.workflow.lineage import ContinuationInput


def test_retrying_stage_deadlines_give_every_attempt_a_full_window() -> None:
    for policy in (
        INTENT_MODEL_POLICY,
        DESIGN_MODEL_POLICY,
        AUTHOR_MODEL_POLICY,
        REPAIR_MODEL_POLICY,
    ):
        assert policy.max_transport_attempts == 2
        assert policy.stage_deadline_seconds >= policy.full_retry_window_seconds


def test_native_model_lineage_budget_covers_cold_path_and_one_repair() -> None:
    policies = (
        ROUTING_MODEL_POLICY,
        INTENT_MODEL_POLICY,
        DESIGN_MODEL_POLICY,
        AUTHOR_MODEL_POLICY,
        REPAIR_MODEL_POLICY,
    )

    assert NATIVE_MODEL_LINEAGE_ATTEMPT_LIMIT >= sum(
        item.max_transport_attempts for item in policies
    )
    assert NATIVE_MODEL_TOTAL_DEADLINE_SECONDS >= sum(
        item.stage_deadline_seconds for item in policies
    )


def test_native_lineage_limit_is_shared_by_all_resume_contracts() -> None:
    bounded_fields = (
        (ConfirmationModelUsage, "transport_attempts_used_before_child"),
        (ConfirmationResumeInput, "transport_attempts_used"),
        (ContinuationInput, "transport_attempts_used"),
    )

    for model, field in bounded_fields:
        assert model.model_json_schema()["properties"][field]["maximum"] == (
            NATIVE_MODEL_LINEAGE_ATTEMPT_LIMIT
        )

    ledger = ModelBudgetLedger.start()
    assert (
        ledger.lineage_transport_attempt_limit
        == NATIVE_MODEL_LINEAGE_ATTEMPT_LIMIT
    )
