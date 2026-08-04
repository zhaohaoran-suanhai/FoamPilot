from foampilot.models import BackendHealth
from foampilot.models.registry import BackendRegistry, doctor_backends


class UnavailableBackend:
    backend_id = "missing"
    model = "test"
    identity_hash = "0" * 64

    def probe(self, *, timeout_seconds: float) -> BackendHealth:
        del timeout_seconds
        return BackendHealth(
            backend_id=self.backend_id,
            model=self.model,
            state="unavailable",
            code="BACKEND_UNAVAILABLE",
            message="模型后端不可用。",
            recovery="请运行 foampilot model doctor 检查配置。",
            elapsed_seconds=0,
        )


def test_doctor_returns_ordered_chinese_health_records() -> None:
    registry = BackendRegistry()
    registry.register(UnavailableBackend(), priority=10)

    records = doctor_backends(registry, timeout_seconds=0.1)

    assert [item.backend_id for item in records] == ["missing"]
    assert records[0].message == "模型后端不可用。"
    assert records[0].recovery.endswith("。")
