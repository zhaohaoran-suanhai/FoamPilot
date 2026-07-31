# FoamPilot 阶段 A：Provider 与状态韧性实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. 本计划按用户要求在当前对话内联执行，
> 不使用子代理。

**Goal:** 在不改变 case authoring、TaskSpec、ExecutionPlan、知识检索、
OpenFOAM case 和 Runner 语义的前提下，使 FoamPilot 的模型调用可分类、限时、
熔断、审计和恢复，并正确区分原始 CFD 失败与当前 provider blocker。

**Architecture:** 将现有 `CodexOAuthModelClient + generate_with_retry` 单层边界
替换为 `ProviderClient → ModelGateway`：provider 只完成一次 HTTP/SSE 交换，
gateway 统一负责 schema validation、retry、单调时钟 deadline、trace 和共享
circuit breaker。`WorkflowStore` 追加阶段事件和 checkpoint，`RunSummary v2`
表达 workflow/native 两个维度；严格恢复只创建 immutable child run。

**Tech Stack:** Python 3.12、Pydantic v2、requests、JSONL、pytest、
Foundation OpenFOAM v10、现有 bubblewrap Runner。

## Global Constraints

- 适用基线固定为 FoamPilot `8d30409`；开始实施前记录实际工作树状态。
- 阶段 A 不修改 generation prompt、TaskSpec schema、ExecutionPlan schema、
  知识检索、Skill 选择、OpenFOAM case 内容或 Runner 执行语义。
- 保留 typed command、bubblewrap、独立 evaluator、immutable attempt、
  tutorial/golden 防泄漏边界。
- provider client 一次调用只对应一次 HTTP/SSE 交换；不重试、不熔断、
  不决定阶段 deadline、不执行最终 Pydantic schema 校验。
- 默认 request timeout 为 300 秒，generation deadline 为 360 秒，repair
  deadline 为 240 秒，total model deadline 为 600 秒，单逻辑请求最多
  3 次 transport attempt。
- overload 和 network retry backoff 固定为 5、15 秒；stream interrupted
  最多额外重试 1 次；auth、permission 和 schema invalid 不重试。
- circuit breaker 键为 provider、model 和 account identity hash；连续两个
  完整逻辑请求以 overload 或 network unavailable 结束后打开 120 秒。
- continuation 每阶段最多 2 个 child，stage budget 在 child 中重置，
  lineage transport attempt 上限为 7。
- parent run、parent manifest 和 parent case 永不修改；恢复只创建 child。
- access token、原始 account ID、完整 prompt 和 provider 原始响应不得写入
  trace、summary 或 qualification report。
- 正式 qualification 不自动切换 provider 或 model。
- 只保留 RunSummary v1 的只读 adapter；不保留旧 retry、generation、
  repair 或 execution 主路径。
- 不新增 MCP、多智能体、LangGraph、队列服务、renderer 或 provider
  fallback。
- 所有代码改动采用 TDD；每个任务完成后先停在 review checkpoint。
- 用户未要求提交：实施过程中不得执行 `git commit` 或 `git push`。
- `docs/superpowers/` 不纳入本计划的改动或未来提交范围。

---

## 1. 文件与职责冻结

### 新增文件

| 文件 | 单一职责 |
| --- | --- |
| `src/foampilot/models/errors.py` | provider/gateway 错误种类和安全错误载荷 |
| `src/foampilot/models/provider.py` | 单次交换的 provider 请求/响应协议 |
| `src/foampilot/models/budgets.py` | 单调时钟 stage/total/lineage 预算 |
| `src/foampilot/models/traces.py` | model attempt trace 与 JSONL sink |
| `src/foampilot/models/circuit_breaker.py` | 线程安全、进程内共享熔断器 |
| `src/foampilot/models/gateway.py` | retry、deadline、breaker、schema validation |
| `src/foampilot/workflow/models.py` | workflow stage/state/failure/resume 类型 |
| `src/foampilot/workflow/events.py` | workflow event 类型与构造函数 |
| `src/foampilot/workflow/store.py` | append-only event、checkpoint、summary 写入 |
| `src/foampilot/workflow/lineage.py` | compatibility fingerprint 与 continuation 校验 |
| `src/foampilot/workflow/__init__.py` | workflow 稳定公开接口 |
| `tests/support/model_gateway.py` | fake clock、scripted provider、trace sink |
| `tests/test_model_gateway.py` | gateway retry/deadline/schema 测试 |
| `tests/test_circuit_breaker.py` | breaker 并发与 half-open 测试 |
| `tests/test_workflow_store.py` | event/checkpoint/summary 测试 |
| `tests/test_continuation.py` | parent/child、fingerprint、预算测试 |
| `tests/test_qualification_gateway.py` | qualification 共享 gateway 与统计测试 |
| `tests/test_artifact_replay.py` | 冻结公开产物完整性与只读 replay gate |
| `tests/fixtures/artifact-replay/index.yaml` | 六类 replay fixture 的来源、hash 和预期 |
| `tools/freeze_artifact_replay.py` | 从已验证 run 提取可重分发最小 fixture |

### 修改文件

| 文件 | 修改职责 |
| --- | --- |
| `src/foampilot/models/base.py` | 保留 provider-neutral `ModelRequest` |
| `src/foampilot/models/codex_oauth.py` | 降级为单次 Codex OAuth provider 交换 |
| `src/foampilot/models/__init__.py` | 导出新边界，删除旧 retry 导出 |
| `src/foampilot/agent/generation.py` | 从 gateway 发起 generation 逻辑请求 |
| `src/foampilot/agent/repair.py` | 从 gateway 发起 repair 逻辑请求 |
| `src/foampilot/agent/native_orchestrator.py` | workflow event、v2 summary、continuation |
| `src/foampilot/artifacts/models.py` | RunSummary v2 与 NativeAgentOutcome |
| `src/foampilot/artifacts/store.py` | v1 只读适配、v2 读取、manifest hash |
| `src/foampilot/artifacts/__init__.py` | 导出 v2 类型 |
| `src/foampilot/qualification/models.py` | provider deferred 与分层运行指标 |
| `src/foampilot/qualification/runner.py` | suite 级共享 gateway |
| `src/foampilot/qualification/reporting.py` | 单 run/lineage 累计统计 |
| `src/foampilot/cli/main.py` | gateway factory、`resume` 命令、v2 exit code |
| `tests/test_model_boundary.py` | 单次 provider 交换 characterization |
| `tests/test_native_case_generation.py` | generation gateway contract |
| `tests/test_native_repair.py` | repair gateway contract |
| `tests/test_native_agent_state_machine.py` | primary failure/blocker 状态机 |
| `tests/test_native_agent_cli.py` | v2 report 与 resume CLI |
| `tests/test_qualification_cli.py` | resume/qualification 参数契约 |
| `tests/test_qualification_reporting.py` | nullable native status 和 lineage 指标 |
| `tests/test_real_native_vertical_slice.py` | canonical gateway 真实最小 gate |
| `tests/test_lean_package_boundary.py` | `resume` CLI 命令集合 |
| `docs/architecture.md` | 已落地的 Stage A 组件边界 |
| `docs/independent-agent-quickstart.md` | solve、deferred、resume 使用方式 |
| `docs/qualification.md` | breaker、lineage 和新指标解释 |

### 删除文件

`src/foampilot/models/retry.py` 在 Task 5 canonical migration 完成后删除。
删除前，所有调用点必须已经迁移到 `ModelGateway`，且
`rg -n "generate_with_retry|ModelRetryPolicy|TransportError|ModelClient"
src tests` 只允许命中专门验证 v1 adapter 的 fixture 文本。

---

## 2. 稳定公开接口

后续任务必须使用以下名称，不得在实现时另造同义接口：

```python
# src/foampilot/models/provider.py
class ProviderClient(Protocol):
    provider: str
    model: str
    account_identity_hash: str

    def exchange(
        self,
        request: ModelRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        raise NotImplementedError


# src/foampilot/models/gateway.py
class ModelGateway:
    def generate_structured(
        self,
        request: ModelRequest,
        schema: type[T],
        *,
        budget: ModelBudgetWindow,
        trace: ModelTraceSink,
    ) -> ModelResult[T]:
        raise NotImplementedError


# src/foampilot/workflow/store.py
class WorkflowStore:
    def record(self, event: WorkflowEvent) -> None:
        raise NotImplementedError

    def checkpoint(
        self,
        name: str,
        payload: BaseModel | dict[str, object],
    ) -> Path:
        raise NotImplementedError

    def finish(self, summary: RunSummary) -> Path:
        raise NotImplementedError


# src/foampilot/workflow/lineage.py
def build_resume_fingerprint(
    *,
    task: TaskSpec,
    public_asset_root: Path | None,
    model: str,
    provider: str,
    provider_policy: BaseModel,
    environment: EnvironmentSnapshot,
    knowledge_ids: list[str],
    knowledge_hash: str,
    skill_ids: list[str],
    skill_hash: str,
) -> ResumeCompatibility:
    raise NotImplementedError

def prepare_continuation(
    *,
    parent_run: Path,
    artifact_store: ArtifactStore,
    current: ResumeCompatibility,
) -> ContinuationInput:
    raise NotImplementedError
```

