from foampilot.models import BackendHealth, BackendResponse


def test_backend_response_is_backend_neutral() -> None:
    response = BackendResponse(
        backend_id="codex-cli",
        model="gpt-test",
        purpose="generation",
        output_text='{"answer": 7}',
        status_code=0,
        output_bytes=13,
    )

    assert response.backend_id == "codex-cli"
    assert response.status_code == 0
    assert "provider" not in response.model_dump()


def test_backend_health_never_contains_a_secret_value() -> None:
    health = BackendHealth(
        backend_id="local-http",
        model="local-model",
        state="misconfigured",
        code="BACKEND_MISCONFIGURED",
        message="模型后端配置错误。",
        recovery="请检查凭据环境变量是否存在。",
        elapsed_seconds=0.01,
    )

    assert "token" not in health.model_dump_json().lower()
