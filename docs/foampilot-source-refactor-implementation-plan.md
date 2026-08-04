# FoamPilot 原地来源治理与模型后端重构实施计划

> **执行要求：** 后续必须使用 `superpowers:executing-plans` 在当前对话中内联执行，逐任务完成 TDD、差异检查和验证。不得启用子代理，不得整体复制 `FoamPilot-clean-source`。

**状态：已实施并通过提交前验收（2026-08-04）。** 下列复选框记录实际完成的实施顺序。
许可证与 NOTICE 的最终表述采用用户在实施后明确确认的双版权方案：保留 Foam-Agent 的
MIT 上游声明，同时加入 `Copyright (c) 2026 Haoran Zhao`。该决定覆盖计划中关于许可证
主体尚待决定以及 NOTICE 临时表述的中间假设。

**目标：** 在保持 FoamPilot 现有 CFD 闭环不变的前提下，用公开、可维护的模型后端替换私有 Codex OAuth 实现，并形成可追踪的来源治理证据。

**架构：** `NativeAgent` 继续通过 `ModelGateway` 请求结构化模型输出；Gateway 改为面向 `BackendRegistry`，普通运行允许有限后端降级，qualification 固定 backend/model。来源治理只处理已确认的模型边界、冻结 replay、来源文档与审计，不扩展求解流程。

**技术栈：** Python 3.12、Pydantic v2、PyYAML、`urllib.request`、`subprocess`、pytest、Foundation OpenFOAM v10。

## 全局约束

- 唯一主仓库是 `/home/edwin/workplace/FoamPilot`，实施基线为 `901e338`。
- `/home/edwin/workplace/FoamPilot-clean-source` 只能作为只读参考；主仓库不得依赖该路径。
- 保持 `TaskSpec → 路由/检索 → case 编写 → 检查 → Runner → 评测 → repair → artifact` 不变。
- 不引入计划缓存、renderer、插件市场、Python entry point、常驻 broker 或新 workflow 状态机。
- 不读取第三方认证文件，不接受明文 CLI key，不保存 token。
- CLI 默认中文错误说明；JSON 保留稳定英文错误码。
- 普通运行可以有限降级；qualification 必须固定 backend/model。
- `docs/superpowers/`、运行产物、缓存、凭据和目标 tutorial 不得进入 Git。
- 每个任务均先观察测试失败，再写最小实现，再运行相关回归。
- 不自动提交或推送。每个任务只形成可审查 checkpoint；只有用户明确授权时才创建提交。
- 不自动修改 `LICENSE` 版权主体；来源验证完成后单独向用户提交版权主体决策证据。

---

## 文件结构

### 新增模型文件

| 文件 | 单一职责 |
| --- | --- |
| `src/foampilot/models/backend.py` | `ModelBackend`、`BackendResponse`、`BackendHealth` 契约 |
| `src/foampilot/models/schema.py` | 把 Pydantic schema 收敛为兼容的严格结构化输出 schema |
| `src/foampilot/models/messages_zh.py` | 英文错误码到中文说明、恢复建议的映射 |
| `src/foampilot/models/command_backend.py` | 固定 argv 的外部已认证模型运行器 |
| `src/foampilot/models/openai_compatible.py` | 非流式 OpenAI-compatible HTTP 后端 |
| `src/foampilot/models/registry.py` | 确定性后端注册、选择与并发 probe |
| `src/foampilot/models/config.py` | 无秘密值的 YAML 配置加载 |

### 删除的模型文件

| 文件 | 删除条件 |
| --- | --- |
| `src/foampilot/models/codex_oauth.py` | CLI、qualification、测试不再导入后删除 |
| `src/foampilot/models/provider.py` | Gateway 与测试全部改用 `ModelBackend` 后删除 |

### 来源治理文件

| 文件 | 单一职责 |
| --- | --- |
| `tools/generate_synthetic_replay.py` | 确定性生成六类 FoamPilot 自有 replay fixture |
| `tools/audit_source_provenance.py` | 私有协议 token 与可选上游相似性审计 |
| `PROVENANCE.md` | 原创、合成资产、事实总结和外部运行时边界 |
| `THIRD_PARTY_NOTICES.md` | 实际打包的第三方字节边界 |

