# FoamPilot Runtime Portability and Execution Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现可跨 Ubuntu 用户和 Foundation OpenFOAM v10 安装路径复用的 Runtime 配置，并让每次求解在风险审计后安全选择 bubblewrap 或 audited host 后端。

**Architecture:** 保留唯一的 `TaskSpec -> NativeAgent.solve()` 求解状态机。CLI、环境变量、用户 TOML 和有限自动发现先汇合为 `RuntimeResolution`；环境发现派生 tutorial/command 路径；materialized case 生成 `ExecutionRiskReport`；共享 sandbox builder 完成 preflight 与真实 launch probe；`PlanRunner` 在首条命令前冻结后端，`NativeAgent` 把配置、来源、风险、probe 和策略决策写入不可变 artifact。

**Tech Stack:** Python 3.12、标准库 `tomllib`、Pydantic v2、pytest、bubblewrap、Foundation OpenFOAM v10、可选 PySide6-Essentials。

## Global Constraints

- 权威规格为 `docs/design/runtime-portability-execution-security-design.md`，状态为 2026-08-11 已冻结。
- 只 qualification Foundation OpenFOAM v10；`distribution="foundation"`，`version="10"`。
- 不增加 OpenFOAM 自动安装、ESI/OpenCFD 支持、容器后端、远程 worker 或第二套求解状态机。
- 配置优先级固定为 CLI > `FOAMPILOT_*` > 显式 TOML > `FOAMPILOT_RUNTIME_CONFIG` TOML > XDG 用户 TOML > 有限发现/默认值。
- 不读取工程目录内的 Runtime TOML；TaskSpec、模型输出和 case 内容不能降低 isolation。
- 普通 solve/Desktop 默认 `sandbox_preferred`；qualification 强制 `sandbox_required`。
- `trusted_host` 的 high/unknown 风险执行必须同时要求 `allow_dynamic_code_on_host=true`。
- preflight 和真实 Runner 必须调用同一个 mount-plan builder；首条 step 开始后不得切换 backend。
- Runtime schema 不包含 `python_executable`；诊断只记录 `sys.executable`；bubblewrap 只通过显式绝对路径或 `shutil.which("bwrap")` 获取。
- 不新增第三方依赖；TOML 使用 Python 3.12 标准库。
- 保留当前工作树中已有 Desktop/TaskBuilder 改动，不 reset、不覆盖、不把无关改动混入 P0-A 审计。
- 本计划不执行 release version、tag 或 P0-C evaluator 拆包；这些属于后续 P0-B/P0-C。
- 当前仓库规则禁止自动提交；本计划以逐任务 review checkpoint 代替中间 commit，最终提交由已批准的 P0-B 发布步骤统一处理。

---

## File and Interface Map

### 新建文件

- `src/foampilot/runtime/config.py`：严格 TOML/环境变量/CLI 合并、有限 root 发现、字段 provenance。
- `src/foampilot/runtime/protection.py`：合并 TaskSpec、当前环境与 evaluator 的保护路径，避免 Runtime/environment 模型循环依赖。
- `src/foampilot/runtime/risk.py`：materialized case 静态执行风险扫描。
- `src/foampilot/runtime/policy.py`：三档 isolation 的纯决策矩阵。
- `tests/test_runtime_config.py`：配置 schema、优先级、发现和 provenance。
- `tests/test_execution_risk.py`：OpenFOAM directive、include 和动态库风险夹具。
- `tests/test_sandbox.py`：动态 mount plan、路径遮蔽和完整 probe。

### 修改文件

- `src/foampilot/runtime/models.py`：Runtime、provenance、probe、risk、policy 与 run result contracts。
- `src/foampilot/runtime/__init__.py`：只导出规范公共接口。
- `src/foampilot/environment/models.py`：保存 source 后的 tutorial root 和命令事实。
- `src/foampilot/environment/discovery.py`：验证 Foundation v10、root 一致性和可信 executable root。
- `src/foampilot/runtime/sandbox.py`：用一个 builder 生成 preflight/Runner 的完整 argv。
- `src/foampilot/runtime/preflight.py`：返回结构化 `RuntimePreflightReport`。
- `src/foampilot/runtime/plan_runner.py`：接收风险报告，执行真实 probe，一次性冻结 backend。
- `src/foampilot/agent/native_orchestrator.py`：每个 attempt 重新扫描并固化 Runtime/执行证据。
- `src/foampilot/cli/main.py`：共享 Runtime 参数和 resolver，删除所有本机工厂调用。
- `src/foampilot/qualification/runner.py`、`validators.py`、`profiles.py`：显式传递同一 Runtime，qualification 拒绝非 required 策略。
- `src/foampilot/desktop/viewmodels.py`、`repository.py`、`main_window.py`、`application.py`：只从规范 artifact 投影 Runtime 安全状态，并把显式 Desktop Runtime 参数传给子命令。
- `src/foampilot/qualification/data/tasks/*.yaml`、`examples/**/*.yaml`：删除个人 tutorial 绝对路径，由运行时派生保护路径。
- `tests/test_runtime.py`、`test_environment_discovery.py`、`test_plan_runner.py`、`test_native_agent_state_machine.py`、`test_native_agent_cli.py`、`test_qualification_cli.py`、`test_qualification_gateway.py`、`test_native_qualification_assets.py`、`test_repository_boundary.py`、`test_desktop_repository.py`、`test_desktop_main_window.py`：迁移并覆盖新合同。
- `README.md`、`AGENTS.md`、`docs/architecture.md`、`docs/system-overview.md`、`docs/independent-agent-quickstart.md`、`docs/desktop-ide.md`：公开配置、策略和证据语义。

### 规范接口

```python
def resolve_runtime_config(
    *,
    cli_overrides: RuntimeOverrides | None = None,
    environ: Mapping[str, str] | None = None,
    explicit_config: Path | None = None,
    user_config: Path | None = None,
    candidate_roots: Sequence[Path] = (),
    default_isolation: IsolationPolicy = "sandbox_preferred",
) -> RuntimeResolution

def discover_environment(
    config: RuntimeConfig,
    workspace_root: str | Path,
    shortlisted: Iterable[str] = (),
) -> EnvironmentSnapshot

def scan_execution_risk(
    case_root: str | Path,
    *,
    openfoam_root: Path,
    trusted_readonly_roots: Sequence[Path] = (),
) -> ExecutionRiskReport

def build_sandbox_argv(
    *,
    config: RuntimeConfig,
    environment: EnvironmentSnapshot,
    case_dir: Path,
    protected_paths: Sequence[Path],
    memory_mib: int,
    cpu_seconds: int,
    typed_argv: Sequence[str],
) -> SandboxLaunch

def decide_execution_policy(
    config: RuntimeConfig,
    risk: ExecutionRiskReport,
    probe: SandboxProbe,
) -> ExecutionPolicyDecision
```

---

### Task 1: 严格 Runtime schema、TOML 合并与字段 provenance

**Files:**
- Create: `src/foampilot/runtime/config.py`
- Modify: `src/foampilot/runtime/models.py:1-45`
- Modify: `src/foampilot/runtime/__init__.py:1-32`
- Create: `tests/test_runtime_config.py`
- Modify: `tests/test_runtime.py:1-30`

**Interfaces:**
- Consumes: Python 3.12 `tomllib`、`os.environ`、`shutil.which`。
- Produces: `RuntimeOverrides`、`RuntimeConfig`、`RuntimeFieldSource`、`RuntimeConfigProvenance`、`RuntimeResolution`、`RuntimeConfigError`、`resolve_runtime_config()`。

- [ ] **Step 1: 写 strict schema 与默认值失败测试**

```python
def test_runtime_file_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "runtime.toml"
    path.write_text(
        'schema_version=1\n[openfoam]\ndistribution="foundation"\n'
        'version="10"\nunknown=true\n',
        encoding="utf-8",
    )
    with pytest.raises(RuntimeConfigError) as captured:
        resolve_runtime_config(
            explicit_config=path,
            candidate_roots=(tmp_path / "missing",),
            environ={},
        )
    assert captured.value.code == "RUNTIME_CONFIG_INVALID"


def test_runtime_defaults_to_sandbox_preferred(fake_openfoam: Path) -> None:
    resolution = resolve_runtime_config(
        environ={"FOAMPILOT_OPENFOAM_ROOT": str(fake_openfoam)},
        candidate_roots=(),
    )
    assert resolution.config.isolation == "sandbox_preferred"
    assert resolution.config.max_mpi_ranks == 4
    assert resolution.config.allow_dynamic_code_on_host is False
    assert resolution.provenance.fields["execution.isolation"].source == "default"


def test_runtime_accepts_qualification_default(fake_openfoam: Path) -> None:
    resolution = resolve_runtime_config(
        environ={"FOAMPILOT_OPENFOAM_ROOT": str(fake_openfoam)},
        candidate_roots=(),
        default_isolation="sandbox_required",
    )
    assert resolution.config.isolation == "sandbox_required"
    assert resolution.provenance.fields["execution.isolation"].source == "default"


def test_runtime_rejects_legacy_execution_backend(fake_openfoam: Path) -> None:
    with pytest.raises(ValidationError):
        RuntimeConfig(
            openfoam_root=fake_openfoam,
            execution_backend="auto",
        )
```

