"""Leakage-safe prompt for one complete native OpenFOAM case bundle."""

from __future__ import annotations

import json

from foampilot.environment import EnvironmentSnapshot
from foampilot.routing import CapabilityProfile
from foampilot.tasks import TaskSpec


_BUNDLE_SYSTEM = """你是一个 OpenFOAM 工程 Agent。
从空算例目录开始，返回完成公开任务所需的全部原生 OpenFOAM 文件和全部 typed command。
根据物理问题选择已安装 solver、mesh workflow、initialization utility、数值设置与控制参数。
只生成求解该算例必需的文件和命令。不要仅为制造评测证据而添加 function object、sampling、
extrema 或 residualControl。求解成功后，由 evaluator 从 solver log 和写出字段计算观测量。
不要假设能够访问 tutorial、golden result、私有 evaluator、shell 或确定性 case renderer。
对动态公开知识中已路由到当前 solver-family 的 content.rules 视为必须落实的适用契约；
只有与公开任务明确冲突时才可不采用，并以公开任务为准。不要忽略契约后重复使用其中列出的
failure_signals 形态。

返回 schema_version 3 的 ExecutionPlan。在 manifest 中声明 solver、solver family、
physics family、regime、mesh family、dimensionality、每个 region、由 author 或运行阶段
生成的 field、patch 及 active physical model。每个 typed command 必须且只能声明一个 stage：
mesh、check、initialize、decompose、solve、reconstruct 或 postprocess。region path 与
field path 必须匹配同一响应中的文件。普通单区域算例必须只声明一个名为 "default"、
path_prefix 为空的 region，并让每个 field 和 patch 使用 region "default"。多区域算例中，
每个 field 与 patch 的 region 必须精确匹配一个已声明 region name。region name、region
path_prefix，以及限定 region 的 field/patch identity 都必须唯一。

命令以 cwd=/case 执行。分别返回 executable 与 argv；绝不能返回 shell 语法、Allrun script
或外部路径。MPI ranks 与命令 timeout 总和必须处于公开资源预算内。每个生成文件必须使用
安全的算例相对路径，并提供完整 UTF-8 内容。使用 Foundation OpenFOAM v10 语法，让字段
boundary patch 与网格一致。使用 MPI 时，设置 solver executable 和 mpi_ranks；绝不能生成
mpirun 或 orterun。除非公开任务明确要求更严格 flag，否则只使用普通 checkMesh；不要额外
添加 -allGeometry 或 -allTopology 作为 qualification gate。公开请求与验收要求具有最高优先级。"""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def bundle_request_text(
    task: TaskSpec,
    environment: EnvironmentSnapshot,
    capability: CapabilityProfile,
    knowledge_text: str,
    skills_text: str,
) -> tuple[str, str]:
    user = "\n\n".join(
        (
            "公开任务（PUBLIC TASK）\n" + _json(task.agent_payload()),
            "已安装环境（INSTALLED ENVIRONMENT）\n" + _json(environment.agent_payload()),
            (
                "系统路由的能力画像（SYSTEM-ROUTED CAPABILITY PROFILE）\n"
                + _json(capability.model_dump(mode="json"))
            ),
            "动态公开知识（DYNAMIC PUBLIC KNOWLEDGE）\n" + knowledge_text,
            "可移植工作流 Skill（PORTABLE WORKFLOW SKILL）\n" + skills_text,
        )
    )
    for protected in task.protected_paths:
        if protected in user:
            raise ValueError("model prompt contains a protected path")
    return _BUNDLE_SYSTEM, user