`ModelBudgetWindow` 是由一个 run/lineage 独占的 `ModelBudgetLedger`
打开的阶段窗口。共享 gateway 不得共享 run 的 total deadline；
qualification 的每道题拥有独立 ledger，但共享同一个 breaker。

---

### Task 1: A0 characterization 与冻结 replay 入口

**Files:**

- Modify: `tests/test_model_boundary.py`
- Modify: `tests/test_native_agent_state_machine.py`
- Modify: `tests/test_qualification_reporting.py`
- Create: `tools/freeze_artifact_replay.py`
- Create: `tests/test_artifact_replay.py`
- Create: `tests/fixtures/artifact-replay/index.yaml`

**Interfaces:**

- Consumes: 当前 `CodexOAuthModelClient`、`generate_with_retry()`、
  `RunSummary v1`、`ArtifactStore.finalize()/verify()`。
- Produces: 不改变行为的 characterization baseline；固定 replay fixture
  索引格式 `schema_version/kind/source_manifest_sha256/files/expected`。

- [ ] **Step 1: 固定当前模型与状态机行为**

在现有测试中增加以下测试名，并保留当前断言作为重构保护：

```python
def test_current_retry_retries_transport_but_not_schema() -> None:
    transport_client = SequenceClient(
        [TransportError("overload"), ExampleOutput(value="ok")]
    )
    assert generate_with_retry(
        transport_client,
        REQUEST,
        ExampleOutput,
        ModelRetryPolicy(max_attempts=2, delays_seconds=(0,)),
    ).value == "ok"

    schema_client = SequenceClient([SchemaOutputError("invalid")])
    with pytest.raises(SchemaOutputError):
        generate_with_retry(
            schema_client,
            REQUEST,
            ExampleOutput,
            ModelRetryPolicy(max_attempts=2, delays_seconds=(0,)),
        )
    assert schema_client.calls == 1
```

同时增加：

- `test_repair_transport_failure_currently_overwrites_solver_status`
- `test_v1_summary_round_trips_through_artifact_store`
- `test_two_qualification_workers_currently_construct_separate_clients`
- `test_artifact_finalize_is_exclusive_and_verify_detects_parent_mutation`

- [ ] **Step 2: 运行 characterization 并记录基线**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_model_boundary.py \
  tests/test_native_agent_state_machine.py \
  tests/test_qualification_reporting.py -q
```

Expected: PASS；新增“当前错误覆盖行为”测试明确证明旧缺陷存在，而不是把
旧缺陷写成期望的新行为。

- [ ] **Step 3: 实现公开最小 fixture 提取器**

`tools/freeze_artifact_replay.py` 接收：

```text
--source-run PATH
--fixture-kind single_region_success|mpi_success|include_success|
               buoyant_success|multi_region_success|known_failure
--output-root tests/fixtures/artifact-replay
```

实现必须：

1. 调用 `ArtifactStore(source_run.parent).verify(source_run)`；
2. 拒绝 manifest 不通过的来源；
3. 拒绝任何源路径包含 `tutorials`；
4. 复制 `summary.json`、`execution-plan.json`、最后 attempt 的
   `static-inspection.json`、`public-validation.json`、`run-result.json`；
5. 只复制 plan 声明的 case 文件和 `.foampilot/logs` 中每个日志最后
   200 行；
6. 对每个复制文件写 bytes 和 SHA256；
7. 写入 source manifest SHA256，但不写入源机器绝对路径；
8. 若文件包含 bearer token、`sk-` key 或 named secret，立即失败。

索引条目使用固定结构：

```yaml
schema_version: 1
fixtures:
  - fixture_id: single-region-success
    kind: single_region_success
    source_manifest_sha256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
    expected:
      artifact_valid: true
      native_status: PUBLIC_VALIDATION_PASS
    files:
      - path: execution-plan.json
        bytes: 0
        sha256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

实际 `bytes` 和 `sha256` 由工具生成，禁止手写伪造。

- [ ] **Step 4: 增加 fixture 结构测试**

```python
def test_replay_index_has_six_distinct_public_fixture_kinds() -> None:
    payload = yaml.safe_load(INDEX.read_text(encoding="utf-8"))
    kinds = {item["kind"] for item in payload["fixtures"]}
    assert kinds == {
        "single_region_success",
        "mpi_success",
        "include_success",
        "buoyant_success",
        "multi_region_success",
        "known_failure",
    }


def test_replay_fixture_hashes_match_index() -> None:
    for fixture, item in indexed_files():
        path = FIXTURE_ROOT / fixture["fixture_id"] / item["path"]
        assert path.stat().st_size == item["bytes"]
        assert sha256(path.read_bytes()).hexdigest() == item["sha256"]
```

- [ ] **Step 5: 从现有公开 run 生成 fixture 并审查体积**

使用 `docs/reports/2026-07-30-extended-10-learning.md` 和
`docs/reports/2026-07-30-controlled-learning-15.md` 中列出的 run 作为候选，
逐个先执行 `foampilot report RUN --json` 和 manifest verify。每一类只选
一个最小、公开、可重分发的 run；找不到合格来源的类别不得用 tutorial
补齐，而是在 Stage A gate 中重新生成该类公开 run。

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_artifact_replay.py -q
du -sh tests/fixtures/artifact-replay
```

Expected: 6 类 fixture 全部 PASS；总 fixture 体积不超过 5 MiB。若超过，
仅缩短日志或删除未被 replay 使用的求解时刻，不删除 plan 声明文件。

- [ ] **Review checkpoint**

审查 `git diff --stat` 和 fixture index；确认无 tutorial、golden、private
evaluator、绝对源路径或 secret。不得提交。

---

### Task 2: A1 单次 Provider 交换与细分错误

**Files:**

- Create: `src/foampilot/models/errors.py`
- Create: `src/foampilot/models/provider.py`
- Modify: `src/foampilot/models/base.py`
- Modify: `src/foampilot/models/codex_oauth.py`
- Modify: `src/foampilot/models/__init__.py`
- Modify: `tests/test_model_boundary.py`

**Interfaces:**

- Consumes: `ModelRequest`。
- Produces: `ProviderFailureKind`、`ProviderError`、`ProviderResponse`、
  `ProviderClient`、`CodexOAuthProviderClient.exchange()`。

- [ ] **Step 1: 写 Provider contract 红测**

```python
def test_provider_exchange_returns_text_without_schema_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeSseResponse(
        status_code=200,
        headers={"x-request-id": "req-1"},
        events=['data: {"type":"response.output_text.done","text":"not-json"}'],
    )
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: response)
    client = CodexOAuthProviderClient(
        model="gpt-test",
        access_token="secret",
    )

    result = client.exchange(
        ModelRequest(
            purpose="generation",
            system_prompt="system",
            user_prompt="user",
            response_schema={"type": "object"},
        ),
        timeout_seconds=7,
    )

    assert result.output_text == "not-json"
    assert result.provider_request_id == "req-1"
    assert response.closed
```

再覆盖状态映射：

| 输入 | 预期 kind | retryable |
| --- | --- | ---: |
| HTTP 429 | `PROVIDER_RATE_LIMITED` | true |
| HTTP 401 | `PROVIDER_AUTH_FAILED` | false |
| HTTP 403 | `PROVIDER_PERMISSION_DENIED` | false |
| HTTP 5xx 且 body/code 表示 overloaded | `PROVIDER_OVERLOADED` | true |
| connection/proxy/DNS error | `PROVIDER_NETWORK_UNAVAILABLE` | true |
| SSE 在完整结果前结束 | `PROVIDER_STREAM_INTERRUPTED` | true |
| 未知 provider error | `PROVIDER_UNKNOWN` | false |

- [ ] **Step 2: 运行红测**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_model_boundary.py -q
```

Expected: FAIL，原因是 `CodexOAuthProviderClient`、`ProviderResponse` 和
细分错误尚不存在。

- [ ] **Step 3: 实现稳定错误与响应类型**

`src/foampilot/models/errors.py` 的公开形状固定为：

