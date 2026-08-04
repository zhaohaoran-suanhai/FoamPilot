from foampilot.models import BackendError, BackendFailureKind
from foampilot.models.messages_zh import (
    BACKEND_MESSAGES_ZH,
    backend_error_payload_zh,
)


def test_backend_error_keeps_machine_code_and_chinese_message() -> None:
    error = BackendError(
        kind=BackendFailureKind.BACKEND_UNAVAILABLE,
        backend_id="codex-cli",
        model="gpt-test",
        purpose="generation",
        detail="executable not found",
        retryable=False,
    )

    payload = backend_error_payload_zh(error)

    assert payload["code"] == "BACKEND_UNAVAILABLE"
    assert payload["message"] == "模型后端不可用。"
    assert "foampilot model doctor" in payload["recovery"]
    assert "executable not found" not in payload["message"]
    assert payload["backend_id"] == "codex-cli"
    assert payload["retryable"] is False


def test_every_backend_failure_has_chinese_guidance() -> None:
    assert set(BACKEND_MESSAGES_ZH) == set(BackendFailureKind)
    for message, recovery in BACKEND_MESSAGES_ZH.values():
        assert message.endswith("。")
        assert recovery.endswith("。")