---

### Task 1：建立后端中立契约、严格 schema 与中文错误

**Files:**

- Create: `src/foampilot/models/backend.py`
- Create: `src/foampilot/models/schema.py`
- Create: `src/foampilot/models/messages_zh.py`
- Modify: `src/foampilot/models/errors.py`
- Modify: `src/foampilot/models/__init__.py`
- Create: `tests/test_backend_contract.py`
- Create: `tests/test_backend_messages_zh.py`
- Create: `tests/test_model_schema.py`

**Interfaces:**

- Produces: `BackendResponse`, `BackendHealth`, `ModelBackend`。
- Produces: `BackendFailureKind`, `BackendError`。
- Produces: `strict_response_schema(schema: dict[str, Any]) -> dict[str, Any]`。
- Produces: `backend_error_payload_zh(error: BackendError) -> dict[str, object]`。
- Transitional constraint: 原 `ProviderError` 类型只为尚未迁移的旧测试临时保留，到 Task 4 必须删除。

- [x] **Step 1：写后端中立契约的失败测试**

```python
from foampilot.models import BackendHealth, BackendResponse


def test_backend_response_is_backend_neutral() -> None:
    response = BackendResponse(
        backend_id="codex-cli",
        model="gpt-test",
        purpose="generation",
        output_text='{"answer":7}',
        status_code=0,
        output_bytes=12,
    )
    assert response.backend_id == "codex-cli"
    assert "provider" not in response.model_dump()


def test_health_contains_only_chinese_guidance_not_secret_detail() -> None:
    health = BackendHealth(
        backend_id="local-http",
        model="local-model",
        state="misconfigured",
        code="BACKEND_MISCONFIGURED",
        message="模型后端配置错误。",
        recovery="请检查凭据环境变量。",
        elapsed_seconds=0.01,
    )
    assert "secret" not in health.model_dump_json().lower()
```

- [x] **Step 2：运行测试并确认缺少新接口**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_backend_contract.py \
  tests/test_backend_messages_zh.py \
  tests/test_model_schema.py
```

Expected: FAIL，原因是 `BackendResponse`、`BackendFailureKind` 或 `strict_response_schema` 尚不存在。

- [x] **Step 3：实现精确数据契约**

`backend.py` 必须提供：

```python
class BackendResponse(StrictModel):
    backend_id: str
    model: str
    purpose: str
    output_text: str
    status_code: int | None = None
    request_id: str | None = None
    output_bytes: int = Field(ge=0)
    partial_output_bytes: int = Field(default=0, ge=0)


class BackendHealth(StrictModel):
    backend_id: str
    model: str
    state: Literal["available", "unavailable", "misconfigured"]
    code: str | None = None
    message: str
    recovery: str
    elapsed_seconds: float = Field(ge=0)


class ModelBackend(Protocol):
    backend_id: str
    model: str
    identity_hash: str

    def probe(self, *, timeout_seconds: float) -> BackendHealth: ...
    def exchange(
        self,
        request: ModelRequest,
        *,
        timeout_seconds: float,
    ) -> BackendResponse: ...
```

`errors.py` 新接口必须为：

```python
class BackendFailureKind(StrEnum):
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    BACKEND_MISCONFIGURED = "BACKEND_MISCONFIGURED"
    AUTH_FAILED = "AUTH_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    OVERLOADED = "OVERLOADED"
    NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    PROCESS_INTERRUPTED = "PROCESS_INTERRUPTED"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    POLICY_REJECTED = "POLICY_REJECTED"
```

`BackendError` 保存 `kind/backend_id/model/purpose/detail/retryable/status_code/request_id/retry_after_seconds/partial_output_bytes/request_timed_out/allows_schema_correction`，但中文 payload 不得包含 `detail`。

`strict_response_schema()` 必须：

- 深拷贝输入；
- 删除 `default`、`title`、`format`、长度/数值范围等供应商不兼容校验键；
- 把 `const` 转换为单元素 `enum`；
- 对每个 object 设置 `additionalProperties: false`；
- 将每个 object 的全部 property 列入 `required`；
- 不改变 Pydantic 接收响应后的本地校验语义。

- [x] **Step 4：验证新契约与现有模型边界并存**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_backend_contract.py \
  tests/test_backend_messages_zh.py \
  tests/test_model_schema.py \
  tests/test_model_gateway.py
```