- [ ] **Step 2: 运行测试并确认旧工厂无法满足合同**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider tests/test_runtime_config.py
```

Expected: collection/import 失败，因为新接口尚不存在。

- [ ] **Step 3: 在 `runtime/models.py` 定义规范类型**

```python
IsolationPolicy = Literal[
    "sandbox_required", "sandbox_preferred", "trusted_host"
]
RuntimeSourceKind = Literal[
    "cli", "environment", "explicit_toml", "environment_toml",
    "user_toml", "discovery", "default", "python_api",
]


class RuntimeOverrides(StrictModel):
    openfoam_root: Path | None = None
    isolation: IsolationPolicy | None = None
    bubblewrap: str | None = None
    max_mpi_ranks: int | None = Field(default=None, ge=1)
    allow_dynamic_code_on_host: bool | None = None
    trusted_readonly_roots: tuple[Path, ...] | None = None


class RuntimeConfig(StrictModel):
    schema_version: Literal[1] = 1
    distribution: Literal["foundation"] = "foundation"
    version: Literal["10"] = "10"
    openfoam_root: Path
    isolation: IsolationPolicy = "sandbox_preferred"
    bubblewrap: Path | None = None
    max_mpi_ranks: int = Field(default=4, ge=1)
    allow_dynamic_code_on_host: bool = False
    trusted_readonly_roots: tuple[Path, ...] = ()


class RuntimeFieldSource(StrictModel):
    source: RuntimeSourceKind
    locator: str | None = None


class RuntimeConfigProvenance(StrictModel):
    schema_version: Literal[1] = 1
    fields: dict[str, RuntimeFieldSource]


class RuntimeResolution(StrictModel):
    config: RuntimeConfig
    provenance: RuntimeConfigProvenance


class RuntimeConfigError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        recovery: str,
        detail: str | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.recovery = recovery
        self.detail = detail
```

为 `openfoam_root`、`bubblewrap` 和 `trusted_readonly_roots` 增加 validator：必须为绝对路径；trusted root 不得为 `/`、当前用户 home 或 home 的父目录；解析后的重复项拒绝。删除 `local_foundation_v10()`、`tutorial_root` 和 `python_executable`。

- [ ] **Step 4: 实现配置文件模型与严格解析**

在 `runtime/config.py` 定义只用于 authoring 的三层模型：

```python
class OpenFOAMFileConfig(StrictModel):
    distribution: Literal["foundation"] | None = None
    version: Literal["10"] | None = None
    root: Path | None = None


class ExecutionFileConfig(StrictModel):
    isolation: IsolationPolicy | None = None
    bubblewrap: str | None = None
    max_mpi_ranks: int | None = Field(default=None, ge=1)
    allow_dynamic_code_on_host: bool | None = None
    trusted_readonly_roots: tuple[Path, ...] | None = None


class RuntimeFileConfig(StrictModel):
    schema_version: Literal[1]
    openfoam: OpenFOAMFileConfig = Field(default_factory=OpenFOAMFileConfig)
    execution: ExecutionFileConfig = Field(default_factory=ExecutionFileConfig)
```

`_load_toml(path)` 必须 canonicalize 后要求目标是普通文件（允许受信任用户配置使用有效 symlink，拒绝 dangling/non-file）、用 `tomllib.loads()` 解析、用 `RuntimeFileConfig.model_validate()` 拒绝未知字段，并把所有错误包装成 `RuntimeConfigError("RUNTIME_CONFIG_INVALID", "Runtime TOML 无效。", "修正未知字段、类型或路径后重试。")`；原始异常文本只作为不含秘密值的 detail 附加。

authoring 模型中的叶子必须保持 optional，以区分“文件未声明”与“文件显式声明”；resolver 最低层一次性注入 Foundation/v10、`default_isolation`、`bubblewrap=auto`、rank 4、host dynamic-code false 和空 trusted roots。否则只写 `[openfoam]` 的 TOML 会错误覆盖 qualification 的命令默认策略。

- [ ] **Step 5: 写逐字段优先级和秘密值保护测试**

```python
def test_runtime_precedence_is_leafwise(tmp_path: Path, fake_openfoam: Path) -> None:
    user = _runtime_toml(tmp_path / "user.toml", root=fake_openfoam, ranks=1)
    env_file = _runtime_toml(tmp_path / "env.toml", root=fake_openfoam, ranks=2)
    explicit = _runtime_toml(tmp_path / "explicit.toml", root=fake_openfoam, ranks=3)
    result = resolve_runtime_config(
        cli_overrides=RuntimeOverrides(max_mpi_ranks=6),
        explicit_config=explicit,
        user_config=user,
        environ={
            "FOAMPILOT_RUNTIME_CONFIG": str(env_file),
            "FOAMPILOT_MAX_MPI_RANKS": "5",
            "FOAMPILOT_OPENFOAM_ROOT": str(fake_openfoam),
        },
        candidate_roots=(),
    )
    assert result.config.max_mpi_ranks == 6
    assert result.provenance.fields["execution.max_mpi_ranks"].source == "cli"
    assert result.provenance.fields["execution.max_mpi_ranks"].locator == "--max-mpi-ranks"

    environment_only = resolve_runtime_config(
        environ={
            "FOAMPILOT_OPENFOAM_ROOT": str(fake_openfoam),
            "FOAMPILOT_MAX_MPI_RANKS": "5",
        },
        candidate_roots=(),
    )
    source = environment_only.provenance.fields["execution.max_mpi_ranks"]
    assert source.model_dump() == {
        "source": "environment",
        "locator": "FOAMPILOT_MAX_MPI_RANKS",
    }
    assert "5" not in source.model_dump_json()


@pytest.mark.parametrize("value", ["1", "yes", "TRUE", ""])
def test_runtime_boolean_environment_is_strict(value: str, fake_openfoam: Path) -> None:
    with pytest.raises(RuntimeConfigError, match="RUNTIME_CONFIG_INVALID"):
        resolve_runtime_config(
            environ={
                "FOAMPILOT_OPENFOAM_ROOT": str(fake_openfoam),
                "FOAMPILOT_ALLOW_DYNAMIC_CODE_ON_HOST": value,
            },
            candidate_roots=(),
        )


def test_xdg_user_config_is_used_below_environment(
    tmp_path: Path,
    fake_openfoam: Path,
) -> None:
    xdg = tmp_path / "xdg"
    _runtime_toml(
        xdg / "foampilot/runtime.toml",
        root=fake_openfoam,
        ranks=2,
    )
    result = resolve_runtime_config(
        environ={"XDG_CONFIG_HOME": str(xdg)},
        candidate_roots=(),
    )
    assert result.config.max_mpi_ranks == 2
    assert result.provenance.fields["execution.max_mpi_ranks"].source == "user_toml"


def test_discovery_refuses_multiple_valid_foundation_v10_roots(
    tmp_path: Path,
) -> None:
    first = _fake_openfoam(tmp_path / "first")
    second = _fake_openfoam(tmp_path / "second")
    with pytest.raises(RuntimeConfigError) as captured:
        resolve_runtime_config(
            environ={},
            candidate_roots=(first, second),
        )
    assert captured.value.code == "OPENFOAM_DISCOVERY_FAILED"
    assert str(first.resolve()) in captured.value.message
    assert str(second.resolve()) in captured.value.message
```

- [ ] **Step 6: 实现 leafwise 合并与 bubblewrap 解析**

实现固定字段表，不递归接受任意键。来源按低到高覆盖，每次覆盖同时替换该字段的 `RuntimeFieldSource`。`bubblewrap="auto"` 调用 `shutil.which("bwrap")`；显式值必须是绝对 executable 文件路径。`FOAMPILOT_BUBBLEWRAP=auto` 允许解析为 `None`，由 policy 决定是否阻断。

有限候选只包含：调用方注入的 `candidate_roots`、当前环境 `WM_PROJECT_DIR`、`shutil.which("foamVersion")` 的有限父目录，以及 `/opt/OpenFOAM/OpenFOAM-10`、`/usr/lib/openfoam/openfoam10`、`/usr/lib/openfoam/openfoam-10`。`probe_openfoam_root(root)` source 候选的 `etc/bashrc` 并验证 `WM_PROJECT`、`WM_PROJECT_VERSION=10`、`WM_PROJECT_DIR`、`FOAM_APPBIN` 和一个基础 solver；只有验证通过才算有效候选。零个返回 `OPENFOAM_DISCOVERY_FAILED`，多个返回同一 code 并在 message 列出规范路径；不得递归扫描 home 或 `/`。Task 2 的 environment discovery 必须复用该 probe，不能实现第二套 source 规则。

- [ ] **Step 7: 迁移基础 Runtime 测试并导出公共接口**

`runtime/__init__.py` 只导出上述公共模型、错误、`probe_openfoam_root()` 和 resolver。删除 `tests/test_runtime.py` 对本机工厂的断言，保留 log parser 测试，并改为断言 fake root resolution、`sys.executable` 尚未作为 Runtime 配置字段出现。把所有生产调用点中的旧 `auto/bubblewrap/host` 配置迁移到 `sandbox_preferred/sandbox_required/trusted_host`，删除 Runtime authoring 字段 `execution_backend`；`PlanStepResult.execution_backend` 继续只表示历史/实际执行事实，不能反向作为新 Runtime 输入。运行：

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider tests/test_runtime_config.py tests/test_runtime.py
```

