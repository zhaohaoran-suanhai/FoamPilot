"""Typed runtime inputs and results."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


IsolationPolicy = Literal[
    "sandbox_required",
    "sandbox_preferred",
    "trusted_host",
]
RuntimeSourceKind = Literal[
    "cli",
    "environment",
    "explicit_toml",
    "environment_toml",
    "user_toml",
    "discovery",
    "default",
    "python_api",
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

    @field_validator("openfoam_root")
    @classmethod
    def _absolute_openfoam_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("openfoam_root must be absolute")
        return value.resolve()

    @field_validator("bubblewrap")
    @classmethod
    def _absolute_bubblewrap(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if not value.is_absolute():
            raise ValueError("bubblewrap must be absolute")
        return value.resolve()

    @field_validator("trusted_readonly_roots")
    @classmethod
    def _safe_trusted_roots(
        cls,
        values: tuple[Path, ...],
    ) -> tuple[Path, ...]:
        home = Path.home().resolve()
        resolved: list[Path] = []
        for value in values:
            if not value.is_absolute():
                raise ValueError("trusted readonly roots must be absolute")
            root = value.resolve()
            if root == Path("/") or home == root or home.is_relative_to(root):
                raise ValueError("trusted readonly root is too broad")
            if root in resolved:
                raise ValueError("trusted readonly roots must be unique")
            resolved.append(root)
        return tuple(resolved)


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


class RiskFinding(StrictModel):
    code: str
    path: str
    line: int = Field(ge=1)
    detail: str


class ExecutionRiskReport(StrictModel):
    schema_version: Literal[1] = 1
    risk_level: Literal["low", "high", "unknown"]
    findings: tuple[RiskFinding, ...] = ()
    scanned_file_sha256: dict[str, str] = Field(default_factory=dict)
    policy_decision: str | None = None


class SandboxProbe(StrictModel):
    schema_version: Literal[1] = 1
    status: Literal["passed", "failed", "not_requested"]
    ok: bool | None
    builder_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    namespace_flags: tuple[str, ...] = ()
    mount_count: int = Field(default=0, ge=0)
    protected_path_count: int = Field(default=0, ge=0)
    failure_code: Literal[
        "BWRAP_UNAVAILABLE",
        "NAMESPACE_UNAVAILABLE",
        "SANDBOX_SETUP_FAILED",
        "TRUSTED_RUNTIME_ROOT_INVALID",
    ] | None = None
    return_code: int | None
    detail: str

    @model_validator(mode="after")
    def _consistent_status(self) -> "SandboxProbe":
        if self.status == "passed" and self.ok is not True:
            raise ValueError("passed sandbox probe must have ok=true")
        if self.status == "failed" and self.ok is not False:
            raise ValueError("failed sandbox probe must have ok=false")
        if self.status == "not_requested" and self.ok is not None:
            raise ValueError("not-requested sandbox probe must have ok=null")
        if self.status == "failed" and self.failure_code is None:
            raise ValueError("failed sandbox probe requires failure_code")
        if self.status != "failed" and self.failure_code is not None:
            raise ValueError("only failed sandbox probes have failure_code")
        return self


class ExecutionPolicyDecision(StrictModel):
    schema_version: Literal[1] = 1
    requested_isolation: IsolationPolicy
    actual_backend: Literal["bubblewrap", "host"] | None
    allowed: bool
    code: str
    dynamic_code_host_opt_in: bool = False
    fallback_reason: str | None = None
    unisolated_warning: str | None = None


class SandboxMount(StrictModel):
    kind: Literal[
        "ro_bind",
        "bind",
        "tmpfs",
        "dir",
        "symlink",
        "proc",
        "dev",
    ]
    source: Path | str | None = None
    target: Path | str


class SandboxLaunch(StrictModel):
    schema_version: Literal[1] = 1
    argv: tuple[str, ...]
    mounts: tuple[SandboxMount, ...]
    hidden_paths: tuple[Path, ...]


class RuntimeCheck(StrictModel):
    name: str
    ok: bool
    detail: str
    blocking: bool = True


class PlanStepResult(StrictModel):
    step_id: str
    command: list[str]
    return_code: int | None
    started_at: datetime
    finished_at: datetime
    # None denotes a legacy run-result that predates monotonic timing.
    elapsed_seconds: float | None = Field(default=None, ge=0)
    timed_out: bool
    cancelled: bool = False
    stdout_path: Path
    stderr_path: Path
    execution_backend: Literal["bubblewrap", "host"] = "bubblewrap"
    backend_fallback_reason: str | None = None


class ReusedStepResult(StrictModel):
    step_id: str
    stage: str
    executable: str
    source_kind: Literal["derived_cache", "parent_attempt"]
    source_id: str
    reason_codes: list[str] = Field(default_factory=list)


class PlanRunResult(StrictModel):
    case_dir: Path
    steps: list[PlanStepResult]
    failed_step_id: str | None = None
    timed_out: bool = False
    cancelled: bool = False
    reused_steps: list[ReusedStepResult] = Field(default_factory=list)
    sandbox_probe: SandboxProbe | None = None
    execution_policy: ExecutionPolicyDecision | None = None
    execution_error_code: Literal[
        "SANDBOX_SETUP_FAILED",
        "EXECUTION_WALL_BUDGET_EXHAUSTED",
    ] | None = None

    @property
    def passed(self) -> bool:
        return (
            self.failed_step_id is None
            and bool(self.steps)
            and not self.cancelled
        )