Expected: PASS；旧 Gateway 测试仍通过，证明 Task 1 没有提前破坏调用方。

- [x] **Step 5：形成 checkpoint**

Run:

```bash
git diff --check
git status --short
```

Expected: 出现 Task 1 文件，以及已经确认但尚未提交的设计/计划文档；不提交。

---

### Task 2：实现 Command、OpenAI-compatible、Registry 与配置

**Files:**

- Create: `src/foampilot/models/command_backend.py`
- Create: `src/foampilot/models/openai_compatible.py`
- Create: `src/foampilot/models/registry.py`
- Create: `src/foampilot/models/config.py`
- Modify: `src/foampilot/models/__init__.py`
- Create: `tests/test_command_backend.py`
- Create: `tests/test_openai_compatible_backend.py`
- Create: `tests/test_backend_registry.py`
- Create: `tests/test_model_doctor.py`

**Interfaces:**

- Consumes: Task 1 的 `ModelBackend`、`BackendResponse`、`BackendError`。
- Produces: `CommandBackendConfig`、`CommandBackend`、`codex_exec_config()`。
- Produces: `OpenAICompatibleConfig`、`OpenAICompatibleBackend`。
- Produces: `BackendMode`、`BackendRegistry`、`doctor_backends()`。
- Produces: `load_backend_registry(path, *, default_model) -> BackendRegistry`。

- [x] **Step 1：写固定 argv、环境白名单和 HTTP 安全测试**

关键断言：

```python
assert recorded["argv"][0] == str(fake_executable)
assert recorded["secret_visible"] is False
assert "Return a JSON object." in recorded["stdin"]
assert "auth.json" not in " ".join(codex_exec_config(model="gpt-test").argv_template)

with pytest.raises(ValueError, match="unknown command placeholder"):
    CommandBackendConfig(
        backend_id="unsafe",
        model="test",
        argv_template=("runner", "{auth_file}"),
        probe_argv=(("runner", "--version"),),
    )

with pytest.raises(ValueError, match="HTTPS"):
    OpenAICompatibleConfig(
        backend_id="remote-http",
        base_url="http://models.example.com/v1",
        model="remote-model",
    )
```

- [x] **Step 2：运行测试确认后端尚未实现**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_command_backend.py \
  tests/test_openai_compatible_backend.py \
  tests/test_backend_registry.py \
  tests/test_model_doctor.py
```

Expected: FAIL，原因是新模块尚不存在。

- [x] **Step 3：实现 `CommandBackend`**

允许的 template placeholder 固定为：

```python
_ALLOWED_PLACEHOLDERS = {
    "model", "schema_file", "output_file", "work_dir"
}
```

执行必须使用：

```python
subprocess.run(
    argv,
    shell=False,
    text=True,
    input=prompt,
    capture_output=True,
    cwd=work_dir,
    env={name: os.environ[name] for name in pass_env if name in os.environ},
    timeout=timeout_seconds,
    check=False,
)
```

`codex_exec_config()` 只使用 Codex CLI 公开非交互接口：`codex exec --ephemeral --skip-git-repo-check --ignore-rules --sandbox read-only --output-schema ... --output-last-message ... -`；probe 为 `codex --version` 和 `codex login status`。不得引用 `.codex/auth.json` 或 token。

stderr 清洗必须覆盖 Bearer、`sk-`、`api_key`、`access_token`、`auth_token` 和 password，并保留错误头部与末尾根因。

- [x] **Step 4：实现 `OpenAICompatibleBackend`**

要求：

- `base_url` 必须是绝对 HTTP(S) URL；非 loopback 禁止明文 HTTP；URL 禁止用户名、密码、query 与 fragment；
- 凭据只从 `api_key_env` 指定的环境变量读取；
- 使用 `/chat/completions` 非流式请求；
- 结构化输出 schema 通过 request body 传递；
- 映射 401、403、429、5xx、timeout、DNS/连接错误；
- response 与 error 不得包含 API key。

- [x] **Step 5：实现确定性 Registry 与无秘密配置**

```python
class BackendMode(StrEnum):
    NORMAL = "normal"
    QUALIFICATION = "qualification"


