"""模型后端注册、选择和并发健康探测。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
import time

from .backend import BackendHealth, ModelBackend


class BackendMode(StrEnum):
    NORMAL = "normal"
    QUALIFICATION = "qualification"


@dataclass(frozen=True, slots=True)
class _Registration:
    priority: int
    backend: ModelBackend


class BackendRegistry:
    """一个小型、确定性且不持有凭据的后端目录。"""

    def __init__(self) -> None:
        self._registrations: list[_Registration] = []

    def register(self, backend: ModelBackend, *, priority: int = 100) -> None:
        if any(
            item.backend.backend_id == backend.backend_id
            and item.backend.model == backend.model
            for item in self._registrations
        ):
            raise ValueError(
                "backend_id and model pair must be unique in registry"
            )
        self._registrations.append(
            _Registration(priority=priority, backend=backend)
        )

    def _ordered(self) -> list[_Registration]:
        return sorted(
            self._registrations,
            key=lambda item: (
                item.priority,
                item.backend.backend_id,
                item.backend.model,
            ),
        )

    def candidates(
        self,
        *,
        mode: BackendMode | str,
        pinned_backend_id: str | None = None,
        pinned_model: str | None = None,
    ) -> list[ModelBackend]:
        selected_mode = BackendMode(mode)
        ordered = self._ordered()
        if selected_mode == BackendMode.NORMAL:
            return [item.backend for item in ordered]
        if pinned_backend_id is None or pinned_model is None:
            raise ValueError(
                "qualification mode requires a pinned backend and model"
            )
        matched = [
            item.backend
            for item in ordered
            if item.backend.backend_id == pinned_backend_id
            and item.backend.model == pinned_model
        ]
        if len(matched) != 1:
            raise ValueError(
                "qualification pinned backend/model must match exactly one backend"
            )
        return matched

    def registrations(self) -> tuple[tuple[int, ModelBackend], ...]:
        return tuple(
            (item.priority, item.backend) for item in self._ordered()
        )


def _probe_one(
    backend: ModelBackend,
    *,
    timeout_seconds: float,
) -> BackendHealth:
    started = time.monotonic()
    try:
        return backend.probe(timeout_seconds=timeout_seconds)
    except Exception:
        return BackendHealth(
            backend_id=backend.backend_id,
            model=backend.model,
            state="unavailable",
            code="BACKEND_UNAVAILABLE",
            message="模型后端不可用。",
            recovery="请运行 foampilot model doctor 检查配置。",
            elapsed_seconds=max(time.monotonic() - started, 0),
        )


def doctor_backends(
    registry: BackendRegistry,
    *,
    timeout_seconds: float = 3.0,
) -> list[BackendHealth]:
    """并发探测全部后端，并保持注册表的确定性顺序。"""

    backends = [item[1] for item in registry.registrations()]
    if not backends:
        return []
    with ThreadPoolExecutor(max_workers=len(backends)) as executor:
        futures = [
            executor.submit(
                _probe_one,
                backend,
                timeout_seconds=timeout_seconds,
            )
            for backend in backends
        ]
        return [future.result() for future in futures]