Expected: 新配置和基础 log parser 测试全部通过；测试文件不再引用本机工厂。

- [ ] **Step 8: Review checkpoint**

检查 `git diff -- src/foampilot/runtime tests/test_runtime_config.py`，确认没有用户目录、没有工程本地配置读取、provenance 不含环境变量值。

---

### Task 2: Foundation v10 环境验证与运行时保护路径

**Files:**
- Modify: `src/foampilot/environment/models.py:15-55`
- Modify: `src/foampilot/environment/discovery.py:18-214`
- Create: `src/foampilot/runtime/protection.py`
- Modify: `src/foampilot/runtime/__init__.py`
- Modify: `src/foampilot/agent/native_orchestrator.py:717-720, 900-1250, 1747-1905, 2286-2517`
- Modify: `src/foampilot/qualification/data/tasks/*.yaml`
- Modify: `examples/tasks/*.yaml`
- Modify: `examples/qualification/*.yaml`
- Modify: `tests/test_environment_discovery.py`
- Modify: `tests/test_native_qualification_assets.py`
- Modify: `tests/test_repository_boundary.py`

**Interfaces:**
- Consumes: `RuntimeConfig` from Task 1。
- Produces: source 后验证过的 `EnvironmentSnapshot`；`runtime_protected_paths()`；不含个人目录的 package/example TaskSpec。

- [ ] **Step 1: 扩充 fake OpenFOAM tree 并写发行版/root/路径测试**

```python
def _make_fake_openfoam_tree(tmp_path: Path, name: str = "OpenFOAM-10") -> Path:
    root = tmp_path / name
    binary = root / "platforms/fake/bin"
    tutorials = root / "tutorials"
    binary.mkdir(parents=True)
    tutorials.mkdir()
    (root / "etc").mkdir()
    (root / "etc/bashrc").write_text(
        f'export WM_PROJECT="OpenFOAM"\n'
        f'export WM_PROJECT_VERSION="10"\n'
        f'export WM_PROJECT_DIR="{root}"\n'
        f'export FOAM_TUTORIALS="{tutorials}"\n'
        f'export FOAM_APPBIN="{binary}"\n'
        f'export PATH="{binary}:$PATH"\n',
        encoding="utf-8",
    )
    _write_executable(binary / "foamVersion", "#!/bin/sh\nprintf '10\\n'\n")
    _write_executable(binary / "icoFoam", "#!/bin/sh\nexit 0\n")
    return root


def test_discovery_rejects_sourced_root_mismatch(tmp_path: Path) -> None:
    root = _make_fake_openfoam_tree(tmp_path)
    bashrc = root / "etc/bashrc"
    bashrc.write_text(
        bashrc.read_text(encoding="utf-8").replace(
            f'WM_PROJECT_DIR="{root}"', 'WM_PROJECT_DIR="/different"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="OPENFOAM_VERSION_MISMATCH|root"):
        discover_environment(RuntimeConfig(openfoam_root=root), tmp_path / "runs")
```

- [ ] **Step 2: 从 sourced environment 派生 tutorial root**

`EnvironmentSnapshot.tutorial_root` 改为 `Path | None`。`discover_environment()` 必须验证：

```python
if environment.get("WM_PROJECT") not in {"OpenFOAM", "openfoam"}:
    raise RuntimeError("OPENFOAM_VERSION_MISMATCH: Foundation runtime required")
if environment.get("WM_PROJECT_VERSION") != "10":
    raise RuntimeError("OPENFOAM_VERSION_MISMATCH: Foundation v10 required")
if Path(environment["WM_PROJECT_DIR"]).resolve() != config.openfoam_root.resolve():
    raise RuntimeError("OPENFOAM_DISCOVERY_FAILED: sourced root mismatch")
tutorial_root = (
    Path(environment["FOAM_TUTORIALS"]).resolve()
    if environment.get("FOAM_TUTORIALS")
    else None
)
```

命令目录仅接受 OpenFOAM root 内目录，或位于 `config.trusted_readonly_roots` 的显式额外目录；保留 `/usr/bin/gmsh` 作为现有确定性 system helper，不把任意 `FOAM_USER_APPBIN` 自动加入 capability。

- [ ] **Step 3: 定义运行时保护路径合并函数**

在 `runtime/protection.py` 中实现，允许它单向依赖 `EnvironmentSnapshot`；`runtime/models.py` 不得反向导入 environment 包，以免形成循环依赖：

```python
def runtime_protected_paths(
    declared: Sequence[str],
    environment: EnvironmentSnapshot,
    evaluator_roots: Sequence[Path] = (),
) -> tuple[Path, ...]:
    values = [Path(item).resolve() for item in declared]
    if environment.tutorial_root is not None:
        values.append(environment.tutorial_root.resolve())
    values.extend(path.resolve() for path in evaluator_roots)
    return tuple(dict.fromkeys(values))
```

在 `NativeAgent.solve()` 完成 environment discovery 后构造：

```python
active_protected_paths = runtime_protected_paths(
    task.protected_paths,
    environment,
)
execution_task = task.model_copy(
    update={
        "protected_paths": [str(path) for path in active_protected_paths]
    }
)
```

原始 `task` 继续用于 `task.yaml`、task hash、plan reuse 与 summary；`execution_task` 只用于 prompt 泄漏检查、plan normalization/validation、materialize、inspection、status、repair scope 和 repair patch。这样机器路径不改变用户 TaskSpec 身份，但所有生成/repair 边界都受到当前 runtime 保护。

- [ ] **Step 4: 写当前 runtime tutorial 在生成内容中被拒绝的 Agent 测试**

```python
def test_native_agent_adds_discovered_tutorial_to_execution_guards(tmp_path: Path) -> None:
    environment = _environment("blockMesh", "icoFoam").model_copy(
        update={"tutorial_root": tmp_path / "OpenFOAM-10/tutorials"}
    )
    plan = _plan().model_copy(
        update={
            "files": [
                GeneratedCaseFile(
                    path="system/controlDict",
                    content=f'#include "{environment.tutorial_root}/cavity/controlDict"\n',
                )
            ]
        }
    )
    outcome = NativeAgent(
        gateway=RecordingModel([plan]),
        runtime_config=_runtime_config(tmp_path),
        artifact_store=ArtifactStore(tmp_path / "runs"),
        environment_snapshot=environment,
        runner=SequencePlanRunner([]),
    ).solve(_task().model_copy(update={"protected_paths": []}))
    assert outcome.status == "PLAN_INVALID"
```

- [ ] **Step 5: 删除所有可交付 TaskSpec 中的个人 tutorial 路径**

把 `src/foampilot/qualification/data/tasks/*.yaml`、`examples/tasks/*.yaml`、`examples/qualification/*.yaml` 中仅含 `/home/edwin/workplace/OpenFOAM-10/tutorials` 的 `protected_paths` 统一改为 `protected_paths: []`。保留其他真正由任务声明的窄绝对 protected path。

增强 repository boundary：

```python
def test_deliverable_runtime_assets_have_no_personal_paths() -> None:
    roots = (ROOT / "src/foampilot", ROOT / "examples")
    violations = []
    for root in roots:
        for path in root.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="ignore")
                if "/home/edwin" in text or "feal-venv" in text:
                    violations.append(str(path.relative_to(ROOT)))
    assert violations == []
```

- [ ] **Step 6: 迁移环境与 qualification asset 测试**

所有测试 Runtime 用 `tmp_path` fake tree 或显式 `RuntimeConfig(openfoam_root=fake_root)`；不再断言本机路径。qualification task 测试断言 frozen TaskSpec 的 `protected_paths == []`，并另测 `runtime_protected_paths()` 注入当前 `FOAM_TUTORIALS`。

- [ ] **Step 7: 运行环境、TaskSpec、Agent 定向测试**

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider \
  tests/test_environment_discovery.py \
  tests/test_native_qualification_assets.py \
  tests/test_native_agent_state_machine.py \
  tests/test_repository_boundary.py
```

Expected: 全部通过，`rg '/home/edwin|feal-venv' src/foampilot examples` 无输出。

- [ ] **Step 8: Review checkpoint**

逐一确认 `task` 与 `execution_task` 的调用点：机器保护路径不能进入 task hash/agent payload；generation、repair、plan validation 和 materialization 不能绕过 `execution_task`。

---

### Task 3: ExecutionRiskReport 与纯 policy engine

**Files:**
- Create: `src/foampilot/runtime/risk.py`
- Create: `src/foampilot/runtime/policy.py`
- Modify: `src/foampilot/runtime/models.py`
- Modify: `src/foampilot/runtime/__init__.py`
- Create: `tests/test_execution_risk.py`

**Interfaces:**
- Consumes: materialized case、verified OpenFOAM root、trusted readonly roots。
- Produces: `RiskFinding`、`ExecutionRiskReport`、`ExecutionPolicyDecision`、`scan_execution_risk()`、`decide_execution_policy()`。

- [ ] **Step 1: 写 low/high/unknown 风险夹具**

```python
@pytest.mark.parametrize(
    ("relative", "content", "code"),
    [
        ("system/controlDict", "functions { x { type coded; code #{ int x; #}; } }", "CODED_FUNCTION"),
        ("system/controlDict", "#codeStream { code #{ int x; #}; }", "CODE_STREAM"),
        ("0/U", "type codedFixedValue;", "CODED_BOUNDARY"),
        ("system/controlDict", '#include "/tmp/foreign"', "ABSOLUTE_INCLUDE"),
        ("system/controlDict", '#include "../../foreign"', "INCLUDE_ESCAPES_CASE"),
        ("system/controlDict", 'libs ("/tmp/libevil.so");', "PATH_LIBRARY"),
    ],
)
def test_risk_scanner_marks_host_unsafe_constructs(
    tmp_path: Path, relative: str, content: str, code: str
) -> None:
    case = tmp_path / "case"
    target = case / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    report = scan_execution_risk(case, openfoam_root=tmp_path / "OpenFOAM-10")
    assert report.risk_level == "high"
    assert code in {finding.code for finding in report.findings}