class BackendRegistry:
    def register(self, backend: ModelBackend, *, priority: int = 100) -> None: ...
    def candidates(
        self,
        *,
        mode: BackendMode | str,
        pinned_backend_id: str | None = None,
        pinned_model: str | None = None,
    ) -> list[ModelBackend]: ...
```

Normal 按 `(priority, backend_id, model)` 排序；qualification 必须精确匹配一个 pinned backend/model。配置文件顶层只接受 `schema_version: 1` 和 `backends`，遇到 `api_key`、`token`、`secret`、`password` 等承载秘密的键直接失败。

- [x] **Step 6：验证后端实现**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_command_backend.py \
  tests/test_openai_compatible_backend.py \
  tests/test_backend_registry.py \
  tests/test_model_doctor.py
```

Expected: PASS，且本地 HTTP 测试只绑定 `127.0.0.1`。

- [x] **Step 7：形成 checkpoint**

Run `git diff --check` 和 `git status --short`；不提交。

---

### Task 3：让 ModelGateway 支持固定 qualification 和普通模式降级

**Files:**

- Modify: `src/foampilot/models/gateway.py`
- Modify: `src/foampilot/models/circuit_breaker.py`
- Modify: `src/foampilot/models/traces.py`
- Modify: `src/foampilot/models/__init__.py`
- Modify: `tests/support/model_gateway.py`
- Modify: `tests/test_model_gateway.py`
- Create: `tests/test_model_gateway_failover.py`
- Modify: `tests/test_circuit_breaker.py`
- Modify: `tests/test_qualification_gateway.py`

**Interfaces:**

- Consumes: `BackendRegistry`、`BackendMode`、`ModelBackend`。
- Produces: `ModelGateway(registry=..., mode=..., pinned_backend_id=..., pinned_model=...)`。
- Produces: `ModelResult.backend_id/model/backend_switches`。
- Produces: `GatewayRequestError.failure: BackendError`。

- [x] **Step 1：把测试 support 改为 `ScriptedBackend` 并写降级失败测试**

```python
registry = BackendRegistry()
registry.register(first_backend, priority=10)
registry.register(second_backend, priority=20)
gateway = ModelGateway(registry=registry, mode=BackendMode.NORMAL)

result = gateway.generate_structured(
    REQUEST,
    ExampleOutput,
    budget=window,
    trace=trace,
)

assert result.backend_id == "second"
assert result.backend_switches == 1
assert trace.attempts[-1].switch_reason == "OVERLOADED"
```

增加 qualification 测试，证明首个固定后端失败后不会调用第二后端。

- [x] **Step 2：运行 Gateway 测试确认旧单 provider 构造不满足新契约**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_model_gateway.py \
  tests/test_model_gateway_failover.py \
  tests/test_circuit_breaker.py \
  tests/test_qualification_gateway.py
```

Expected: FAIL，原因是 Gateway 还不接受 Registry 或没有 failover trace。

- [x] **Step 3：实现 Gateway 候选后端循环**

构造函数必须立即冻结候选列表和 policy hash：

```python
self._candidates = tuple(
    registry.candidates(
        mode=self.mode,
        pinned_backend_id=pinned_backend_id,
        pinned_model=pinned_model,
    )
)
```

每个逻辑请求执行：

1. 使用 `strict_response_schema()` 生成请求 schema；
2. 按 Registry 顺序尝试 backend；
3. 每个 backend 内最多使用预算允许的 3 次 transport；
4. retry delay 只允许 `(5, 15)` 秒或合法 `Retry-After`；
5. auth/config/policy 错误不在同一 backend 重试，但 normal 可切换下一 backend；
6. schema response 校验失败只在同一 backend 纠正一次；
7. backoff 和 probe 都不能越过 stage/total deadline；
8. qualification 遇到任何最终后端失败立即抛出，不切换；
9. trace 不保存 prompt、响应正文、header 或环境值。

- [x] **Step 4：升级 circuit key 与 trace schema**

```python
@dataclass(frozen=True)
class CircuitBreakerKey:
    backend_id: str
    model: str
    identity_hash: str