```python
from enum import StrEnum


class ProviderFailureKind(StrEnum):
    OVERLOADED = "PROVIDER_OVERLOADED"
    RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    PERMISSION_DENIED = "PROVIDER_PERMISSION_DENIED"
    NETWORK_UNAVAILABLE = "PROVIDER_NETWORK_UNAVAILABLE"
    STREAM_INTERRUPTED = "PROVIDER_STREAM_INTERRUPTED"
    SCHEMA_INVALID = "PROVIDER_SCHEMA_INVALID"
    UNKNOWN = "PROVIDER_UNKNOWN"


class ProviderError(RuntimeError):
    def __init__(
        self,
        *,
        kind: ProviderFailureKind,
        provider: str,
        model: str,
        purpose: str,
        detail: str,
        retryable: bool,
        http_status: int | None = None,
        provider_code: str | None = None,
        provider_request_id: str | None = None,
        retry_after_seconds: float | None = None,
        partial_output_bytes: int = 0,
    ) -> None:
        super().__init__(detail)
        self.kind = kind
        self.provider = provider
        self.model = model
        self.purpose = purpose
        self.detail = detail
        self.retryable = retryable
        self.http_status = http_status
        self.provider_code = provider_code
        self.provider_request_id = provider_request_id
        self.retry_after_seconds = retry_after_seconds
        self.partial_output_bytes = partial_output_bytes
```

`ProviderResponse` 必须包含：

```python
class ProviderResponse(StrictModel):
    provider: str
    model: str
    purpose: str
    output_text: str
    http_status: int
    provider_request_id: str | None = None
    provider_code: str | None = None
    output_bytes: int = Field(ge=0)
    partial_output_bytes: int = Field(default=0, ge=0)
```

`ModelRequest` 增加 `response_schema: dict[str, object]`，由 gateway 注入；
provider 仅把它序列化到请求，不用它验证输出。

- [ ] **Step 4: 把 Codex OAuth client 降为单次 exchange**

重命名为 `CodexOAuthProviderClient`，并确保：

- `requests.post(url, headers=headers, json=payload,
  timeout=timeout_seconds, stream=True)`；
- response 在成功、HTTP error、JSON error、timeout 和 SSE error 的
  `finally` 中关闭；
- `Retry-After` 同时支持秒数和 HTTP-date；
- detail 只包含异常类、HTTP 状态、provider code 和脱敏消息；
- access token 与 account ID 不出现在异常字符串；
- `account_identity_hash` 使用
  `sha256(f"codex-oauth\\0{account_id or 'suite-default'}")`；
- 本任务暂时保留旧 `CodexOAuthModelClient` 名称为测试迁移 alias，
  Task 5 删除该 alias。

- [ ] **Step 5: 运行 Provider contract 测试**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_model_boundary.py -q
```

Expected: PASS；每个 fake response 的 `closed` 为 true；每次测试只发生
一次 `requests.post`。

- [ ] **Review checkpoint**

执行：

```bash
rg -n "access_token|Authorization|ChatGPT-Account-Id" \
  src/foampilot/models tests/test_model_boundary.py