def test_unknown_execution_directive_blocks_host(tmp_path: Path) -> None:
    case = _case(tmp_path, "system/controlDict", "#unknownExec foo;")
    report = scan_execution_risk(case, openfoam_root=tmp_path / "OpenFOAM-10")
    assert report.risk_level == "unknown"
```

- [ ] **Step 2: 定义风险和决策模型**

```python
class RiskFinding(StrictModel):
    code: str
    path: str
    line: int
    detail: str


class ExecutionRiskReport(StrictModel):
    schema_version: Literal[1] = 1
    risk_level: Literal["low", "high", "unknown"]
    findings: tuple[RiskFinding, ...] = ()
    scanned_file_sha256: dict[str, str]
    policy_decision: str | None = None


class SandboxProbe(StrictModel):
    schema_version: Literal[1] = 1
    status: Literal["passed", "failed", "not_requested"]
    ok: bool | None
    builder_sha256: str | None = None
    namespace_flags: tuple[str, ...] = ()
    mount_count: int = 0
    protected_path_count: int = 0
    failure_code: Literal[
        "BWRAP_UNAVAILABLE",
        "NAMESPACE_UNAVAILABLE",
        "SANDBOX_SETUP_FAILED",
        "TRUSTED_RUNTIME_ROOT_INVALID",
    ] | None = None
    return_code: int | None
    detail: str


class ExecutionPolicyDecision(StrictModel):
    schema_version: Literal[1] = 1
    requested_isolation: IsolationPolicy
    actual_backend: Literal["bubblewrap", "host"] | None
    allowed: bool
    code: str
    fallback_reason: str | None = None
    unisolated_warning: str | None = None
```

- [ ] **Step 3: 实现注释剥离、哈希和 directive 解析**

`risk.py` 必须：

1. 只遍历 case root 下普通非 symlink 文件，跳过 `.foampilot/`；
2. 对 UTF-8 文本计算 SHA256；二进制文件只忽略内容，不标记 unknown；
3. 去除 C++ 行注释和块注释后逐行匹配 `#include`、`#includeIfPresent`、`#includeEtc`、`#codeStream`、coded 类型、`dynamicCode` 和 `libs`；
4. 相对 include 用声明文件父目录解析，只有仍在 case root 才 low；
5. `#includeEtc` 目标必须能规范化到 verified OpenFOAM root；
6. 未识别且名称包含 `code`、`exec`、`include` 或 `load` 的 directive 记为 unknown；
7. findings 按 `(path, line, code)` 稳定排序；有 high finding 则 high，否则有 unknown 则 unknown，否则 low。

- [ ] **Step 4: 写完整 policy matrix 测试**

```python
@pytest.mark.parametrize(
    ("isolation", "probe_ok", "risk", "opt_in", "backend", "code"),
    [
        ("sandbox_required", True, "high", False, "bubblewrap", "SANDBOX_SELECTED"),
        ("sandbox_required", False, "low", False, None, "SANDBOX_REQUIRED_UNAVAILABLE"),
        ("sandbox_preferred", True, "unknown", False, "bubblewrap", "SANDBOX_SELECTED"),
        ("sandbox_preferred", False, "low", False, "host", "HOST_FALLBACK_SELECTED"),
        ("sandbox_preferred", False, "high", True, None, "HOST_DYNAMIC_CODE_BLOCKED"),
        ("trusted_host", True, "low", False, "host", "TRUSTED_HOST_SELECTED"),
        ("trusted_host", True, "high", False, None, "HOST_DYNAMIC_CODE_BLOCKED"),
        ("trusted_host", True, "high", True, "host", "TRUSTED_HOST_DYNAMIC_CODE_OPT_IN"),
    ],
)
def test_execution_policy_matrix(
    tmp_path: Path,
    isolation: str,
    probe_ok: bool,
    risk: str,
    opt_in: bool,
    backend: str | None,
    code: str,
) -> None:
    config = RuntimeConfig(
        openfoam_root=tmp_path / "OpenFOAM-10",
        isolation=isolation,
        allow_dynamic_code_on_host=opt_in,
    )
    risk_report = ExecutionRiskReport(
        risk_level=risk,
        scanned_file_sha256={},
    )
    probe = SandboxProbe(
        status="passed" if probe_ok else "failed",
        ok=probe_ok,
        builder_sha256="a" * 64,
        namespace_flags=("unshare-net", "unshare-pid", "unshare-ipc", "unshare-uts"),
        mount_count=8,
        protected_path_count=0,
        failure_code=None if probe_ok else "NAMESPACE_UNAVAILABLE",
        return_code=0 if probe_ok else 1,
        detail="ok" if probe_ok else "Operation not permitted",
    )
    decision = decide_execution_policy(config, risk_report, probe)
    assert decision.actual_backend == backend
    assert decision.code == code
    assert decision.allowed is (backend is not None)


@pytest.mark.parametrize(
    "failure_code",
    ["SANDBOX_SETUP_FAILED", "TRUSTED_RUNTIME_ROOT_INVALID"],
)
def test_mount_plan_failures_never_fall_back_to_host(
    tmp_path: Path,
    failure_code: str,
) -> None:
    probe = SandboxProbe(
        status="failed",
        ok=False,
        failure_code=failure_code,
        return_code=None,
        detail="redacted setup failure",
    )
    decision = decide_execution_policy(
        RuntimeConfig(openfoam_root=tmp_path, isolation="sandbox_preferred"),
        ExecutionRiskReport(risk_level="low", scanned_file_sha256={}),
        probe,
    )
    assert decision.allowed is False
    assert decision.actual_backend is None
    assert decision.code == failure_code
```

- [ ] **Step 5: 实现无副作用的 `decide_execution_policy()`**

严格按测试矩阵返回；只有 `probe.status == "passed" and probe.ok is True` 才视为 sandbox 可用，不一致事实按 setup failure 处理；trusted_host 忽略 probe 可用性但保留 `not_requested` 证据。只有 `BWRAP_UNAVAILABLE`/`NAMESPACE_UNAVAILABLE` 才属于 preferred 可能降级的 mechanism failure；`SANDBOX_SETUP_FAILED` 和 `TRUSTED_RUNTIME_ROOT_INVALID` 即使 case low-risk 也必须阻断，不能通过 host 绕过保护路径。`sandbox_preferred` 即使 opt-in 为 true，也不在 bubblewrap 不可用时运行 high/unknown；只有显式 `trusted_host` 加 opt-in 可运行。所有 host 允许结果必须设置 `unisolated_warning`。

- [ ] **Step 6: 运行风险与 policy 测试**

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider tests/test_execution_risk.py
```

Expected: 全部通过；同一 case 重复扫描 JSON 完全稳定。

- [ ] **Step 7: Review checkpoint**

确认 scanner 被描述为 host 降级 guard，而非恶意代码证明；确认 `sandbox_preferred + high/unknown + probe fail` 永不选择 host。

---

### Task 4: 动态 bubblewrap mount plan 与等价 preflight probe

**Files:**
- Modify: `src/foampilot/runtime/sandbox.py:1-136`
- Modify: `src/foampilot/runtime/preflight.py:1-124`
- Modify: `src/foampilot/runtime/models.py`
- Create: `tests/test_sandbox.py`
- Modify: `tests/test_runtime.py`

**Interfaces:**
- Consumes: `RuntimeConfig`、`EnvironmentSnapshot`、case、protected paths、resource limits、typed argv。
- Produces: `SandboxLaunch`、`RuntimePreflightReport`、`build_sandbox_argv()`、`probe_sandbox()`、`run_preflight()`。

- [ ] **Step 1: 写无个人路径、动态父目录和遮蔽顺序测试**

```python
def test_mount_plan_is_dynamic_and_hides_tutorials(tmp_path: Path) -> None:
    root = tmp_path / "opt/vendor/OpenFOAM-10"
    bwrap = tmp_path / "bin/bwrap"
    _write_executable(bwrap, "#!/bin/sh\nexit 0\n")
    tutorials = root / "tutorials"
    case = tmp_path / "runs/run-1/attempt-01/case"
    tutorials.mkdir(parents=True)
    case.mkdir(parents=True)
    environment = _environment(root=root, tutorial_root=tutorials)
    launch = build_sandbox_argv(
        config=RuntimeConfig(openfoam_root=root, bubblewrap=bwrap),
        environment=environment,
        case_dir=case,
        protected_paths=(tutorials,),
        memory_mib=1024,
        cpu_seconds=30,
        typed_argv=("/usr/bin/true",),
    )
    assert all("/home/edwin" not in item for item in launch.argv)
    bind_index = launch.argv.index(str(root.resolve()))
    hide_index = launch.argv.index(str(tutorials.resolve()))
    assert hide_index > bind_index
    assert launch.argv[-1] == "/usr/bin/true"
