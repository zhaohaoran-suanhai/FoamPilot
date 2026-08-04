"""模型后端注册表的严格 YAML 配置加载。"""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Literal

from pydantic import Field
import yaml

from .base import StrictModel
from .command_backend import CommandBackend, codex_exec_config
from .openai_compatible import (
    OpenAICompatibleBackend,
    OpenAICompatibleConfig,
)
from .registry import BackendRegistry


_SECRET_KEYS = {
    "api_key",
    "token",
    "password",
    "secret",
    "access_token",
    "auth_file",
}


class _BackendFile(StrictModel):
    schema_version: Literal[1] = 1
    backends: list[dict[str, object]] = Field(default_factory=list)


class _CommandSpec(StrictModel):
    id: str = Field(min_length=1)
    kind: Literal["command"]
    profile: Literal["codex_exec"]
    model: str = Field(min_length=1)
    priority: int = 100


class _OpenAISpec(StrictModel):
    id: str = Field(min_length=1)
    kind: Literal["openai_compatible"]
    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key_env: str | None = None
    priority: int = 100


def _reject_secret_keys(value: object, *, path: str = "root") -> None:
    if isinstance(value, dict):
        for raw_key, item in value.items():
            key = str(raw_key)
            if key.lower() in _SECRET_KEYS:
                raise ValueError(
                    f"secret-bearing key is forbidden at {path}.{key}"
                )
            _reject_secret_keys(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_keys(item, path=f"{path}[{index}]")


def load_backend_registry(
    path: Path | None,
    *,
    default_model: str = "gpt-5.6-sol",
) -> BackendRegistry:
    """加载显式配置；无配置时只自动发现公开 Codex CLI。"""

    registry = BackendRegistry()
    if path is None:
        if shutil.which("codex") is not None:
            registry.register(
                CommandBackend(codex_exec_config(model=default_model)),
                priority=10,
            )
        return registry

    source = Path(path)
    if not source.is_file():
        raise ValueError(f"backend config does not exist: {source}")
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    _reject_secret_keys(payload)
    config = _BackendFile.model_validate(payload)
    for raw in config.backends:
        kind = raw.get("kind")
        if kind == "command":
            spec = _CommandSpec.model_validate(raw)
            command_config = codex_exec_config(model=spec.model).model_copy(
                update={"backend_id": spec.id}
            )
            registry.register(
                CommandBackend(command_config),
                priority=spec.priority,
            )
        elif kind == "openai_compatible":
            spec = _OpenAISpec.model_validate(raw)
            registry.register(
                OpenAICompatibleBackend(
                    OpenAICompatibleConfig(
                        backend_id=spec.id,
                        base_url=spec.base_url,
                        model=spec.model,
                        api_key_env=spec.api_key_env,
                        priority=spec.priority,
                    )
                ),
                priority=spec.priority,
            )
        else:
            raise ValueError(f"unsupported backend kind: {kind!r}")
    return registry