class ModelAttemptTrace(StrictModel):
    schema_version: Literal[2] = 2
    purpose: str
    backend_id: str
    model: str
    request_hash: str
    logical_request_id: str
    transport_attempt: int
    backend_ordinal: int
    backend_attempt: int
    switch_reason: str | None = None
```

保留现有时间、字节数、status、request ID、错误码、retryable、partial bytes 与 deadline reason 字段。

- [x] **Step 5：验证 Gateway 全部预算与降级语义**

Run Task 3 的四个测试文件。Expected: PASS，测试必须覆盖 overload、rate limit、auth、timeout、schema correction、circuit open、normal failover 和 pinned qualification。

- [x] **Step 6：形成 checkpoint**

Run `git diff --check` 和 `git status --short`；不提交。

---

### Task 4：迁移 CLI、Agent、qualification，并删除私有 OAuth/Provider

**Files:**

- Modify: `src/foampilot/cli/main.py`
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Modify: `src/foampilot/qualification/runner.py`
- Modify: `src/foampilot/qualification/models.py`
- Modify: `src/foampilot/qualification/reporting.py`
- Modify: `src/foampilot/workflow/models.py`
- Modify: `src/foampilot/workflow/lineage.py`
- Modify: `src/foampilot/models/errors.py`
- Modify: `src/foampilot/models/__init__.py`
- Delete: `src/foampilot/models/codex_oauth.py`
- Delete: `src/foampilot/models/provider.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_native_agent_cli.py`
- Modify: `tests/test_native_agent_state_machine.py`
- Modify: `tests/test_qualification_reporting.py`
- Modify: `tests/test_qualification_cli.py`
- Create: `tests/test_model_doctor_cli.py`
- Delete: `tests/test_provider_boundary.py`
- Delete: `tests/test_model_boundary.py`

**Interfaces:**

- Consumes: Task 3 的 Registry-based `ModelGateway`。
- Produces CLI options: `--backend`、`--backend-config`、`--model-name`。
- Produces CLI command: `foampilot model doctor [--backend-config PATH] --json`。
- Produces qualification API: `run_qualification_suite(..., backend_id: str, model_name: str, gateway: ModelGateway)`。

- [x] **Step 1：先改 CLI 测试，禁止 `--auth` 与认证文件路径**

```python
help_text = build_parser().format_help()
assert "--auth" not in help_text
assert "auth.json" not in help_text

with pytest.raises(SystemExit):
    build_parser().parse_args(["solve", "task.yaml", "--auth", "secret.json"])
```

增加 qualification 测试：`--backend auto` 或缺失 `--model-name` 必须在模型请求前失败；显式 `--backend fake --model-name fake-model` 才能运行。

- [x] **Step 2：运行调用方测试确认旧 CLI 行为失败**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_native_agent_cli.py \
  tests/test_native_agent_state_machine.py \
  tests/test_qualification_gateway.py \
  tests/test_qualification_reporting.py \
  tests/test_qualification_cli.py \
  tests/test_model_doctor_cli.py
```

Expected: FAIL，原因是 CLI 仍暴露 `--auth` 或 qualification 仍自行读取 token。

- [x] **Step 3：实现不含 plan cache 的 CLI 切换**

公共参数只增加：

```python
def _add_backend_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", default="auto")
    parser.add_argument("--backend-config", type=Path)
    parser.add_argument("--model-name")
```

`_native_gateway(arguments, qualification=False)`：

- 用 `load_backend_registry()` 构建 Registry；
- normal 的 `auto` 使用全部候选；
- normal 的显式 backend 只选择匹配项；
- qualification 禁止 `auto`，并要求显式 model；
- 不增加 `--plan-cache`、`--no-plan-cache` 或缓存读取。