```

- [ ] **Step 2: 定义 `SandboxLaunch` 与 mount validation**

```python
class SandboxMount(StrictModel):
    kind: Literal["ro_bind", "bind", "tmpfs", "dir", "symlink"]
    source: Path | str | None = None
    target: Path | str


class SandboxLaunch(StrictModel):
    schema_version: Literal[1] = 1
    argv: tuple[str, ...]
    mounts: tuple[SandboxMount, ...]
    hidden_paths: tuple[Path, ...]
```

校验规则：OpenFOAM root、case 和额外 roots 必须存在；case 是唯一 writable bind；额外 root 不得为 `/` 或 home；额外 root 与 protected path 相交即 `TRUSTED_RUNTIME_ROOT_INVALID`；位于较宽 bind 内且存在的 protected 目录必须在 bind 后用 `--tmpfs` 覆盖；存在的 protected 普通文件直接拒绝 mount plan，避免伪造遮蔽。

- [ ] **Step 3: 用一个函数生成完整 sandbox argv**

`build_sandbox_argv()` 生成：`--die-with-parent --new-session --unshare-net --unshare-pid --unshare-ipc --unshare-uts --clearenv`；只读 `/usr`、运行所需的 `/etc` 和 OpenFOAM/trusted roots；最小 `/proc`、`/dev`；tmpfs `/tmp`；隔离 `/home/agent`；bind case 到 `/case`；设置 `HOME/USER/LOGNAME/TMPDIR/PATH/LANG/LC_ALL`；最后执行 `/usr/bin/prlimit --cpu=<cpu_seconds> --as=<memory_bytes> -- /bin/bash --noprofile --norc -c <fixed_source_and_exec_template> foampilot <bashrc> <typed_argv>`。在 usr-merged 系统中按主机实际 symlink 关系重建 `/bin`、`/sbin`、`/lib`、`/lib64`，不得把用户 home 当成系统 mount。必要父目录由 root 的 absolute parts 动态追加 `--dir`，不得出现用户名常量。

- [ ] **Step 4: 写 preflight 与 Runner builder 等价测试**

```python
def test_probe_uses_same_builder_as_real_launch(monkeypatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(
        "foampilot.runtime.sandbox.build_sandbox_argv",
        lambda **kwargs: calls.append(kwargs) or _launch(kwargs["typed_argv"]),
    )
    probe = probe_sandbox(
        config=_config(tmp_path),
        environment=_environment(tmp_path),
        case_dir=_case(tmp_path),
        protected_paths=(),
        memory_mib=256,
        cpu_seconds=5,
        executor=_successful_executor,
    )
    assert probe.ok
    assert calls[0]["typed_argv"] == ("/usr/bin/true",)
    assert probe.builder_sha256 == _safe_builder_sha256(_launch(("/usr/bin/true",)))
    assert str(tmp_path) not in probe.model_dump_json()
```

`probe_sandbox()` 仅把对路径 token 化后的 builder 结构哈希、namespace flags、mount/protected 数量、return code 与脱敏 detail 写入 `SandboxProbe`；完整 bubblewrap argv 只存在于进程内 `SandboxLaunch`，不得把 OpenFOAM、evaluator、trusted root 或 case 的绝对路径写入公开 probe artifact。`_safe_builder_sha256()` 必须让相同结构、不同用户路径得到相同摘要。

- [ ] **Step 5: 重写 preflight 为结构化报告**

```python
class RuntimePreflightReport(StrictModel):
    schema_version: Literal[1] = 1
    ok: bool
    python_executable: Path
    checks: tuple[RuntimeCheck, ...]
    environment: EnvironmentSnapshot | None
    sandbox_probe: SandboxProbe


def run_preflight(
    config: RuntimeConfig,
    *,
    workspace_root: str | Path,
) -> RuntimePreflightReport
```

该模型定义在 `runtime/preflight.py`，允许它单向导入 `EnvironmentSnapshot`；不得移入 `runtime/models.py`。函数记录 `Path(sys.executable).resolve()`；调用 `discover_environment()`；用临时空 case 和 `probe_sandbox()`；required 下 probe 失败使 `ok=false`，preferred 下只记录非阻断并明确 low-risk 条件，trusted_host 不选择 sandbox。删除旧 `_bubblewrap_launch_check()` 和弱化的 `probe_bubblewrap()`。

- [ ] **Step 6: 运行 sandbox/preflight 测试**

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider tests/test_sandbox.py tests/test_runtime.py
```

Expected: mount/probe 测试全部通过；preflight 中不再存在 `python_executable` 路径存在性检查。

- [ ] **Step 7: Review checkpoint**

比较 probe 与真实 launch 的 namespace、mount、env、bashrc、prlimit 字段；除 typed argv 和 case path 外必须一致。

---

### Task 5: PlanRunner 首命令前冻结 backend

**Files:**
- Modify: `src/foampilot/runtime/plan_runner.py:47-346`
- Modify: `src/foampilot/runtime/models.py:47-78`
- Modify: `tests/test_plan_runner.py`

**Interfaces:**
- Consumes: Task 3 risk report/policy、Task 4 sandbox builder/probe。
- Produces: `PlanRunner.run(*, case_dir: str | Path, commands: Sequence[NativeCommand], budget: ResourceBudget, risk_report: ExecutionRiskReport, protected_paths: Sequence[Path]) -> PlanRunResult`；返回值带 `sandbox_probe` 和 `execution_policy`。

- [ ] **Step 1: 把旧 auto fallback 测试改为低风险、首命令前 probe 失败测试**

```python
def _risk(level: str) -> ExecutionRiskReport:
    return ExecutionRiskReport(
        risk_level=level,
        scanned_file_sha256={"system/controlDict": "a" * 64},
    )


def _probe(ok: bool, detail: str = "ok") -> SandboxProbe:
    return SandboxProbe(
        status="passed" if ok else "failed",
        ok=ok,
        builder_sha256="a" * 64,
        namespace_flags=("unshare-net", "unshare-pid", "unshare-ipc", "unshare-uts"),
        mount_count=8,
        protected_path_count=0,
        failure_code=None if ok else "NAMESPACE_UNAVAILABLE",
        return_code=0 if ok else 1,
        detail=detail,
    )


def test_preferred_falls_back_before_first_step_only_for_low_risk(
    tmp_path: Path,
) -> None:
    executor = RecordingExecutor(return_codes={"solve": 0})
    case = tmp_path / "case"
    case.mkdir()
    runner = _runner(
        tmp_path,
        executor,
        config=_config(tmp_path).model_copy(update={"isolation": "sandbox_preferred"}),
        sandbox_probe=lambda **_: _probe(False, "Operation not permitted"),
    )
    result = runner.run(
        case_dir=case,
        commands=[_command("solve")],
        budget=_budget(),
        risk_report=_risk("low"),
        protected_paths=(),
    )
    assert result.execution_policy.actual_backend == "host"
    assert result.steps[0].execution_backend == "host"


def test_preferred_blocks_high_risk_when_probe_fails(
    tmp_path: Path,
) -> None:
    executor = RecordingExecutor(return_codes={"solve": 0})
    case = tmp_path / "case"
    case.mkdir()
    runner = _runner(
        tmp_path,
        executor,
        config=_config(tmp_path).model_copy(
            update={"isolation": "sandbox_preferred"}
        ),
        sandbox_probe=lambda **_: _probe(False, "Operation not permitted"),
    )
    with pytest.raises(RuntimeExecutionError) as captured:
        runner.run(
            case_dir=case,
            commands=[_command("solve")],
            budget=_budget(),
            risk_report=_risk("high"),
            protected_paths=(),
        )
    assert captured.value.code == "HOST_DYNAMIC_CODE_BLOCKED"
    assert executor.invocations == []
```

- [ ] **Step 2: 注入 probe seam 并删除 `_execution_backend()`**

`PlanRunner.__init__` 增加：

```python
sandbox_probe: Callable[..., SandboxProbe] = probe_sandbox
```

`run()` 在创建 step log 前完成 actual-case probe（trusted_host 生成 `status="not_requested"`、`ok=None` 且不伪造 builder hash 的 probe 事实），调用 `decide_execution_policy()`，不允许时抛出：

```python
class RuntimeExecutionError(RuntimeError):
    def __init__(
        self,
        decision: ExecutionPolicyDecision,
        probe: SandboxProbe,
    ) -> None:
        super().__init__(decision.code)
        self.code = decision.code
        self.decision = decision
        self.probe = probe
```

- [ ] **Step 3: 让 sandbox 与 host 共享 typed command 校验**

保留 `_validate_commands()`、`_typed_argv()`、`shell=False`、MPI launcher ownership 和 wall/memory limits。sandbox 调 `build_sandbox_argv()`；host 继续固定 bash template、隔离 case-local HOME/TMPDIR 和 `prlimit`，并记录未隔离 warning。所有 step 的 backend/fallback reason 必须等于冻结 decision，循环内不得重新 probe 或改写。

`PlanRunResult` 增加 `execution_error_code: Literal["SANDBOX_SETUP_FAILED"] | None = None`。已冻结为 bubblewrap 后，如果 step stderr 以 `bwrap:` 或 `prlimit:` 开头，Runner 保留该 step/log，设置 `failed_step_id` 和 `execution_error_code="SANDBOX_SETUP_FAILED"`，停止后续 step，绝不转 host。

- [ ] **Step 4: 测试运行中 sandbox 故障不切 host**

```python
def test_sandbox_step_failure_never_switches_backend(tmp_path: Path) -> None:
    executor = SandboxSetupFailingExecutor(
        stderr="bwrap: loopback: Operation not permitted\n"
    )
    case = tmp_path / "case"
    case.mkdir()
    runner = _runner(
        tmp_path,
        executor,
        config=_config(tmp_path).model_copy(
            update={"isolation": "sandbox_required"}
        ),
        sandbox_probe=lambda **_: _probe(True),
    )
    result = runner.run(
        case_dir=case,
        commands=[_command("mesh"), _command("solve")],
        budget=_budget(),
        risk_report=_risk("low"),
        protected_paths=(),
    )
    assert [step.execution_backend for step in result.steps] == ["bubblewrap"]
    assert result.failed_step_id == "mesh"
    assert result.execution_error_code == "SANDBOX_SETUP_FAILED"
    assert len(executor.invocations) == 1
```

- [ ] **Step 5: 运行 Runner 测试**

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider tests/test_plan_runner.py
```

Expected: typed command、MPI、checkMesh、timeout 和新 policy 测试全部通过。

- [ ] **Step 6: Review checkpoint**

确认 backend decision 只发生一次；probe/decision 在任何 OpenFOAM executable 前完成；high/unknown host block 不创建 step result 或 solver log。

---

### Task 6: NativeAgent 每 attempt 风险重扫与不可变证据

**Files:**
- Modify: `src/foampilot/agent/native_orchestrator.py:528-720, 818-975, 1800-2100, 2250-2520`
- Modify: `src/foampilot/artifacts/models.py:22-86`
- Modify: `tests/test_native_agent_state_machine.py`
- Modify: `tests/test_artifact_store.py`
- Modify: `tests/test_continuation.py`

**Interfaces:**
- Consumes: `RuntimeResolution`、preflight、risk scanner、policy-aware Runner。
- Produces: run-root Runtime artifacts、attempt risk/probe/policy artifacts、稳定 environment failure。

- [ ] **Step 1: 写 artifact 完整性和 repair 重扫测试**

```python
def test_native_agent_freezes_runtime_and_execution_evidence(tmp_path: Path) -> None:
    outcome = _successful_agent(tmp_path).solve(_task())
    assert json.loads((outcome.run_dir / "runtime-config.json").read_text())["isolation"]
    assert (outcome.run_dir / "runtime-config-provenance.json").is_file()
    assert (outcome.run_dir / "sandbox-probe.json").is_file()
    assert (outcome.run_dir / "execution-policy.json").is_file()
    attempt = outcome.run_dir / "attempt-01"
    assert (attempt / "execution-risk-report.json").is_file()
    assert (attempt / "sandbox-probe.json").is_file()
    assert (attempt / "execution-policy.json").is_file()
    assert ArtifactStore(outcome.run_dir.parent).verify(outcome.run_dir) == []


def test_repair_attempt_recomputes_execution_risk(tmp_path: Path) -> None:
    outcome = _agent_with_low_then_coded_repair(tmp_path).solve(_task())
    first = _json(outcome.run_dir / "attempt-01/execution-risk-report.json")
    second = _json(outcome.run_dir / "attempt-02/execution-risk-report.json")
    assert first["risk_level"] == "low"
    assert second["risk_level"] == "high"
    assert second["scanned_file_sha256"] != first["scanned_file_sha256"]
```

- [ ] **Step 2: 给 NativeAgent 注入 provenance 并在 run 创建后立即写配置证据**

`NativeAgent.__init__` 增加可选 `runtime_provenance: RuntimeConfigProvenance | None` 和 `protected_runtime_roots: Sequence[Path] = ()`；前者未提供时生成每字段 `python_api` provenance，后者只允许可信调用方增加运行时不可见路径，不能由 TaskSpec、plan 或模型响应设置。创建 run 后、任何模型调用前写：

```python
_write_json(run_dir / "runtime-config.json", self.runtime_config)
_write_json(
    run_dir / "runtime-config-provenance.json",
    self.runtime_provenance,
)
_write_json(
    run_dir / "execution-policy.json",
    {
        "schema_version": 1,
        "requested_isolation": self.runtime_config.isolation,
        "actual_backend": None,
        "allowed": False,
        "code": "POLICY_PENDING",
        "fallback_reason": None,
        "unisolated_warning": None,
    },
)
```

有效配置 artifact 不包含环境变量秘密值；`python_executable` 只在 preflight artifact 的诊断字段出现。

- [ ] **Step 3: 在 environment 阶段调用完整 preflight**

真实 environment 未注入时调用 `run_preflight(config, workspace_root=run_dir)`，写 `preflight.json` 与 run-root `sandbox-probe.json`，复用 report.environment。测试注入 snapshot 时构造明确的 synthetic check/probe，不能误报真实 sandbox gate。required 下 preflight 失败返回 `BLOCKED_ENVIRONMENT`，primary failure code 使用 `SANDBOX_REQUIRED_UNAVAILABLE` 或实际 Runtime code。

- [ ] **Step 4: 在每次 materialize/repair 后扫描并传给 Runner**

在 `static-inspection.json` 写完、执行 cache/reuse 或 OpenFOAM command 前：

```python
risk = scan_execution_risk(
    case_root,
    openfoam_root=self.runtime_config.openfoam_root,
    trusted_readonly_roots=self.runtime_config.trusted_readonly_roots,
)
_write_json(attempt_root / "execution-risk-report.json", risk)
run_result = runner.run(
    case_dir=case_root,
    commands=commands_to_execute,
    budget=task.resource_budget,
    risk_report=risk,
    protected_paths=runtime_protected_paths(
        execution_task.protected_paths,
        environment,
        self.protected_runtime_roots,
    ),
)
_write_json(attempt_root / "sandbox-probe.json", run_result.sandbox_probe)
_write_json(attempt_root / "execution-policy.json", run_result.execution_policy)
_write_json(run_dir / "sandbox-probe.json", run_result.sandbox_probe)
_write_json(run_dir / "execution-policy.json", run_result.execution_policy)
```

用 decision code 回填 `risk.policy_decision` 后再最终写 risk artifact。repair 每次 materialize 都走同一代码块，不能复制 parent risk。run-root 文件保存最后一次实际 probe/decision；attempt 文件保留每次决策，初始 preflight probe 仍完整保存在 `preflight.json`。

- [ ] **Step 5: 把 policy block 映射为 environment failure，不进入 repair**

捕获执行前的 `RuntimeExecutionError` 时从 `error.probe`/`error.decision` 写 attempt 与 root probe/policy；检测返回结果的 `execution_error_code` 时从 `run_result` 写相同证据。随后创建 `AttemptSummary(status="BLOCKED_ENVIRONMENT")` 和 `FailureRecord(domain=ENVIRONMENT, code=error.code 或 run_result.execution_error_code, message="执行隔离环境不可用。", recovery="修复 bubblewrap/namespace 后重试；sandbox_preferred 只会对 low-risk case 在首命令前降级。")` 并 `_finish()`。不得调用模型 repair gateway；用户可在修复运行环境后通过规范 resume 边界重试。扩充 `AttemptStatus`/summary 允许稳定 Runtime codes 通过 primary failure 表达，但顶层兼容状态仍为 `BLOCKED_ENVIRONMENT`。

- [ ] **Step 6: 让 manifest 覆盖所有新证据**

无需修改 `ArtifactStore.finalize()` 的扫描算法；新增测试断言 root/attempt 新 JSON 都出现在 `artifact-manifest.json.files`，并在改写任一 policy artifact 后得到 hash mismatch。

- [ ] **Step 7: 运行 Agent、continuation 与 artifact 测试**

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider \
  tests/test_native_agent_state_machine.py \
  tests/test_continuation.py \
  tests/test_artifact_store.py
```

Expected: 成功、repair、environment block、continuation 和 manifest 全部通过。

- [ ] **Step 8: Review checkpoint**

确认风险扫描位于每个 attempt 的 materialize 之后、第一条 command 之前；确认 cache/reuse 不绕过扫描；确认 policy failure 不被分类成 solver/case/model failure。

---

### Task 7: CLI、qualification 与 Desktop 共用 resolver

**Files:**
- Modify: `src/foampilot/cli/main.py:122-345, 488-525, 639-820, 848-872, 1055-1083`
- Modify: `src/foampilot/qualification/runner.py:124-233, 256-377`
- Modify: `src/foampilot/qualification/validators.py:1-24, 186-214, 427-456, 651-656`
- Modify: `src/foampilot/qualification/profiles.py:12-28`
- Modify: `src/foampilot/desktop/application.py:15-40`
- Modify: `src/foampilot/desktop/viewmodels.py:65-75`
- Modify: `src/foampilot/desktop/repository.py:202-324`
- Modify: `src/foampilot/desktop/main_window.py:88-180, 324-355, 508-539, 679-815, 860-923`
- Modify: `tests/test_native_agent_cli.py`
- Modify: `tests/test_qualification_cli.py`
- Modify: `tests/test_qualification_gateway.py`
- Modify: `tests/test_desktop_cli.py`
- Modify: `tests/test_desktop_repository.py`
- Modify: `tests/test_desktop_main_window.py`

**Interfaces:**
- Consumes: Task 1 resolver、Task 6 evidence。
- Produces: 一组共享 Runtime CLI flags；qualification required policy；Desktop 参数转发与只读安全状态。

- [ ] **Step 1: 写共享 CLI 参数与 precedence 测试**

```python
@pytest.mark.parametrize(
    "argv",
    [
        ["preflight"],
        ["plan", "task.yaml", "--output", "plan.json"],
        ["solve", "task.yaml", "--run-root", "runs"],
        ["resume", "parent", "--run-root", "runs"],
        ["inspect", "task.yaml", "plan.json", "case"],
        ["qualify", "suite", "--suite-file", "suite.yaml", "--run-root", "runs"],
        ["desktop"],
    ],
)
def test_runtime_options_exist_on_runtime_commands(argv: list[str]) -> None:
    arguments = build_parser().parse_args(
        [*argv, "--openfoam-root", "/opt/OpenFOAM/OpenFOAM-10",
         "--execution-isolation", "sandbox_required",
         "--bubblewrap", "/usr/bin/bwrap", "--max-mpi-ranks", "3",
         "--trusted-readonly-root", "/opt/foam-solvers"]
    )
    assert arguments.openfoam_root == Path("/opt/OpenFOAM/OpenFOAM-10")
    assert arguments.execution_isolation == "sandbox_required"
    assert arguments.max_mpi_ranks == 3
    assert arguments.trusted_readonly_root == [Path("/opt/foam-solvers")]
```

- [ ] **Step 2: 增加 `_add_runtime_options()` 和 `_resolve_runtime()`**

所有 flag 默认 `None`，确保未显式给出的 CLI 不覆盖 TOML/env：

```python
def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime-config", type=Path)
    parser.add_argument("--openfoam-root", type=Path)
    parser.add_argument(
        "--execution-isolation",
        choices=("sandbox_required", "sandbox_preferred", "trusted_host"),
    )
    parser.add_argument("--bubblewrap")
    parser.add_argument("--max-mpi-ranks", type=int)
    parser.add_argument("--allow-dynamic-code-on-host", action="store_true", default=None)
    parser.add_argument(
        "--trusted-readonly-root",
        action="append",
        type=Path,
        default=None,
    )


def _resolve_runtime(
    arguments: argparse.Namespace,
    *,
    default_isolation: IsolationPolicy = "sandbox_preferred",
) -> RuntimeResolution:
    return resolve_runtime_config(
        explicit_config=arguments.runtime_config,
        cli_overrides=RuntimeOverrides(
            openfoam_root=arguments.openfoam_root,
            isolation=arguments.execution_isolation,
            bubblewrap=arguments.bubblewrap,
            max_mpi_ranks=arguments.max_mpi_ranks,
            allow_dynamic_code_on_host=arguments.allow_dynamic_code_on_host,
            trusted_readonly_roots=(
                tuple(arguments.trusted_readonly_root)
                if arguments.trusted_readonly_root is not None
                else None
            ),
        ),
        default_isolation=default_isolation,
    )
```

`plan/solve/resume/inspect/preflight/qualify` 只调用该 helper。普通命令使用默认 `sandbox_preferred`；`qualify` 调用 `_resolve_runtime(arguments, default_isolation="sandbox_required")`。因此完全未声明 isolation 时 qualification 安全默认 required，而 CLI/env/TOML 显式声明其他策略仍保留来源并由冲突门禁拒绝。`wall-heat-flux` 的 `--openfoam-root` 默认改为 `None`，未提供时也通过 resolver 获取。TaskBuilder 不再注入固定 tutorial path；运行时保护由 `NativeAgent` 负责。

- [ ] **Step 3: 输出稳定 Runtime 错误 payload**

CLI 捕获 `RuntimeConfigError` 和 `RuntimeExecutionError`，返回 exit code 3：

```json
{
  "status": "BLOCKED_ENVIRONMENT",
  "code": "OPENFOAM_DISCOVERY_FAILED",
  "message": "未找到唯一的 Foundation OpenFOAM v10 运行时。",
  "recovery": "通过 --openfoam-root、用户 TOML 或 FOAMPILOT_OPENFOAM_ROOT 指定安装目录。"
}
```

preflight JSON 另外包含 config、provenance、checks、sandbox_probe；不得退化为 `INVALID_INPUT`。

- [ ] **Step 4: qualification 显式接收 RuntimeResolution**

`run_qualification_suite()`、`run_official_six()` 和 `_run_one()` 都增加 keyword-only 参数 `runtime_resolution: RuntimeResolution` 并传递同一对象。入口先检查 `runtime_resolution.config.isolation == "sandbox_required"`，否则抛 `RuntimeConfigError("RUNTIME_POLICY_CONFLICT", "Qualification 必须使用 sandbox_required。", "修改 Runtime isolation 后重新运行 qualification。")`，不启动 thread pool。

增加两个 CLI/runner 测试：不提供任何 isolation 来源时 `qualify` 得到 provenance=`default` 的 `sandbox_required`；用户 TOML 显式写 `trusted_host` 时得到 `RUNTIME_POLICY_CONFLICT`，并断言 worker/thread pool 没有启动。

入口还必须先运行/复用 Runtime preflight；若 `environment.tutorial_root is None`，以 `OPENFOAM_DISCOVERY_FAILED` 和“当前 Foundation v10 安装缺少 FOAM_TUTORIALS；qualification 无法执行官方算例”的恢复信息阻断，不启动 thread pool。普通 `solve` 不依赖 tutorial root，不能受此门禁影响。

`_run_one()` 计算当前安装中的 `qualification/data` root，并作为 `protected_runtime_roots` 传给 `NativeAgent`。因此即使 wheel 安装在已只读挂载的 `/usr` 下，sandbox builder 也必须遮蔽 tasks、validation、references 和 suites 的共同父目录。若用户 trusted root 与该 evaluator root 相交，具体 qualification run 返回 `TRUSTED_RUNTIME_ROOT_INVALID`。

`evaluate_case_copy()` 和 `extract_observations()` 显式接收 `openfoam_root: Path`。`OpenFOAMCaseData.__init__()` 增加 keyword-only 参数 `openfoam_root: Path` 并保存该事实；`_buoyant`、`_cht_region_heat_flow` 使用 `data.openfoam_root`，删除 validators 中的本机工厂调用。

- [ ] **Step 5: Desktop 转发显式 Runtime flags**

`foampilot desktop` 把用户在 desktop 命令上显式提供的 Runtime 参数规范化为 `tuple[str, ...]`，传给 `launch(run_dir, runtime_cli_args)` 和 `FoamPilotMainWindow(runtime_cli_args=runtime_cli_args)`。窗口只给声明了共享 Runtime options 的 preflight 与 solve 子命令在 `--json` 前追加同一 tuple；`task draft` 不接收也不需要 Runtime 参数。GUI 不重新实现 resolver，也不自动添加 `--allow-dynamic-code-on-host`。增加 QProcess argv 测试，分别断言 preflight/solve 获得参数、task draft 未获得参数。

- [ ] **Step 6: Desktop 从 artifact 投影安全状态**

在 `RunSnapshot` 增加：

```python
runtime_config: dict[str, object] | None = None
runtime_provenance: dict[str, object] | None = None
execution_risk: dict[str, object] | None = None
execution_policy: dict[str, object] | None = None
sandbox_probe: dict[str, object] | None = None
```

`RunRepository` 只读取当前 run 内注册/可见的规范 JSON；active run 未 finalized 时允许直接普通文件但仍拒绝 symlink/越界。选择最高 attempt 的 risk/policy/probe；解析失败加入 warnings。右侧 dock 增加 Config source、OpenFOAM root/version、Requested isolation、Actual backend、Risk、Sandbox probe、Fallback warning 七组 label；未隔离 host 使用明确中文警告，不能显示成与 sandbox 等价的绿色成功。

- [ ] **Step 7: 写 Desktop artifact 投影测试**

```python
def test_desktop_projects_runtime_security_artifacts(tmp_path: Path) -> None:
    run_dir = _runtime_run(
        tmp_path,
        isolation="sandbox_preferred",
        actual_backend="host",
        risk="low",
        fallback="Operation not permitted",
    )
    snapshot = RunRepository().open(run_dir)
    assert snapshot.runtime_config["isolation"] == "sandbox_preferred"
    assert snapshot.execution_policy["actual_backend"] == "host"
    assert snapshot.execution_risk["risk_level"] == "low"

    window.open_run(run_dir)
    assert "sandbox_preferred" in window.isolation_label.text()
    assert "host" in window.actual_backend_label.text()
    assert "未隔离" in window.fallback_warning_label.text()
```

- [ ] **Step 8: 运行 CLI、qualification、Desktop 测试**

```bash
PYTHONPATH=src QT_QPA_PLATFORM=offscreen /home/edwin/feal-venv-py312/bin/python -B -m pytest -q -p no:cacheprovider \
  tests/test_native_agent_cli.py \
  tests/test_qualification_cli.py \
  tests/test_qualification_gateway.py \
  tests/test_desktop_cli.py \
  tests/test_desktop_repository.py \
  tests/test_desktop_main_window.py
```

Expected: 全部通过；核心 CLI import 测试不加载 PySide6。

- [ ] **Step 9: Review checkpoint**

从 parser 到 Desktop QProcess、qualification worker 和 `NativeAgent` 追踪一份显式 `--runtime-config`，确认没有命令重新创建默认 Runtime，也没有 Desktop 工程文件降低 isolation。

---

### Task 8: 文档、全量回归、wheel 与真实 OpenFOAM gates

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/architecture.md`
- Modify: `docs/system-overview.md`
- Modify: `docs/independent-agent-quickstart.md`
- Modify: `docs/desktop-ide.md`
- Modify: `tests/test_repository_docs.py`
- Modify: `tests/test_real_native_vertical_slice.py`
- Modify: `tests/test_real_performance_gate.py`
- Modify: `tests/test_real_continuation_gate.py`
- Modify: `tests/test_real_repair_command_gate.py`
- Modify: `tests/test_real_taskbuilder_gate.py`
- Create during verification only: `/tmp/foampilot-p0a-*`

**Interfaces:**
- Consumes: Tasks 1-7 完成的统一 Runtime path。
- Produces: 可复制配置说明、完整 deterministic evidence、wheel smoke、host/sandbox 真实 gate 证据。

- [ ] **Step 1: 写文档合同测试**

```python
def test_repository_documents_portable_runtime_and_isolation() -> None:
    combined = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "README.md", "AGENTS.md", "docs/architecture.md",
            "docs/system-overview.md", "docs/independent-agent-quickstart.md",
            "docs/desktop-ide.md",
        )
    )
    for token in (
        "FOAMPILOT_OPENFOAM_ROOT", "sandbox_required",
        "sandbox_preferred", "trusted_host", "runtime-config.json",
        "execution-risk-report.json", "execution-policy.json",
    ):
        assert token in combined
    assert "audited host 与 bubblewrap 不具有相同安全性" in combined
