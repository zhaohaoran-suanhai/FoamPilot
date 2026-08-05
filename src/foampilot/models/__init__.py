"""独立、可替换的模型后端边界。"""

from .base import (
    ModelRequest,
)
from .backend import BackendHealth, BackendResponse, ModelBackend
from .command_backend import (
    CommandBackend,
    CommandBackendConfig,
    codex_exec_config,
)
from .config import load_backend_registry
from .budgets import (
    LineageBudgetExhausted,
    ModelBudgetLedger,
    ModelBudgetWindow,
    ModelStage,
)
from .circuit_breaker import (
    CircuitBreakerKey,
    CircuitDeferredError,
    CircuitState,
    SharedCircuitBreaker,
)
from .errors import (
    BackendError,
    BackendFailureKind,
)
from .gateway import GatewayRequestError, ModelGateway, ModelResult
from .messages_zh import backend_error_payload_zh
from .openai_compatible import (
    OpenAICompatibleBackend,
    OpenAICompatibleConfig,
)
from .registry import BackendMode, BackendRegistry, doctor_backends
from .traces import (
    InMemoryModelTraceSink,
    JsonlModelTraceSink,
    ModelAttemptTrace,
    ModelTraceSink,
)
__all__ = [
    "BackendHealth",
    "BackendError",
    "BackendFailureKind",
    "BackendMode",
    "BackendRegistry",
    "BackendResponse",
    "CommandBackend",
    "CommandBackendConfig",
    "CircuitBreakerKey",
    "CircuitDeferredError",
    "CircuitState",
    "GatewayRequestError",
    "InMemoryModelTraceSink",
    "JsonlModelTraceSink",
    "LineageBudgetExhausted",
    "ModelAttemptTrace",
    "ModelBudgetLedger",
    "ModelBudgetWindow",
    "ModelGateway",
    "ModelBackend",
    "ModelRequest",
    "ModelResult",
    "ModelStage",
    "ModelTraceSink",
    "OpenAICompatibleBackend",
    "OpenAICompatibleConfig",
    "SharedCircuitBreaker",
    "codex_exec_config",
    "backend_error_payload_zh",
    "doctor_backends",
    "load_backend_registry",
]