```

人工确认 token 只进入 HTTP header，错误和 trace 类型不接受 secret 字段。
不得提交。

---

### Task 3: A2 ModelGateway 的预算、retry、trace 与 schema validation

**Files:**

- Create: `src/foampilot/models/budgets.py`
- Create: `src/foampilot/models/traces.py`
- Create: `src/foampilot/models/gateway.py`
- Create: `tests/support/__init__.py`
- Create: `tests/support/model_gateway.py`
- Create: `tests/test_model_gateway.py`
- Modify: `src/foampilot/models/__init__.py`

**Interfaces:**

- Consumes: `ProviderClient.exchange()`、`ProviderError`。
- Produces: `ModelStage`、`ModelBudgetLedger`、`ModelBudgetWindow`、
  `ModelAttemptTrace`、`ModelTraceSink`、`JsonlModelTraceSink`、
  `ModelResult[T]`、`ModelGateway.generate_structured()`。

- [ ] **Step 1: 实现测试用 fake clock/provider**

`tests/support/model_gateway.py` 提供：

```python
class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class ScriptedProvider:
    provider = "fake"
    model = "fake-model"
    account_identity_hash = "a" * 64

    def __init__(self, events: list[ProviderResponse | ProviderError]) -> None:
        self.events = list(events)
        self.timeouts: list[float] = []

    def exchange(
        self,
        request: ModelRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        self.timeouts.append(timeout_seconds)
        event = self.events.pop(0)
        if isinstance(event, ProviderError):
            raise event
        return event
```

- [ ] **Step 2: 写 deadline/retry/schema 红测**

至少包含：

```python
def test_gateway_uses_minimum_remaining_deadline() -> None:
    clock = FakeClock()
    ledger = ModelBudgetLedger.start(
        total_model_deadline_seconds=600,
        lineage_transport_attempt_limit=7,
        now=clock.monotonic,
    )
    window = ledger.open_stage(
        ModelStage.GENERATION,
        request_timeout_seconds=300,
        stage_deadline_seconds=9,
        max_transport_attempts=3,
        now=clock.monotonic,
    )
    provider = ScriptedProvider([valid_response('{"value":"ok"}')])

    result = ModelGateway(
        provider=provider,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    ).generate_structured(
        REQUEST,
        ExampleOutput,
        budget=window,
        trace=InMemoryModelTraceSink(),
    )

    assert result.value.value == "ok"
    assert provider.timeouts == [pytest.approx(9)]


def test_schema_invalid_is_not_retried() -> None:
    provider = ScriptedProvider(
        [
            valid_response("not-json"),
            valid_response('{"value":"must-not-be-used"}'),
        ]
    )
    with pytest.raises(GatewayRequestError) as captured:
        gateway(provider).generate_structured(
            REQUEST,
            ExampleOutput,
            budget=generation_window(),
            trace=InMemoryModelTraceSink(),
        )
    assert captured.value.failure.kind == ProviderFailureKind.SCHEMA_INVALID
    assert len(provider.timeouts) == 1
```

另加：

- overload 使用 5、15 秒并最多 3 次；
- rate limit 优先 `Retry-After`；
- `Retry-After` 超过剩余 deadline 时不再请求；
- network 最多 3 次；
- interrupted 最多 2 次；
- auth/permission 各 1 次；
- total deadline 比 stage deadline 更早时以 total 为准；
- backoff 计入两个 deadline；
- lineage attempt 7 次上限不能绕过；
- trace 只记录 request hash、bytes 和安全元数据。

- [ ] **Step 3: 运行 gateway 红测**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_model_gateway.py -q
```

Expected: FAIL，原因是 budget、trace 和 gateway 类型尚不存在。

- [ ] **Step 4: 实现单调预算类型**

公开类型固定为：

```python
class ModelStage(StrEnum):
    GENERATION = "generation"
    REPAIR = "repair"
    ROUTING = "routing"


@dataclass(frozen=True)
class ModelBudgetWindow:
    stage: ModelStage
    request_timeout_seconds: float
    stage_deadline_monotonic: float
    total_deadline_monotonic: float
    max_transport_attempts: int
    ledger: ModelBudgetLedger


class ModelBudgetLedger:
    @classmethod
    def start(
        cls,
        *,
        total_model_deadline_seconds: float = 600,
        lineage_transport_attempt_limit: int = 7,
        transport_attempts_used: int = 0,
        now: Callable[[], float] = time.monotonic,
    ) -> "ModelBudgetLedger":
        raise NotImplementedError

    def open_stage(
        self,
        stage: ModelStage,
        *,
        request_timeout_seconds: float = 300,
        stage_deadline_seconds: float,
        max_transport_attempts: int = 3,
        now: Callable[[], float] = time.monotonic,
    ) -> ModelBudgetWindow:
        raise NotImplementedError

    def reserve_transport_attempt(self) -> int:
        raise NotImplementedError


class LineageBudgetExhausted(RuntimeError):
    """No transport attempt may be sent for this lineage."""
```

实现时用 `threading.Lock` 保护累计 attempt；当 lineage 上限耗尽时
`reserve_transport_attempt()` 抛出 `LineageBudgetExhausted`，不得发送请求。

- [ ] **Step 5: 实现 trace**

`ModelAttemptTrace` 固定字段：

```python
class ModelAttemptTrace(StrictModel):
    purpose: str
    provider: str
    model: str
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    logical_request_id: str
    transport_attempt: int = Field(ge=1)
    started_at: datetime
    finished_at: datetime
    elapsed_seconds: float = Field(ge=0)
    prompt_bytes: int = Field(ge=0)
    output_bytes: int = Field(ge=0)
    http_status: int | None = None
    provider_request_id: str | None = None
    provider_error_code: str | None = None
    retryable: bool | None = None
    partial_output_bytes: int = Field(default=0, ge=0)
    deadline_reason: Literal[
        "REQUEST_TIMEOUT",
        "STAGE_DEADLINE",
        "TOTAL_MODEL_DEADLINE",
    ] | None = None
```

`JsonlModelTraceSink.record()` 必须 append、flush、`os.fsync()`；一行一个
完整 JSON，不保存 prompt 或 output text。

- [ ] **Step 6: 实现 ModelGateway**

`ModelGateway.generate_structured()` 的固定执行顺序：

1. 生成 `logical_request_id`；
2. 将 `schema.model_json_schema()` 注入 request；
3. 对 request 的 canonical JSON 计算 SHA256；
4. 在每次传输前计算 stage/total remaining；
5. 剩余时间小于等于 0 时停止；
6. 调用 ledger 预留 transport attempt；
7. 用三个 timeout 最小值调用 provider；
8. 写一条 attempt trace；
9. 成功后由 gateway 调用 `schema.model_validate_json()`；
10. schema invalid 形成非 retryable `ProviderError`；
11. 按错误表选择 backoff；
12. backoff 会越过任一 deadline 时停止；
13. 返回 `ModelResult(value, logical_request_id, transport_attempts,
    elapsed_seconds)` 或抛 `GatewayRequestError`。

结果和异常类型固定为：

```python
class ModelResult(BaseModel, Generic[T]):
    value: T
    logical_request_id: str
    transport_attempts: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)


class GatewayRequestError(RuntimeError):
    def __init__(
        self,
        *,
        failure: ProviderError,
        logical_request_id: str,
        transport_attempts: int,
        deadline_reason: str | None,
        deferred_by_circuit: bool = False,
    ) -> None:
        super().__init__(failure.detail)
        self.failure = failure
        self.logical_request_id = logical_request_id
        self.transport_attempts = transport_attempts
        self.deadline_reason = deadline_reason
        self.deferred_by_circuit = deferred_by_circuit
```

`GatewayRequestError` 必须保留 `failure`、`logical_request_id`、
`transport_attempts` 和 `deadline_reason`，`str(error)` 只输出脱敏 detail。
breaker 在 transport 前拒绝请求时，gateway 将 `CircuitDeferredError`
转换成 `GatewayRequestError(deferred_by_circuit=True,
transport_attempts=0)`；gateway 外部不直接依赖 breaker 内部异常。

`ModelTraceSink` 接口固定为：

```python
class ModelTraceSink(Protocol):
    def record(self, attempt: ModelAttemptTrace) -> None:
        raise NotImplementedError


class InMemoryModelTraceSink:
    def __init__(self) -> None:
        self.attempts: list[ModelAttemptTrace] = []

    def record(self, attempt: ModelAttemptTrace) -> None:
        self.attempts.append(attempt)
```

- [ ] **Step 7: 运行 gateway 测试**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_model_gateway.py -q
```

Expected: PASS；fake clock 测试不真实等待；每个测试断言准确 transport 次数。

- [ ] **Review checkpoint**

确认所有 deadline 比较使用注入的 monotonic clock；`datetime.now()` 只生成
可读 trace 时间。不得提交。

---

### Task 4: A2/A3 线程安全 Circuit Breaker

**Files:**

- Create: `src/foampilot/models/circuit_breaker.py`
- Create: `tests/test_circuit_breaker.py`
- Modify: `src/foampilot/models/gateway.py`
- Modify: `src/foampilot/models/__init__.py`

**Interfaces:**

- Consumes: 一个完整逻辑请求的最终 `ProviderError`。
- Produces: `CircuitBreakerKey`、`CircuitState`、
  `SharedCircuitBreaker.before_request()/record_success()/record_failure()`；
  gateway 的 `CircuitDeferredError`。

- [ ] **Step 1: 写 breaker 状态机红测**

```python
def test_breaker_opens_after_two_failed_logical_requests() -> None:
    clock = FakeClock()
    breaker = SharedCircuitBreaker(
        failure_threshold=2,
        cooldown_seconds=120,
        monotonic=clock.monotonic,
    )
    key = CircuitBreakerKey("fake", "model", "a" * 64)

    breaker.before_request(key)
    breaker.record_failure(key, ProviderFailureKind.OVERLOADED)
    breaker.before_request(key)
    breaker.record_failure(key, ProviderFailureKind.NETWORK_UNAVAILABLE)

    with pytest.raises(CircuitDeferredError):
        breaker.before_request(key)
```

另加：

- auth、permission、rate-limit、stream 和 schema 不累计 breaker；
- cooldown 前不放行；
- cooldown 后仅一个线程获得 half-open probe；
- half-open 成功关闭并清零；
- half-open 失败重新打开 120 秒；
- 16 线程并发更新不丢失计数。

- [ ] **Step 2: 运行 breaker 红测**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_circuit_breaker.py -q
```

Expected: FAIL，原因是 breaker 类型尚不存在。

- [ ] **Step 3: 实现锁保护状态机**

```python
class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class CircuitBreakerKey:
    provider: str
    model: str
    account_identity_hash: str


class CircuitDeferredError(RuntimeError):
    def __init__(
        self,
        *,
        key: CircuitBreakerKey,
        retry_after_seconds: float,
        last_failure_kind: ProviderFailureKind,
    ) -> None:
        super().__init__("provider circuit is open")
        self.key = key
        self.retry_after_seconds = retry_after_seconds
        self.last_failure_kind = last_failure_kind


class SharedCircuitBreaker:
    def before_request(self, key: CircuitBreakerKey) -> None:
        """Reject open circuits; grant at most one half-open probe."""

    def record_success(self, key: CircuitBreakerKey) -> None:
        """Close the circuit and clear the consecutive logical failures."""

    def record_failure(
        self,
        key: CircuitBreakerKey,
        kind: ProviderFailureKind,
    ) -> None:
        """Count only overload/network final logical failures."""
```

内部 map 的每次读改写必须在同一个 `threading.Lock` 临界区完成。不得把
breaker 状态写入磁盘或跨进程共享。

- [ ] **Step 4: 集成 gateway**

`ModelGateway.generate_structured()` 在任何 transport attempt 前调用一次
`before_request()`；只有整个逻辑请求成功后调用 `record_success()`，只有
整个逻辑请求最终以 overload/network 结束后调用 `record_failure()`。
单次 transport retry 不得被计为完整逻辑请求失败。

- [ ] **Step 5: 运行 breaker/gateway 联合测试**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_circuit_breaker.py tests/test_model_gateway.py -q
```

Expected: PASS；breaker 打开后的调用 transport attempt 为 0。

- [ ] **Review checkpoint**

人工确认 breaker key 不包含 token 或原始 account ID，且 qualification
之外的不同 gateway 默认不共享全局状态。不得提交。

---

### Task 5: 迁移 generation/repair 到唯一 Gateway 主路径

**Files:**

- Modify: `src/foampilot/agent/generation.py`
- Modify: `src/foampilot/agent/repair.py`
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Modify: `src/foampilot/cli/main.py`
- Modify: `src/foampilot/models/__init__.py`
- Delete: `src/foampilot/models/retry.py`
- Modify: `tests/test_native_case_generation.py`
- Modify: `tests/test_native_repair.py`
- Modify: `tests/test_native_agent_state_machine.py`

**Interfaces:**

- Consumes: `ModelGateway.generate_structured()`、run 独占
  `ModelBudgetLedger`、run 独占 `ModelTraceSink`。
- Produces:
  `author_case_bundle(task, environment, gateway, knowledge_text, skills_text,
  budget=budget, trace=trace)` 和
  `request_repair(task=task, plan=plan, report=report, failed_log=log,
  current_files=files, knowledge_text=knowledge, skills_text=skills,
  gateway=gateway, budget=budget, trace=trace)`；无旧 retry 分支。

- [ ] **Step 1: 把 generation/repair 单元测试改成 fake gateway**

测试 fake 固定记录：

```python
class RecordingGateway:
    def __init__(self, outputs: list[BaseModel | GatewayRequestError]) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple[str, ModelStage]] = []

    def generate_structured(
        self,
        request: ModelRequest,
        schema: type[T],
        *,
        budget: ModelBudgetWindow,
        trace: ModelTraceSink,
    ) -> ModelResult[T]:
        self.calls.append((request.purpose, budget.stage))
        output = self.outputs.pop(0)
        if isinstance(output, GatewayRequestError):
            raise output
        return ModelResult(
            value=schema.model_validate(output),
            logical_request_id=f"logical-{len(self.calls)}",
            transport_attempts=1,
            elapsed_seconds=0.1,
        )
```

断言 generation 使用 `ModelStage.GENERATION`，repair 使用
`ModelStage.REPAIR`，且一次业务请求只调用一次 gateway。

- [ ] **Step 2: 运行迁移红测**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_native_case_generation.py \
  tests/test_native_repair.py \
  tests/test_native_agent_state_machine.py -q
```

Expected: FAIL，旧函数仍接收 `ModelClient`。

- [ ] **Step 3: 修改 generation/repair 签名**

固定调用形状：

```python
result = gateway.generate_structured(
    request,
    ExecutionPlan,
    budget=budget,
    trace=trace,
)
return result.value
```

repair 同样返回 `result.value`。prompt、schema、TaskSpec、knowledge/Skill
文本不得改变；本任务 diff 中这些文本必须字节一致。

