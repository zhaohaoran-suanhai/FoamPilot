"""稳定模型错误码对应的默认中文说明。"""

from __future__ import annotations

from .errors import BackendError, BackendFailureKind


BACKEND_MESSAGES_ZH: dict[
    BackendFailureKind,
    tuple[str, str],
] = {
    BackendFailureKind.BACKEND_UNAVAILABLE: (
        "模型后端不可用。",
        "请运行 foampilot model doctor 检查外部运行器或模型服务配置。",
    ),
    BackendFailureKind.BACKEND_MISCONFIGURED: (
        "模型后端配置错误。",
        "请检查 backend 配置中的命令、地址、模型名称、环境变量和可写状态目录。",
    ),
    BackendFailureKind.AUTH_FAILED: (
        "模型后端认证失败。",
        "请在外部运行器中完成登录，或检查配置指定的凭据环境变量。",
    ),
    BackendFailureKind.RATE_LIMITED: (
        "模型服务请求频率受限。",
        "请等待限流窗口结束后恢复，普通求解也可以尝试其他健康后端。",
    ),
    BackendFailureKind.OVERLOADED: (
        "模型服务当前过载。",
        "请稍后恢复，普通求解将在预算允许时尝试其他健康后端。",
    ),
    BackendFailureKind.NETWORK_UNAVAILABLE: (
        "无法连接模型服务。",
        "请检查网络、代理或本地模型服务状态后恢复运行。",
    ),
    BackendFailureKind.TIMEOUT: (
        "模型请求超时。",
        "请检查模型服务响应时间，或调整受控请求期限后恢复。",
    ),
    BackendFailureKind.PROCESS_INTERRUPTED: (
        "外部模型进程异常中断。",
        "请检查外部运行器状态和脱敏诊断信息后恢复。",
    ),
    BackendFailureKind.SCHEMA_INVALID: (
        "模型输出格式不符合要求。",
        "请检查结构化输出能力；系统会在预算内执行一次格式纠正。",
    ),
    BackendFailureKind.POLICY_REJECTED: (
        "模型后端配置违反安全规则。",
        "请删除 shell 拼接、秘密值或未经允许的命令和环境配置。",
    ),
}

if set(BACKEND_MESSAGES_ZH) != set(BackendFailureKind):
    raise RuntimeError("中文模型错误信息目录不完整")


def backend_error_payload_zh(error: BackendError) -> dict[str, object]:
    """生成机器码稳定、中文说明默认的错误 payload。"""

    message, recovery = BACKEND_MESSAGES_ZH[error.kind]
    return {
        "code": error.kind.value,
        "message": message,
        "recovery": recovery,
        "backend_id": error.backend_id,
        "model": error.model,
        "retryable": error.retryable,
    }