`model doctor` 并发 probe，JSON 输出 `schema_version/status/backends`；至少一个 available 返回 0，否则返回 3。

- [x] **Step 4：迁移 qualification 和 workflow 命名**

- qualification runner 不再接受 `auth: Path`，也不再自行构造模型客户端；
- qualification 验证注入 Gateway 处于 pinned mode 且 backend/model 一致；
- 报告记录 `backend_id` 与 `model_name`；
- 把机器状态 `DEFERRED_PROVIDER` 改为 `DEFERRED_BACKEND`；
- 把统计字段 `provider_deferred` 改为 `backend_deferred`；
- 把 `FailureDomain.PROVIDER` 改为 `FailureDomain.BACKEND`，并让 backend blocker
  同时保存稳定错误码、脱敏 detail、中文 `message` 与中文 `recovery`；
- workflow compatibility fingerprint 使用 backend policy hash，不保存认证身份原文；
- `primary_failure` 与 `terminal_blocker` 的分离保持不变。

- [x] **Step 5：删除私有实现与过渡类型**

删除 `codex_oauth.py`、`provider.py`、`ProviderError`、`ProviderFailureKind`、`ProviderResponse`、`ProviderClient` 及其导出。`pyproject.toml` 删除 `codex = ["requests>=2.31"]`，因为新 HTTP 后端使用标准库。

- [x] **Step 6：运行全量确定性测试**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests
```

Expected: 全部通过；不得存在因为旧 API 被静默 alias 而通过的测试。

- [x] **Step 7：扫描私有协议残留**

Run:

```bash
rg -n --hidden \
  -g '!docs/superpowers/**' -g '!docs/reports/**' \
  -g '!docs/*design.md' -g '!.git/**' \
  'CodexOAuthProviderClient|load_codex_access_token|chatgpt\.com/backend-api/codex|\.codex/auth\.json|--auth' \
  README.md AGENTS.md docs src tests pyproject.toml