```

- [ ] **Step 2: 更新用户配置和故障恢复文档**

文档给出冻结 TOML、XDG 路径、所有环境变量、所有 CLI flags、三档策略矩阵和如下可复制命令：

```bash
foampilot preflight \
  --openfoam-root /opt/OpenFOAM/OpenFOAM-10 \
  --execution-isolation sandbox_preferred \
  --json

foampilot solve task.yaml \
  --run-root runs \
  --runtime-config ~/.config/foampilot/runtime.toml \
  --backend auto \
  --json
```

说明 `HOST_DYNAMIC_CODE_BLOCKED`、`SANDBOX_REQUIRED_UNAVAILABLE`、`OPENFOAM_DISCOVERY_FAILED` 的恢复方式；说明 `trusted_host` 无 network/filesystem namespace；说明 qualification 不允许 host；说明 Desktop 只展示公开规范证据。

- [ ] **Step 3: 迁移真实 gate 到显式 Runtime**

真实测试不再调用 `local_foundation_v10()`。测试从 `FOAMPILOT_OPENFOAM_ROOT` 获取 root；缺少时使用 `pytest.skip`，CI/发布 gate 则通过命令显式设置并要求执行。bubblewrap 通过 `shutil.which("bwrap")`；Python 使用 `sys.executable`。

- [ ] **Step 4: 运行静态扫描与完整 deterministic suite**

```bash
rg -n '/home/edwin|feal-venv|local_foundation_v10|python_executable' src/foampilot examples
git diff --check
PYTHONPATH=src QT_QPA_PLATFORM=offscreen /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests
```

Expected: `rg` 无输出，`git diff --check` 无输出，完整 suite 全部通过或只有明确标记且环境变量未提供的真实 gate skip。

- [ ] **Step 5: 构建 wheel/sdist 并做源码树隔离 smoke**

```bash
python3 -m build --wheel --sdist --no-isolation --outdir /tmp/foampilot-p0a-dist
python3 -m venv /tmp/foampilot-p0a-venv
/tmp/foampilot-p0a-venv/bin/pip install /tmp/foampilot-p0a-dist/foampilot-*.whl
cd /tmp
/tmp/foampilot-p0a-venv/bin/foampilot --help
/tmp/foampilot-p0a-venv/bin/foampilot preflight \
  --openfoam-root /home/edwin/workplace/OpenFOAM-10 \
  --execution-isolation trusted_host \
  --json
