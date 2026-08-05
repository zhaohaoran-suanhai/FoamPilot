"""Stable Chinese TaskBuilder messages."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TaskBuilderMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str
    message: str
    recovery: str


_MESSAGES = {
    "TASK_EXTRACTION_FAILED": (
        "模型未返回合法的任务草稿。",
        "请在模型服务恢复后重试，或更换已配置的模型后端。",
    ),
    "TASK_REQUEST_INCOMPLETE": (
        "任务缺少定义物理问题所需的信息。",
        "请根据 blocking 问题补充边界、物性、工况或终止条件。",
    ),
    "TASK_UNIT_AMBIGUOUS": (
        "几何或物理量的单位缺失或相互冲突。",
        "请确认并明确提供对应量的单位。",
    ),
    "TASK_PHYSICS_AMBIGUOUS": (
        "任务存在会改变问题定义的多种物理解读。",
        "请确认稳态/瞬态、相态、可压缩性或能量模型。",
    ),
    "TASK_ASSET_UNRESOLVED": (
        "公开附件、哈希或几何映射无法确定。",
        "请修正附件路径、文件内容或 patch/region 映射。",
    ),
    "TASK_COMPILATION_FAILED": (
        "任务草稿尚不能确定性编译为 TaskSpec。",
        "请先解决所有 blocking 和 confirmable 问题并确认草稿。",
    ),
    "TASK_CAPABILITY_UNAVAILABLE": (
        "本机缺少任务明确要求的 OpenFOAM 或网格能力。",
        "请安装所需程序，或明确修改任务所要求的求解/网格路线。",
    ),
}


def taskbuilder_message_zh(code: str) -> TaskBuilderMessage:
    try:
        message, recovery = _MESSAGES[code]
    except KeyError as error:
        raise ValueError(f"unknown TaskBuilder message code: {code}") from error
    return TaskBuilderMessage(code=code, message=message, recovery=recovery)