```

Expected: 仅允许来源设计文档中的历史说明；功能代码、主介绍文档和测试不得命中。

- [x] **Step 8：形成 checkpoint**

Run `git diff --check` 和 `git status --short`；不提交。

---

### Task 5：用确定性合成 replay 替换来源不清晰的冻结资产

**Files:**

- Create: `tools/generate_synthetic_replay.py`
- Replace: `tests/fixtures/artifact-replay/**`
- Modify: `tests/test_artifact_replay.py`
- Modify: `tests/test_source_provenance.py`
- Delete: `src/foampilot/plans/legacy.py`
- Modify: `src/foampilot/plans/__init__.py`

**Interfaces:**

- Produces: 六类 fixture：单区域、MPI、include、大字段/热场、多区域、已知失败。
- Produces: `index.yaml` schema v2，包含 `source_kind`、生成器 SHA256 和逐文件 hash。
- Removes: v2 plan overlay 兼容路径。

- [x] **Step 1：先改 replay 测试要求完全合成来源**

```python
payload = yaml.safe_load(INDEX.read_text(encoding="utf-8"))
generator_hash = sha256(GENERATOR.read_bytes()).hexdigest()

assert payload["schema_version"] == 2
assert {item["kind"] for item in payload["fixtures"]} == EXPECTED_KINDS
assert all(item["source_kind"] == "synthetic_foampilot" for item in payload["fixtures"])
assert all(item["generator_sha256"] == generator_hash for item in payload["fixtures"])
```

新增测试：所有 `execution-plan.json` 直接满足 schema v3，不允许调用 legacy overlay loader。

- [x] **Step 2：运行 replay 测试确认旧 fixture 不符合新来源契约**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests/test_artifact_replay.py
```

Expected: FAIL，原因是 index 仍为 schema v1 或 fixture 仍依赖 overlay。

- [x] **Step 3：实现合成生成器**

生成器必须从参数化 `SyntheticFixtureSpec` 构造：

```python
@dataclass(frozen=True)
class SyntheticFixtureSpec:
    fixture_id: str
    kind: str
    dimensions: tuple[float, float, float]
    patches: tuple[str, str, str, str]
    expected_status: str
    mpi_ranks: int = 1
    include_field: bool = False
    thermal: bool = False
    multi_region: bool = False
    bad_pressure_dimension: bool = False
```

所有几何尺寸、patch 名称和 case 文本由 FoamPilot 自行生成；不得读取 tutorial。生成器必须拒绝疑似 Bearer、`sk-`、API key、token 或 password 字节。

生成内容包括 TaskSpec、ExecutionPlan v3、case、静态检查、合成 typed run result、公开验证和 summary；index 记录每个文件字节数与 SHA256。

- [x] **Step 4：在临时目录验证确定性，再替换仓库 fixture**

Run generator twice into两个不同 `/tmp` 目录，比较 tree hash。Expected: 完全一致。确认后用生成结果替换 `tests/fixtures/artifact-replay/`，删除 v2 overlay 与 `plans/legacy.py`。

- [x] **Step 5：运行 replay 与 plan 回归**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_artifact_replay.py \
  tests/test_execution_plan.py \
  tests/test_plan_normalizer.py \
  tests/test_plan_runner.py
```

Expected: PASS；六类 fixture 均可被当前 reader/inspector 读取。

- [x] **Step 6：形成 checkpoint**

Run `git diff --check` 和 `git status --short`；不提交。

---

### Task 6：建立来源说明、自动审计和最终验证

**Files:**

- Create: `tools/audit_source_provenance.py`
- Create: `tests/test_source_provenance.py`
- Create: `PROVENANCE.md`
- Create: `THIRD_PARTY_NOTICES.md`
- Modify: `NOTICE.md`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/agent-integration.md`
- Modify: `docs/architecture.md`
- Modify: `docs/independent-agent-quickstart.md`
- Modify: `docs/qualification.md`
- Modify only if audit finds mismatches: `src/foampilot/knowledge/openfoam10/**/*.yaml`
- Modify if Knowledge changes: `src/foampilot/knowledge/knowledge-manifest.json`
- Do not modify without user-supplied ownership decision: `LICENSE`

**Interfaces:**

- Produces: `audit_repository(root, compare_root=None) -> ProvenanceAuditReport`。
- Produces CLI: `python tools/audit_source_provenance.py --root ROOT [--compare-root UPSTREAM]`。
- Produces engineering evidence; does not claim legal clean-room status。

- [x] **Step 1：写来源审计失败测试**

必须覆盖：

```python
report = audit_repository(PROJECT)
assert report.forbidden_matches == ()

report = audit_repository(candidate, compare_root=upstream)
assert report.long_line_matches
assert report.shingle_matches
assert report.passed is False
```

还要检查：每条 Knowledge 具有相对 locator、64 位 source SHA256 和 SPDX；每个 replay fixture 都来自当前 generator hash。

- [x] **Step 2：运行来源测试确认审计工具尚不存在**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests/test_source_provenance.py
```

Expected: FAIL，原因是审计工具或来源文档尚不存在。

- [x] **Step 3：实现不泄露被比较正文的审计器**

审计报告只允许包含：路径、行号、计数、相似度、SHA256 tree digest 和短 fingerprint。不得序列化候选或上游原文。

规则：

- 核心源码禁止私有 OAuth 类名、认证文件路径、token loader 和私有 endpoint；
- 忽略 `.git`、cache、venv、build、dist、`docs/superpowers/`；
- MIT 标准许可证模板、JSON Schema 公共 token 和 OpenFOAM 标准 header 从文本相似性判定中排除；
- 长规范化行完全匹配形成 finding；
- 12-token shingle containment 达到 5% 形成 finding；
- 显式传入不存在的 compare root 时失败关闭。

- [x] **Step 4：重写当前介绍文档中的认证说明**

主介绍文档统一为：

- 默认调用已登录的外部 `codex exec`，不读取认证文件；
- 可通过无秘密 YAML 配置 OpenAI-compatible 或本地模型；
- 普通 solve 可以降级，qualification 必须 pinned；
- `foampilot model doctor --json` 是模型预检入口；
- 不宣称 FoamPilot 与 Foam-Agent 存在运行依赖或官方延续关系。