```

Expected: import path 位于临时 venv；preflight 记录实际 Python、Foundation v10、config provenance 和 trusted-host policy；不依赖仓库 cwd。该 `/home/edwin` 只出现在当前机器的验证命令，不进入产品文件或 wheel。

- [ ] **Step 6: 真实 trusted-host 最小非 tutorial gate**

使用冻结公开 plan 和空 case 路线，不读取 tutorial：

```bash
FOAMPILOT_OPENFOAM_ROOT=/home/edwin/workplace/OpenFOAM-10 \
FOAMPILOT_EXECUTION_ISOLATION=trusted_host \
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests/test_real_native_vertical_slice.py
```

Expected: `blockMesh -> checkMesh -> icoFoam` 完成，`PUBLIC_VALIDATION_PASS`，artifact manifest 无问题，actual backend 为 host，并有未隔离警告。

- [ ] **Step 7: 真实 sandbox-required 同 case gate**

```bash
FOAMPILOT_OPENFOAM_ROOT=/home/edwin/workplace/OpenFOAM-10 \
FOAMPILOT_EXECUTION_ISOLATION=sandbox_required \
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests/test_real_native_vertical_slice.py
```

Expected: 完整 launch probe 和全部 step 使用 bubblewrap；另用同一个 `build_sandbox_argv()` 执行 `/usr/bin/test ! -e <resolved FOAM_TUTORIALS>`，证明 tutorial root 在 namespace 内不可见；`PUBLIC_VALIDATION_PASS`。若主机内核确实禁止 namespace，该 gate 是真实阻断，不得以 host 结果替代。

- [ ] **Step 8: 真实 fallback 风险注入 gate**

用测试注入不可用 bwrap 路径：low-risk fixture 在 `sandbox_preferred` 下选择 host；包含 `#codeStream` 的 fixture 在任何 OpenFOAM command 前返回 `HOST_DYNAMIC_CODE_BLOCKED`。检查两者 manifest 和 execution artifacts。