- [ ] **Step 4: 给 NativeAgent 和 CLI 建立 gateway/ledger**

`NativeAgent.__init__()` 接收：

```python
gateway: ModelGateway
```

`solve()` 每个 run 创建：

```python
ledger = ModelBudgetLedger.start(
    total_model_deadline_seconds=600,
    lineage_transport_attempt_limit=7,
)
trace = JsonlModelTraceSink(run_dir / "model-attempts.jsonl")
```

generation window 为 360 秒；repair window 为 240 秒。`plan` CLI 创建
generation window，但不创建 ArtifactStore run；trace 写到
`<output>.model-attempts.jsonl`。

NativeAgent 在内存中累计并最终写入
`model-configuration.json`：

```yaml
schema_version: 2
provider: codex-oauth
model: gpt-5.6-sol
logical_model_requests: 1
transport_attempts: 3
model_time_seconds: 20.0
generation_logical_requests: 1
repair_logical_requests: 0
```

进入 gateway 就计一个 logical request；breaker 拒绝的 logical request
计数为 1、transport attempt 为 0。transport 和 elapsed 从
`ModelResult` 或 `GatewayRequestError` 累加，不从 trace 行数反推。

- [ ] **Step 5: 删除旧重试路径**

删除：

- `src/foampilot/models/retry.py`
- `ModelClient`
- `ModelError`
- `TransportError`
- `SchemaOutputError`
- `CodexOAuthModelClient` 临时 alias
- `ModelRetryPolicy`
- `generate_with_retry`

所有旧调用改为 `CodexOAuthProviderClient → ModelGateway`。
Task 1 的 `test_current_retry_retries_transport_but_not_schema` 在本步骤删除；
其行为已经由 `tests/test_model_gateway.py` 的 retry/schema 测试取代，不能
为了保留 characterization 而保留旧生产接口。

- [ ] **Step 6: 运行迁移测试和旧符号审计**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_model_boundary.py \
  tests/test_model_gateway.py \
  tests/test_native_case_generation.py \
  tests/test_native_repair.py \
  tests/test_native_agent_state_machine.py -q
rg -n "generate_with_retry|ModelRetryPolicy|TransportError|ModelClient|CodexOAuthModelClient" \
  src tests