`NOTICE.md` 不再声明上游版权文本保留在 `LICENSE`；改为指向 `PROVENANCE.md` 与 `THIRD_PARTY_NOTICES.md`，并明确工程审计不等于法律意见。

- [x] **Step 5：只修复来源测试指出的 Knowledge 元数据**

对每个 mismatch：核对本机 Foundation OpenFOAM v10 对应文件或 source set，重新计算 SHA256；只修正 source locator/hash 或确有来源问题的解释，不批量改写已经独立表述的知识正文。更新 `knowledge-manifest.json`。

- [x] **Step 6：运行全量确定性验证**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests
```

Expected: 全部通过。

- [x] **Step 7：构建和检查 wheel**

从 `/tmp` 运行：

```bash
/home/edwin/feal-venv-py312/bin/python -B -m pip wheel \
  /home/edwin/workplace/FoamPilot \
  --no-deps --no-build-isolation --wheel-dir /tmp/foampilot-source-refactor-wheel
```

检查 wheel 不包含 `docs/superpowers`、fixture 之外的 `.foampilot`、运行目录、缓存、凭据、Foam-Agent 或 `FoamPilot-clean-source` 路径。从临时 target 加载 wheel，运行 `foampilot --help`、`foampilot model doctor --json` 和 `foampilot preflight --json`。

- [x] **Step 8：执行真实模型与 OpenFOAM 最小 gate**

1. `foampilot model doctor --json` 验证 `codex-cli`；
2. 从空 case、非 tutorial TaskSpec 调用完整 `NativeAgent.solve()`；
3. 验证 `blockMesh → checkMesh → 目标 solver` 通过 Runner；
4. 验证 public validation 与 artifact manifest；
5. 不要求本阶段提高复杂算例物理精度。

Expected: 模型、case 生成、OpenFOAM 与产物闭环均有独立证据；任一失败按 backend/environment/authoring/solver/validation 分层报告。

- [x] **Step 9：运行可选上游相似性审计**

若本机存在只读 Foam-Agent 上游 checkout：

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B \
  tools/audit_source_provenance.py \
  --root /home/edwin/workplace/FoamPilot \
  --compare-root /tmp/foampilot-upstream-foam-agent-audit
```

Expected: `passed: true`。若 compare root 不存在，先报告缺少对比证据，不自动联网或伪造结果。

- [x] **Step 10：形成最终审查 checkpoint**

Run:

```bash
git diff --check
git status --short
git diff --stat 901e338
```

列出所有改动、删除、测试、wheel hash、真实 gate 和来源审计结果。不提交、不推送。

- [x] **Step 11：单独处理许可证决策**

向用户报告：现有 `LICENSE` 版权行为 `Copyright (c) 2025 Ling Yue`，以及重构后的来源审计证据。只有用户或法律审查明确给出新的合法版权主体和年份后，才修改 `LICENSE`。在该决策完成前，不得宣称已经法律意义上移除原版权归属。

---

## 最终验收矩阵

| 目标 | 证据 |
| --- | --- |
| 不读取 OAuth 文件 | CLI 测试、私有 token 扫描、来源审计 |
| Codex 使用体验可用 | `codex exec` probe、真实结构化请求、中文 doctor |
| 普通模式可恢复 | Gateway failover 与 deadline 测试 |
| qualification 可比较 | pinned backend/model 测试与报告字段 |
| CFD 闭环不变 | 一个真实非 tutorial `NativeAgent.solve()` gate |
| replay 来源清晰 | 合成生成器、schema v2 index、逐文件 hash |
| Knowledge 来源可追踪 | locator/hash/SPDX 测试 |
| wheel 独立 | 临时 target 加载、路径扫描、CLI/preflight |
| 无大范围架构膨胀 | diff 中不存在 cache、renderer、plugin 或新状态机 |
| 许可证表述诚实 | 版权主体单独决策，不由实现者猜测 |