- [ ] **Step 9: Desktop offscreen 与人工可用性 gate**

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests/test_desktop_cli.py tests/test_desktop_main_window.py
```

随后在图形会话运行：

```bash
FOAMPILOT_OPENFOAM_ROOT=/home/edwin/workplace/OpenFOAM-10 \
/home/edwin/feal-venv-py312/bin/foampilot desktop
```

人工确认：环境检查显示配置来源与 probe；自然语言/TaskSpec 求解仍调用规范 CLI；运行中残差更新；完成后能看到 requested isolation、actual backend、risk、fallback warning 和 manifest 状态。

- [ ] **Step 10: 最终证据审计与 P0-A 完成判断**

逐条核对冻结规格第 12、13 节。记录 deterministic test 计数、wheel/sdist 路径与 SHA256、preflight JSON、trusted-host run、sandbox run、dynamic-code block run、Desktop gate。明确区分 solver completion、public validation 与 qualification；未完成第二台干净 Ubuntu 验收时，只能声明本机 P0-A 实现通过，不能声明 P0-B 跨机发布完成。

- [ ] **Step 11: Review checkpoint**

运行 `git status --short` 和完整 diff 审计；确认没有 `.foampilot/`、run 时间目录、缓存、凭据、tutorial 副本或 `/tmp` 产物进入仓库。P0-A 在此停止，交给已批准顺序中的 P0-B 处理版本、提交、tag、wheel/sdist 发布和第二台机器 gate。

---

## Spec Coverage Matrix

| 冻结规格要求 | 实施任务 |
| --- | --- |
| Runtime TOML、CLI、env、XDG、有限发现、provenance | Task 1 |
| Foundation v10/root/tutorial/可信命令验证 | Task 2 |
| 移除个人路径和固定 Python | Task 1、2、7、8 |
| dynamic code/include/library 风险扫描 | Task 3 |
| 三档 isolation 与 host opt-in | Task 3 |
| 动态 mount、tutorial/evaluator 遮蔽、等价 probe | Task 4 |
| 首命令前冻结 backend、运行中不切换 | Task 5 |
| repair 重扫和不可变 artifact | Task 6 |
| qualification 强制 required | Task 7 |
| CLI/Python/Desktop 同一 resolver | Task 7 |
| deterministic、wheel、host、sandbox、Desktop gates | Task 8 |
| P0-B/P0-C/P1 非目标边界 | Global Constraints、Task 8 |