```

Expected: pytest PASS；`rg` 无生产代码命中。

- [ ] **Review checkpoint**

对比改动前后的 generation/repair prompt SHA256，确认只是调用边界迁移，
没有改变模型看到的任务内容。不得提交。

---

### Task 6: A4 WorkflowStore、阶段事件与 checkpoint

**Files:**

- Create: `src/foampilot/workflow/models.py`
- Create: `src/foampilot/workflow/events.py`
- Create: `src/foampilot/workflow/store.py`
- Create: `src/foampilot/workflow/__init__.py`
- Create: `tests/test_workflow_store.py`
- Modify: `src/foampilot/agent/native_orchestrator.py`

**Interfaces:**

- Consumes: `ArtifactStore` 分配的未冻结 `run_dir`。
- Produces: append-only `workflow-events.jsonl`、`checkpoints/*.json`、
  `WorkflowStore.finish()`；不接管 ArtifactStore manifest。

- [ ] **Step 1: 写事件顺序、持久化和 JSON stdout 红测**

```python
def test_workflow_store_appends_ordered_fsynced_events(tmp_path: Path) -> None:
    store = WorkflowStore(run_dir=tmp_path, utc_now=fixed_utc_now)
    store.record(
        WorkflowEvent.started(
            stage=WorkflowStage.MODEL_GENERATION_STARTED,
            sequence=1,
            occurred_at=fixed_utc_now(),
        )
    )
    store.record(
        WorkflowEvent.completed(
            stage=WorkflowStage.PLAN_READY,
            sequence=2,
            occurred_at=fixed_utc_now(),
        )
    )

    events = [
        json.loads(line)
        for line in (tmp_path / "workflow-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [item["sequence"] for item in events] == [1, 2]
```

另加：

- sequence 重复或倒退被拒绝；
- checkpoint 使用临时文件加 `os.replace()` 原子落盘；
- 同名 checkpoint 不允许无声明覆盖；
- finish 只写 summary，不自行修改 parent；
- `--json` CLI stdout 仍只有最终一个 JSON 对象。
- 非 JSON CLI 通过 listener 实时打印 `stage state detail`，但不打印 prompt、
  output、token、account ID 或绝对 auth path。

- [ ] **Step 2: 运行 workflow 红测**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_workflow_store.py -q
```

Expected: FAIL，workflow package 尚不存在。

- [ ] **Step 3: 实现阶段与事件**

`WorkflowStage` 使用稳定值：

```text
TASK_VALIDATED
ENVIRONMENT_READY
CONTEXT_READY
MODEL_GENERATION_STARTED
PLAN_READY
CASE_MATERIALIZED
STATIC_INSPECTION_COMPLETE
OPENFOAM_STEP_STARTED
OPENFOAM_STEP_COMPLETE
PUBLIC_VALIDATION_COMPLETE
MODEL_REPAIR_STARTED
REPAIR_APPLIED
RUN_FINALIZED
```

阶段 B 再加入 `ROUTING_READY` 和 `REPAIR_SCOPE_READY` 的真实写入；Stage A
可以保留枚举值，但不得伪造未执行事件。

`WorkflowEvent` 固定包含 schema version、sequence、stage、state
(`started|completed|failed|deferred`)、UTC timestamp、attempt、step_id、
detail 和 evidence_paths。

- [ ] **Step 4: 实现 WorkflowStore**

```python
class WorkflowStore:
    def __init__(
        self,
        *,
        run_dir: Path,
        event_listener: Callable[[WorkflowEvent], None] | None = None,
    ) -> None:
        self.run_dir = run_dir.resolve()
        self.events_path = self.run_dir / "workflow-events.jsonl"
        self.event_listener = event_listener
        self._last_sequence = self._read_last_sequence()

    def record(self, event: WorkflowEvent) -> None:
        if event.sequence != self._last_sequence + 1:
            raise ValueError("workflow event sequence must be contiguous")
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(event.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._last_sequence = event.sequence
        if self.event_listener is not None:
            self.event_listener(event)
```

`checkpoint()` 对 payload canonical JSON 计算 SHA256，并写
`checkpoints/<name>.json`；返回路径。`finish()` 只写
`summary.json`，ArtifactStore.finalize 仍由 orchestrator 最后调用。
CLI `--json` 路径传入 `event_listener=None`；human 路径传入只输出脱敏
摘要的 printer。

- [ ] **Step 5: 在 NativeAgent 中记录真实阶段**

每个阶段只在实际开始/完成时记录。Runner 的每个 typed command 分别产生
`OPENFOAM_STEP_STARTED` 和 `OPENFOAM_STEP_COMPLETE`；为此向 PlanRunner
增加可选只读 event callback 会改变 Runner 接口，因此 Stage A 不修改
Runner，而是在 `run_result.steps` 返回后补写带原始
`started_at/finished_at` 的完成事件；未返回的正在执行 step 不伪造开始
事件。更细实时事件留给后续独立设计。

- [ ] **Step 6: 运行 workflow/state-machine 测试**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_workflow_store.py \
  tests/test_native_agent_state_machine.py \
  tests/test_native_agent_cli.py -q
```

Expected: PASS；每个冻结 run 均有可解析、sequence 连续的 events。

- [ ] **Review checkpoint**

确认 WorkflowStore 没有复制 ArtifactStore 的 manifest/verify 职责，且
orchestrator 没有新增散落的 workflow JSON 写入。不得提交。

---

### Task 7: A5 RunSummary v2 与双故障语义

**Files:**

- Modify: `src/foampilot/artifacts/models.py`
- Modify: `src/foampilot/artifacts/store.py`
- Modify: `src/foampilot/artifacts/__init__.py`
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Modify: `tests/test_native_agent_state_machine.py`
- Modify: `tests/test_native_agent_cli.py`
- Modify: `tests/test_qualification_reporting.py`

**Interfaces:**

- Consumes: workflow events、gateway failure、native validation failure。
- Produces: `RunSummary v2`、`FailureRecord`、`ResumeMetadata`、
  `ParentRun`、v1 只读 `adapt_v1_summary()`。

- [ ] **Step 1: 写双故障和 nullable native status 红测**

```python
def test_solver_failure_survives_repair_provider_overload(
    tmp_path: Path,
) -> None:
    outcome = solve_with(
        runner=solver_failure_runner(),
        gateway=gateway_with_generation_then_overload(),
        root=tmp_path,
    )

    assert outcome.summary.workflow_state == WorkflowState.DEFERRED
    assert outcome.summary.native_status == "SOLVER_FAILED"
    assert outcome.summary.primary_failure.domain == FailureDomain.SOLVER
    assert outcome.summary.terminal_blocker.domain == FailureDomain.PROVIDER
    assert (
        outcome.summary.terminal_blocker.code
        == "PROVIDER_OVERLOADED"
    )
    assert outcome.summary.resume.allowed
    assert outcome.summary.resume.from_stage == "MODEL_REPAIR_STARTED"


def test_generation_provider_failure_has_no_native_status(tmp_path: Path) -> None:
    outcome = solve_with(
        gateway=always_overloaded_gateway(),
        root=tmp_path,
    )
    assert outcome.summary.workflow_state == WorkflowState.DEFERRED
    assert outcome.summary.native_status is None
    assert outcome.summary.primary_failure is None
    assert outcome.summary.terminal_blocker.code == "PROVIDER_OVERLOADED"
```

另加 plan invalid、environment failure、public validation failure 和成功
路径。
Task 1 的
`test_repair_transport_failure_currently_overwrites_solver_status` 在本步骤
替换为上面的双故障测试；旧缺陷断言不得继续存在。

- [ ] **Step 2: 运行 summary 红测**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_native_agent_state_machine.py \
  tests/test_native_agent_cli.py \
  tests/test_qualification_reporting.py -q
```

Expected: FAIL，当前 summary 只有一个 status。

- [ ] **Step 3: 实现 v2 类型**

```python
class WorkflowState(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEFERRED = "DEFERRED"


class FailureDomain(StrEnum):
    TASK = "task"
    ENVIRONMENT = "environment"
    PROVIDER = "provider"
    PLAN = "plan"
    CASE = "case"
    INSPECTION = "inspection"
    MESH = "mesh"
    INITIALIZATION = "initialization"
    SOLVER = "solver"
    POSTPROCESS = "postprocess"
    VALIDATION = "validation"
    LEGACY = "legacy"


class FailureRecord(StrictModel):
    domain: FailureDomain
    code: str
    step_id: str | None = None
    retryable: bool = False
    detail: str
    evidence_paths: list[str] = Field(default_factory=list)


NativeStatus = Literal[
    "STATIC_INSPECTION_FAILED",
    "MESH_FAILED",
    "INITIALIZATION_FAILED",
    "SOLVER_FAILED",
    "POSTPROCESS_FAILED",
    "PUBLIC_VALIDATION_FAILED",
    "PUBLIC_VALIDATION_PASS",
]


class ResumeMetadata(StrictModel):
    allowed: bool = False
    from_stage: str | None = None
    reason: str


class ParentRun(StrictModel):
    run_id: str
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RunSummary(StrictModel):
    schema_version: Literal[2] = 2
    task_id: str
    workflow_state: WorkflowState
    native_status: NativeStatus | None = None
    last_completed_stage: str | None = None
    attempts: list[AttemptSummary] = Field(default_factory=list)
    primary_failure: FailureRecord | None = None
    terminal_blocker: FailureRecord | None = None
    resume: ResumeMetadata
    parent_run: ParentRun | None = None
    message: str
```

`NativeStatus` 只保留 static inspection、mesh、initialization、solver、
postprocess、public validation failed/pass；task、plan、case generation 和
provider 不得进入该类型。

- [ ] **Step 4: 实现 v1 只读 adapter**

`ArtifactStore.read_summary()` 先读取 JSON 的 `schema_version`：

- v2 直接 `RunSummary.model_validate()`；
- v1 调用 `adapt_v1_summary(payload)`；
- 其他版本抛 `ValueError`。

adapter 规则固定：

- `PUBLIC_VALIDATION_PASS` → completed + 同名 native status；
- 真实 native failure → failed + 同名 native status + legacy failure；
- `BLOCKED_ENVIRONMENT` → failed + environment legacy failure；
- `PLAN_INVALID/CASE_GENERATION_FAILED/REQUEST_INCOMPLETE` → failed +
  domain 对应 failure，native status 为 null；
- v1 `resume.allowed=false`，reason 为 `legacy summaries cannot resume`。

case materialization 在任何 native inspection/command 前失败时，不创建
伪造的 `AttemptSummary`；它进入 `primary_failure.domain=case`，且
`native_status=null`。`AttemptSummary.status` 因而只使用 `NativeStatus`。

- [ ] **Step 5: 修改 orchestrator 的结束路径**

用一个 `finish_run()` 构造 v2 summary；任何异常只能填充对应的
`primary_failure` 或 `terminal_blocker`，不得通过 message 文本推断状态。
repair provider failure 必须保留最近 native attempt 的 status/fingerprint。

`NativeAgentOutcome.status` 暂时作为只读 property：

```python
@property
def status(self) -> str:
    if self.summary.native_status is not None:
        return self.summary.native_status
    return self.summary.workflow_state.value
```

它只为现有调用方提供单值显示，不写回 summary，也不用于根因分类。

- [ ] **Step 6: 运行 v1/v2 与状态机测试**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_native_agent_state_machine.py \
  tests/test_native_agent_cli.py \
  tests/test_qualification_reporting.py \
  tests/test_artifact_replay.py -q
```

Expected: PASS；历史 fixture 可报告但 `resume.allowed` 为 false。

- [ ] **Review checkpoint**

逐项检查每个 orchestrator return 分支：generation 前的错误
`native_status=null`，native attempt 后的 provider blocker 不覆盖
`primary_failure/native_status`。不得提交。

---

### Task 8: A3 Qualification 共享 Gateway 与分层指标

**Files:**

- Modify: `src/foampilot/qualification/models.py`
- Modify: `src/foampilot/qualification/runner.py`
- Modify: `src/foampilot/qualification/reporting.py`
- Create: `tests/test_qualification_gateway.py`
- Modify: `tests/test_qualification_reporting.py`
- Modify: `tests/test_qualification_cli.py`

**Interfaces:**

- Consumes: 一个 suite 共享的 `ModelGateway`、每题独立 ledger/trace/run。
- Produces: `DEFERRED_PROVIDER` qualification 状态；逻辑请求与 transport
  attempt 分开统计；lineage-aware 结果。

- [ ] **Step 1: 写 suite 共享 breaker 红测**

```python
def test_suite_shares_breaker_and_skips_transport_after_open(
    tmp_path: Path,
) -> None:
    provider = CountingProvider(always=overloaded_error())
    gateway = ModelGateway(
        provider=provider,
        circuit_breaker=SharedCircuitBreaker(
            failure_threshold=2,
            cooldown_seconds=120,
        ),
        sleep=lambda seconds: None,
    )

    report = run_qualification_suite(
        suite=three_case_fake_suite(),
        run_root=tmp_path,
        workers=2,
        model_name="fake-model",
        gateway=gateway,
    )

    assert provider.exchange_calls <= 6
    assert report.counts["DEFERRED_PROVIDER"] >= 1
    assert any(
        item.transport_attempts == 0
        for item in report.results
        if item.status == "DEFERRED_PROVIDER"
    )
```

为避免并发顺序使断言不稳定，fake suite 使用 barrier 让前两个逻辑请求
先完成失败，再启动第三题。
Task 1 的
`test_two_qualification_workers_currently_construct_separate_clients` 在本步骤
替换为共享 gateway/breaker 测试；旧构造行为不再保留。

- [ ] **Step 2: 运行 qualification 红测**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_qualification_gateway.py \
  tests/test_qualification_reporting.py -q
```

Expected: FAIL，runner 当前每题构造独立 client。

- [ ] **Step 3: 改为 suite 级 gateway factory**

`run_qualification_suite()` 正式 CLI 路径只加载一次 token、构造一次
`CodexOAuthProviderClient`、一次 `SharedCircuitBreaker` 和一次
`ModelGateway`。测试可以通过可选 keyword-only `gateway` 注入 fake；
`_run_one()` 必须接收 gateway，不再接收 token 或自行构造 client。

每道题仍创建：

- 独立 `ArtifactStore(run_root / case_id)`；
- 独立 `ModelBudgetLedger`；
- 独立 `model-attempts.jsonl`；
- 独立 `NativeAgent`；
- 独立 case 和 Runner。

- [ ] **Step 4: 扩展 qualification result/report**

`QualificationStatus` 增加 `DEFERRED_PROVIDER`。`QualificationResult` 增加：

```python
workflow_state: WorkflowState
native_status: str | None
logical_model_requests: int = Field(ge=0)
transport_attempts: int = Field(ge=0)
model_time_seconds: float = Field(ge=0)
provider_deferred: bool
native_execution_started: bool
mesh_generation_pass: bool | None
check_mesh_pass: bool | None
target_solver_started: bool
solver_normal_completion: bool
public_validation_pass: bool
physics_qualification_pass: bool
time_to_first_openfoam_command: float | None = Field(default=None, ge=0)
openfoam_time_seconds: float = Field(ge=0)
```

Stage A 没有 command stage，因此只采用可靠证据：

- `native_execution_started`：任一 `run-result.steps` 存在；
- `check_mesh_pass`：存在 executable 为 `checkMesh` 的 step 时按返回码和
  semantic result判断，否则 null；
- `mesh_generation_pass`：存在 `blockMesh` 或 `gmsh` step 时判断，否则
  null；
- `target_solver_started`：step command 首元素等于 private validation 的
  `expected_application`；
- `solver_normal_completion`：目标 solver step return code 0 且日志正常
  结束；
- 不能可靠判断的值必须为 null，不得默认为 false。

`QualificationReport` 升级 schema v2，并增加 aggregate counts：

```text
task_count
logical_model_requests
transport_attempts
provider_deferred_count
generation_success_count
native_execution_started_count
mesh_generation_pass_count
check_mesh_pass_count
target_solver_started_count
solver_normal_completion_count
public_validation_pass_count
physics_qualification_pass_count
model_time_seconds
openfoam_time_seconds
```

`run_metadata()` 沿 `parent_run` 链读取每个冻结 run 的
`model-configuration.json` 并去重累加 logical requests、transport attempts
和 model time；若任一 parent manifest 校验失败，qualification 结果为
`INVALID_QUALIFICATION`，不得跳过坏 parent。

- [ ] **Step 5: 实现 classification**

顺序固定：

1. manifest invalid → `FAIL_AGENT`；
2. workflow deferred 且 terminal blocker domain provider →
   `DEFERRED_PROVIDER`；
3. workflow failed 且 environment failure → `BLOCKED_ENVIRONMENT`；
4. native/public failure → `FAIL_AGENT`；
5. evaluator evidence 不完整 → `INVALID_QUALIFICATION`；
6. physics required metric failed → `FAIL_AGENT`；
7. 全部通过 → `PASS`。

- [ ] **Step 6: 运行 qualification 测试**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_qualification_gateway.py \
  tests/test_qualification_reporting.py \
  tests/test_qualification_cli.py \
  tests/test_qualification_suites.py -q
```

Expected: PASS；Markdown 分别显示 workflow、native、logical calls 和
transport attempts。

- [ ] **Review checkpoint**

确认 worker 只共享 gateway/breaker，不共享 ledger、trace、ArtifactStore、
case 或 evaluator 临时目录。不得提交。

---

### Task 9: A6 Strict Continuation、fingerprint 与 lineage budget

**Files:**

- Create: `src/foampilot/workflow/lineage.py`
- Create: `tests/test_continuation.py`
- Modify: `src/foampilot/workflow/models.py`
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Modify: `src/foampilot/artifacts/store.py`
- Modify: `src/foampilot/cli/main.py`
- Modify: `tests/test_native_agent_cli.py`
- Modify: `tests/test_lean_package_boundary.py`

**Interfaces:**

- Consumes: v2 parent summary、parent manifest、parent checkpoints、
  current environment/context/provider policy。
- Produces: `ResumeCompatibility`、`ContinuationInput`、
  `build_resume_fingerprint()`、`prepare_continuation()`、
  `NativeAgent.resume()`、`foampilot resume`。

- [ ] **Step 1: 写 parent immutable 与 repair resume 红测**

```python
def test_resume_repair_creates_child_and_preserves_parent(
    tmp_path: Path,
) -> None:
    parent = make_solver_failure_then_provider_deferred_run(tmp_path)
    parent_manifest = (parent / "artifact-manifest.json").read_bytes()
    child = resumable_agent(tmp_path).resume(parent)

    assert child.run_dir != parent
    assert (parent / "artifact-manifest.json").read_bytes() == parent_manifest
    assert ArtifactStore(tmp_path).verify(parent) == []
    assert child.summary.parent_run.run_id == parent.name
    assert (
        child.summary.parent_run.manifest_sha256
        == sha256(parent_manifest).hexdigest()
    )
    assert child.summary.workflow_state == WorkflowState.COMPLETED
```

另加：

- generation resume 重新发起完整 generation；
- repair resume 复用 parent active plan、public report、failure/log evidence；
- v1 summary 拒绝；
- non-retryable blocker 拒绝；
- parent manifest mismatch 拒绝；
- TaskSpec、public asset、model、provider policy、knowledge、Skill、package、
  plan schema 或 OpenFOAM target 改变均拒绝 strict resume；
- host path 改变但 runtime capability 兼容时允许并写 warning；
- executable 或 OpenFOAM version 改变拒绝；
- 每阶段第 3 个 continuation 拒绝；
- lineage 第 8 个 transport attempt 在发送前拒绝。

- [ ] **Step 2: 运行 continuation 红测**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_continuation.py -q
```

Expected: FAIL，lineage 类型和 resume 命令尚不存在。

- [ ] **Step 3: 实现 ResumeCompatibility**

```python
class ResumeCompatibility(StrictModel):
    task_sha256: str
    public_assets_sha256: str | None
    model: str
    provider: str
    provider_policy_sha256: str
    package_version: str
    package_artifact_sha256: str
    git_revision: str | None
    execution_plan_schema: int
    knowledge_ids: list[str]
    knowledge_hash: str
    skill_ids: list[str]
    skill_hash: str
    openfoam_target: dict[str, str]
    executable_names: list[str]


class ContinuationInput(StrictModel):
    parent_run: Path
    parent_manifest_sha256: str
    from_stage: Literal[
        "MODEL_GENERATION_STARTED",
        "MODEL_REPAIR_STARTED",
    ]
    parent_summary: RunSummary
    active_plan_path: Path | None
    public_validation_path: Path | None
    failed_log_paths: list[Path]
    transport_attempts_used: int
    continuation_index_for_stage: int
    environment_warnings: list[str]


class ResumeCompatibilityError(ValueError):
    def __init__(self, field: str) -> None:
        super().__init__(
            f"strict resume rejected: {field} changed; "
            "use rerun_with_changes"
        )
        self.field = field
```

所有 SHA256 字段使用 64-hex validator。package artifact hash 对已安装 wheel
使用 `importlib.metadata.files("foampilot")` 中 Python/YAML/Markdown 文件的
path+content canonical hash；源码 checkout 同样计算内容 hash，不依赖 mtime。

- [ ] **Step 4: 实现 strict compatibility 检查**

`prepare_continuation()` 固定顺序：

1. `ArtifactStore.verify(parent_run)`；
2. 读取 v2 summary；
3. 检查 `resume.allowed` 和 from_stage；
4. 计算 parent manifest SHA256；
5. 读取 `resume-compatibility.json`；
6. 比较 strict 字段；
7. 重新 discover environment 并比较 distribution/version/executables；
8. 沿 `parent_run` 链防循环地累计 continuation 和 transport attempts；
9. 构造只引用 parent 冻结证据的 `ContinuationInput`。

任一 strict 字段不兼容时抛 `ResumeCompatibilityError`，消息必须包含：

```text
strict resume rejected: <field> changed; use rerun_with_changes
```

首次 `solve()` 在 `ENVIRONMENT_READY` 和 `CONTEXT_READY` 后、任何 generation
请求前构造 fingerprint，并通过 WorkflowStore 写
`resume-compatibility.json`。因此 generation 第一次 transport 就失败时
仍有可比较的 parent fingerprint。每次 active plan 更新、公开验证完成或
repair 即将开始时分别写不可覆盖的 checkpoint：

```text
checkpoints/active-plan-attempt-01.json
checkpoints/public-validation-attempt-01.json
checkpoints/repair-evidence-attempt-01.json
```

repair evidence 只包含公开 report、失败 step、failure fingerprint 和日志
artifact 相对路径，不复制 private evaluator/golden。

- [ ] **Step 5: 实现 generation/repair continuation**

`NativeAgent.resume(parent_run)`：

- 创建 child run 后立即写 parent metadata；
- generation：复用 TaskSpec/public assets/context fingerprint，重新调用完整
  generation，然后进入 canonical plan/materialize/inspect/run 流程；
- repair：载入 parent active plan、最后失败 report 和公开日志，发起 scoped
  之前的现有 full repair 请求；Stage C 才缩小 RepairScope；
- child 重新 materialize case 并从 inspection 开始执行；
- parent case 和 parent attempt 不作为可写 workspace；
- child 的 ledger 用 parent lineage transport count 初始化，并获得新的
  360/240 秒 stage deadline；
- child 成功/失败后照常冻结自己的 manifest。

- [ ] **Step 6: 增加 `foampilot resume` CLI**

参数：

```text
foampilot resume PARENT_RUN
  --run-root RUN_ROOT
  [--auth AUTH]
  [--model-name MODEL]
  [--max-mpi-ranks N]
  [--json]
```

`COMMANDS` 增加 `resume`。exit code 固定：

- 0：completed/public validation pass；
- 3：deferred provider 或 blocked environment；
- 4：native/validation/plan failure；
- 2：strict compatibility 或输入错误；
- 5：内部错误。

- [ ] **Step 7: 运行 continuation/CLI 测试**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_continuation.py \
  tests/test_native_agent_cli.py \
  tests/test_lean_package_boundary.py -q
```

Expected: PASS；parent 目录在 resume 前后 `find -printf '%P %s %T@'` 与
manifest bytes 均不变。

- [ ] **Review checkpoint**

人工审计 child lineage：没有 symlink 指回 parent 的可写 case 文件，没有
复制 secret，没有用 message 文本决定 resume eligibility。不得提交。

---

### Task 10: A7 全量确定性回归、故障注入、真实最小 gate 与文档

**Files:**

- Modify: `tests/test_real_native_vertical_slice.py`
- Modify: `docs/architecture.md`
- Modify: `docs/independent-agent-quickstart.md`
- Modify: `docs/qualification.md`
- Modify: `docs/architecture-optimization-design.md`

**Interfaces:**

- Consumes: Tasks 1–9 的 canonical Stage A 路径。
- Produces: 阶段 A 验收证据和用户可复现的 solve/deferred/resume 文档。

- [ ] **Step 1: 建立完整 fake-provider 故障矩阵**

确定性测试必须逐项证明：

1. generation 首次 overload 后成功；
2. generation 持续 overload，最多 3 次；
3. repair overload 保留 solver failure；
4. auth failure 1 次；
5. permission failure 1 次；
6. SSE interrupted 最多 2 次；
7. schema invalid 1 次；
8. request timeout 关闭 response；
9. stage deadline 在 backoff 前停止；
10. total model deadline 跨 generation/repair 生效；
11. breaker half-open 成功与失败；
12. solver failure → repair provider overload → resume repair → success；
13. parent/child manifest 与 lineage budget；
14. qualification breaker 后续 task 0 HTTP；
15. logical request 与 transport attempt 分开统计。

- [ ] **Step 2: 跑 Stage A 定向测试**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_model_boundary.py \
  tests/test_model_gateway.py \
  tests/test_circuit_breaker.py \
  tests/test_workflow_store.py \
  tests/test_continuation.py \
  tests/test_qualification_gateway.py \
  tests/test_native_agent_state_machine.py \
  tests/test_qualification_reporting.py \
  tests/test_artifact_replay.py -q
```

Expected: PASS，无 warning 被当作测试成功条件。

- [ ] **Step 3: 跑全仓自动化测试**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m pytest -q
```

Expected: 全部非 opt-in 测试 PASS；真实模型测试保持 skip，除非显式设置
`OFKIT_RUN_REAL_MODEL=1`。

- [ ] **Step 4: 构建并从 wheel 做 preflight**

Run:

```bash
/home/edwin/feal-venv-py312/bin/python -m build
/home/edwin/feal-venv-py312/bin/python -m pip install \
  --force-reinstall --no-deps dist/foampilot-0.1.0-py3-none-any.whl
/home/edwin/feal-venv-py312/bin/foampilot preflight --json
```

Expected: wheel 构建成功；preflight 报 Foundation OpenFOAM v10、
workspace writable、bubblewrap 可用和所需 executable 可发现。

- [ ] **Step 5: 运行一个最小真实 gateway + OpenFOAM gate**

仅使用公开
`examples/tasks/non-tutorial-side-driven-box.yaml`，不得读取 tutorial：

先把真实测试的参数化 ID 固定为：

```python
@pytest.mark.parametrize(
    "task_path",
    TASKS,
    ids=("side-driven-box", "two-phase-column"),
)
```

```bash
OFKIT_RUN_REAL_MODEL=1 \
OFKIT_CODEX_MODEL=gpt-5.6-sol \
/home/edwin/feal-venv-py312/bin/python -m pytest \
  tests/test_real_native_vertical_slice.py \
  -k side-driven-box -vv
```

Expected:

- gateway 至少一条成功 transport trace；
- workflow event sequence 连续；
- `blockMesh → checkMesh → icoFoam` 通过 canonical Runner；
- RunSummary schema v2；
- final native status `PUBLIC_VALIDATION_PASS`；
- ArtifactStore verify 无问题；
- 没有 tutorial/golden/private evaluator 泄漏。

若模型服务在 gate 期间 overload，结果应是可审计的 `DEFERRED`，不能写成
CFD 回归失败；provider 恢复后用 `foampilot resume` 继续同一 lineage。

- [ ] **Step 6: 运行 solver failure + provider blocker + resume 真实/半真实 gate**

使用 fake provider 和真实 OpenFOAM Runner 构造：

1. 首次模型输出一个会进入目标 solver、随后因公开可见字典错误失败的
   非 tutorial case；
2. repair provider 返回 overload；
3. parent summary 同时保存 solver failure 和 provider blocker；
4. continuation provider 返回修复；
5. child 重新 materialize、运行并通过公开验证。

该 gate 的 provider 响应是测试内固定公开 fixture，不调用第二个模型，
因此可重复、无 golden 泄漏。

- [ ] **Step 7: 更新文档**

`docs/architecture.md` 写入当前已实现组件，而不是未来 Stage B–D。
`docs/independent-agent-quickstart.md` 增加：

- `solve` 正常流程；
- provider deferred 的 summary 示例；
- `resume` 命令；
- strict resume 与 `rerun_with_changes` 的区别；
- parent/child artifact 查验命令。

`docs/qualification.md` 增加：

- shared breaker；
- `DEFERRED_PROVIDER` 不等于 Agent/CFD 失败；
- logical request 与 transport attempt；
- target solver 与 native utility 的区别；
- lineage 累计时间和 attempt。

`docs/architecture-optimization-design.md` 的 Stage A 状态更新为
“已实施，证据见阶段 A 验收记录”，只有真实 gate 完成后才能更新。

- [ ] **Step 8: 最终静态审计**

Run:

```bash
rg -n "generate_with_retry|ModelRetryPolicy|TransportError|ModelClient|CodexOAuthModelClient" \
  src tests
rg -n "access_token|Authorization|ChatGPT-Account-Id|Bearer |sk-" \
  docs tests/fixtures/artifact-replay
git status --short
git diff --check
```

Expected:

- 第一条无生产路径旧符号；
- 第二条无 secret 值，文档中的字段名说明可以保留；
- `git diff --check` 无错误；
- 工作树只包含本计划范围内文件和用户原有改动；
- 没有 `docs/superpowers/` 新改动；
- 没有 commit 或 push。

- [ ] **Review checkpoint**

向用户报告：

- 自动化测试数和结果；
- fake-provider 故障矩阵；
- 真实最小 gate 的 run path、manifest hash、workflow/native 状态；
- provider deferred/resume 证据；
- 仍未实施的 Stage B、C、D；
- 当前工作树和提交状态。

用户确认阶段 A 证据后，再单独为阶段 B 编写实施计划，不在本计划中提前
实现 CapabilityRouter、CaseManifest、semantic inspector 或 normalizer。

---

## 3. 阶段 A 完成定义

只有同时满足以下条件，才能将阶段 A 标记完成：

1. auth/permission 各只发送一次 transport；
2. overload/network 单逻辑请求最多 3 次，退避和 deadline 均可测试；
3. request timeout、stage deadline 和 total deadline 能被区分；
4. SSE response 在所有结束路径显式关闭；
5. suite worker 共享同一个线程安全 breaker；
6. breaker 打开后的后续题 transport attempt 为 0；
7. generation 前 provider failure 的 native status 为 null；
8. repair provider failure 不覆盖原 solver/native failure；
9. parent immutable，child 记录 parent manifest hash；
10. strict resume 受 fingerprint、每阶段 continuation 和 lineage attempt
    三重预算约束；
11. v1 summary 可读不可 resume；
12. logical model request 与 transport attempt 分开统计；
13. 非 opt-in 自动化测试全部通过；
14. 一个非 tutorial 真实 case 通过 canonical gateway、state machine、
    Runner 和 public validation；
15. 没有改变 case prompt、TaskSpec、ExecutionPlan、检索、Skill、case
    文件或 Runner 语义；
16. 没有旧 provider/retry 主路径、长期 compatibility 分支或
    `docs/superpowers/` 新内容；
17. 没有 commit 或 push。

## 4. 明确留给后续阶段的内容

以下内容不属于本计划，阶段 A 实施者不得顺手加入：

- CapabilityRouter 与 deterministic confidence；
- slot-based retrieval；
- dynamic family Skill；
- ExecutionPlan v3、region-aware CaseManifest 和 command stage；
- MPI command normalizer；
- solver-family semantic contract；
- FailureClassifier、RepairScope、RepairPatch 和 command insertion；
- improvement RootCause/ImprovementTarget 扩展；
- 官方六题或全量 15 题复测。

这些能力分别属于阶段 B、C、D。阶段 A 的唯一真实 OpenFOAM gate 是一个
最小非 tutorial case；阶段 B 完成后再按已确认规格执行官方六题各一次。
