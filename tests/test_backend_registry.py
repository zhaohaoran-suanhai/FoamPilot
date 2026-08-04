from __future__ import annotations

from pathlib import Path

import pytest

from foampilot.models import BackendHealth
from foampilot.models.config import load_backend_registry
from foampilot.models.registry import BackendMode, BackendRegistry


class FakeBackend:
    def __init__(self, backend_id: str, model: str = "test") -> None:
        self.backend_id = backend_id
        self.model = model
        self.identity_hash = backend_id * 4

    def probe(self, *, timeout_seconds: float) -> BackendHealth:
        del timeout_seconds
        return BackendHealth(
            backend_id=self.backend_id,
            model=self.model,
            state="available",
            message="模型后端可用。",
            recovery="无需处理。",
            elapsed_seconds=0,
        )


def test_normal_mode_orders_backends_by_priority() -> None:
    registry = BackendRegistry()
    registry.register(FakeBackend("slow"), priority=20)
    registry.register(FakeBackend("fast"), priority=10)

    assert [
        item.backend_id
        for item in registry.candidates(mode=BackendMode.NORMAL)
    ] == ["fast", "slow"]


def test_qualification_requires_one_pinned_backend() -> None:
    registry = BackendRegistry()
    registry.register(FakeBackend("only"), priority=10)

    with pytest.raises(ValueError, match="qualification.*pinned"):
        registry.candidates(mode=BackendMode.QUALIFICATION)

    assert registry.candidates(
        mode=BackendMode.QUALIFICATION,
        pinned_backend_id="only",
        pinned_model="test",
    )[0].backend_id == "only"


def test_backend_config_rejects_embedded_secret(tmp_path: Path) -> None:
    config = tmp_path / "backends.yaml"
    config.write_text(
        """schema_version: 1
backends:
  - id: unsafe
    kind: openai_compatible
    base_url: https://models.example.com/v1
    model: test
    api_key: literal-secret
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="secret-bearing key"):
        load_backend_registry(config)
