"""Deterministic local-job reconciliation and identity-safe orphan control."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import signal
import tempfile
import time
import yaml

from foampilot.artifacts import ArtifactStore, RunSummary
from foampilot.workflow import (
    FailureDomain,
    FailureRecord,
    ResumeMetadata,
    WorkflowEvent,
    WorkflowEventState,
    WorkflowStage,
    WorkflowState,
)

from .identity import process_identity_matches
from .models import (
    JobOperation,
    JobState,
    RecoveryAction,
    RecoveryDecision,
    RecoveryState,
)
from .store import LocalJobStore


_ACTIVE_STATES = {
    JobState.SUBMITTED,
    JobState.STARTING,
    JobState.RUNNING,
    JobState.CANCEL_REQUESTED,
    JobState.CANCELLING,
}
_TERMINAL_STATES = {
    JobState.CANCELLED,
    JobState.COMPLETED,
    JobState.FAILED,
    JobState.INTERRUPTED,
}
_RUN_PRODUCING_OPERATIONS = {
    JobOperation.SOLVE,
    JobOperation.RESUME,
    JobOperation.RERUN,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run_path(store: LocalJobStore) -> tuple[Path | None, tuple[str, ...]]:
    status = store.read_status()
    if status.run_dir is not None:
        relative = PurePosixPath(status.run_dir)
        if relative.is_absolute() or ".." in relative.parts:
            return None, ("job status run path escapes the job root",)
        source = store.root
        for part in relative.parts:
            source = source / part
            if source.is_symlink():
                return None, ("job status run path contains a symbolic link",)
        candidate = source.resolve()
        if (
            not candidate.is_relative_to(store.root)
            or not candidate.is_dir()
        ):
            return None, ("job status run path is unavailable",)
        return candidate, ()
    candidates = tuple(
        child.resolve()
        for child in sorted(store.root.glob("run-*"))
        if child.is_dir() and not child.is_symlink()
    )
    if len(candidates) > 1:
        return None, ("job root contains multiple unbound runs",)
    return (candidates[0], ()) if candidates else (None, ())


def _artifact_state(
    store: LocalJobStore,
    run_dir: Path | None,
) -> tuple[bool, tuple[str, ...], bool]:
    if run_dir is None:
        return False, (), False
    summary = run_dir / "summary.json"
    manifest = run_dir / ArtifactStore.manifest_name
    if not summary.exists() and not manifest.exists():
        return False, (), False
    if not summary.is_file() or not manifest.is_file():
        return False, ("terminal summary/manifest pair is incomplete",), True
    try:
        parsed = ArtifactStore.read_summary(run_dir)
        issues = ArtifactStore(store.root).verify(run_dir)
    except (OSError, ValueError) as error:
        return False, (f"invalid terminal evidence: {error}",), True
    if issues:
        return False, tuple(issues), True
    return True, (), parsed.resume.allowed


def reconcile_job(
    job_root: str | Path,
    *,
    heartbeat_stale_seconds: float = 5.0,
    now: Callable[[], datetime] = _utc_now,
) -> RecoveryDecision:
    """Read job/process/artifact evidence and return the allowed action set."""

    if heartbeat_stale_seconds <= 0:
        raise ValueError("heartbeat stale threshold must be positive")
    store = LocalJobStore(job_root)
    spec = store.read_spec()
    status = store.read_status()
    if status.job_id != spec.job_id:
        raise ValueError("job receipt and status IDs do not match")
    lock_held = store.writer_lock_held()
    worker_alive = (
        status.worker is not None and process_identity_matches(status.worker)
    )
    child_alive = (
        status.current_child is not None
        and process_identity_matches(status.current_child)
    )
    run_dir, path_issues = _run_path(store)
    artifact_valid, artifact_issues, strict_resume = _artifact_state(
        store,
        run_dir,
    )
    issues = (*path_issues, *artifact_issues)
    control_failure = store.root / "worker-control-failure.json"
    control_failure_recorded = False
    if control_failure.is_file() and not control_failure.is_symlink():
        try:
            payload = json.loads(control_failure.read_text(encoding="utf-8"))
            control_failure_recorded = (
                isinstance(payload, dict)
                and payload.get("job_id") == spec.job_id
                and payload.get("code") == "JOB_STATUS_WRITE_FAILED"
            )
        except (OSError, ValueError):
            control_failure_recorded = False

    if worker_alive and lock_held and status.state in _ACTIVE_STATES:
        heartbeat = status.last_heartbeat_at
        stale = heartbeat is None or (
            now() - heartbeat
        ).total_seconds() > heartbeat_stale_seconds
        return RecoveryDecision(
            job_id=spec.job_id,
            state=(
                RecoveryState.UNRESPONSIVE
                if stale
                else RecoveryState.RUNNING
            ),
            code="JOB_HEARTBEAT_STALE" if stale else "JOB_RUNNING",
            reason_zh=(
                "worker 身份仍匹配，但 heartbeat 已过期。"
                if stale
                else "worker 身份、writer lock 与 heartbeat 均有效。"
            ),
            recovery_zh=(
                "保持只读观察并可请求取消；不要直接判定求解失败。"
                if stale
                else "重新连接观察，或显式请求取消。"
            ),
            allowed_actions=(RecoveryAction.ATTACH, RecoveryAction.CANCEL),
            worker_alive=True,
            child_alive=child_alive,
            writer_lock_held=True,
            run_dir=run_dir,
        )
    if child_alive and not worker_alive and not lock_held:
        return RecoveryDecision(
            job_id=spec.job_id,
            state=RecoveryState.ORPHANED_ACTIVE,
            code="JOB_ORPHANED_ACTIVE",
            reason_zh="原 worker 已消失，但身份匹配的受监督进程组仍存活。",
            recovery_zh="只读检查或受控终止该进程组；禁止接管 workflow。",
            allowed_actions=(
                RecoveryAction.INSPECT,
                RecoveryAction.TERMINATE_ORPHAN,
            ),
            worker_alive=False,
            child_alive=True,
            writer_lock_held=False,
            run_dir=run_dir,
        )
    if artifact_valid:
        actions = [RecoveryAction.REPORT]
        if strict_resume:
            actions.append(RecoveryAction.STRICT_RESUME)
        actions.append(RecoveryAction.RERUN)
        return RecoveryDecision(
            job_id=spec.job_id,
            state=RecoveryState.FINALIZED,
            code="JOB_FINALIZED",
            reason_zh="任务已有可验证的终态 summary 与 manifest。",
            recovery_zh="可查看报告；仅在明确满足资格时恢复模型阶段，或完整重跑。",
            allowed_actions=tuple(actions),
            worker_alive=worker_alive,
            child_alive=child_alive,
            writer_lock_held=lock_held,
            run_dir=run_dir,
        )
    if issues or (status.state in _TERMINAL_STATES and run_dir is not None):
        return RecoveryDecision(
            job_id=spec.job_id,
            state=RecoveryState.EVIDENCE_DAMAGED,
            code="JOB_EVIDENCE_DAMAGED",
            reason_zh="终态产物缺失、损坏或无法通过 manifest 校验。",
            recovery_zh="只读检查现有证据；禁止严格恢复，可从规范输入完整重跑。",
            allowed_actions=(RecoveryAction.INSPECT,),
            worker_alive=worker_alive,
            child_alive=child_alive,
            writer_lock_held=lock_held,
            run_dir=run_dir,
            manifest_issues=tuple(issues) or ("terminal run is invalid",),
        )
    if status.state in _TERMINAL_STATES and run_dir is None:
        if (
            status.state == JobState.CANCELLED
            and status.terminal_code == "USER_CANCELLED"
        ):
            return RecoveryDecision(
                job_id=spec.job_id,
                state=RecoveryState.FINALIZED,
                code="JOB_CANCELLED_BEFORE_RUN",
                reason_zh="任务在产生 run 前已按用户请求正常取消。",
                recovery_zh="可检查 worker 日志，或从原始规范输入重新提交。",
                allowed_actions=(RecoveryAction.INSPECT,),
                worker_alive=worker_alive,
                child_alive=child_alive,
                writer_lock_held=lock_held,
            )
        if status.state == JobState.FAILED and status.terminal_code in {
            "JOB_BOOTSTRAP_FAILED",
            "JOB_STATUS_WRITE_FAILED",
        }:
            return RecoveryDecision(
                job_id=spec.job_id,
                state=RecoveryState.FINALIZED,
                code=status.terminal_code,
                reason_zh="worker 在产生 run 前记录了明确的本机控制面失败。",
                recovery_zh="检查 worker 日志并修复输入或存储后重新提交。",
                allowed_actions=(RecoveryAction.INSPECT,),
                worker_alive=worker_alive,
                child_alive=child_alive,
                writer_lock_held=lock_held,
            )
        if spec.operation in _RUN_PRODUCING_OPERATIONS:
            return RecoveryDecision(
                job_id=spec.job_id,
                state=RecoveryState.EVIDENCE_DAMAGED,
                code="JOB_TERMINAL_RUN_MISSING",
                reason_zh="求解类任务已记录终态，但没有可验证的 run 目录。",
                recovery_zh="检查 worker 日志；禁止严格恢复，可从规范输入完整重跑。",
                allowed_actions=(RecoveryAction.INSPECT,),
                worker_alive=worker_alive,
                child_alive=child_alive,
                writer_lock_held=lock_held,
                manifest_issues=("terminal solve-like job has no run",),
            )
        return RecoveryDecision(
            job_id=spec.job_id,
            state=RecoveryState.FINALIZED,
            code="JOB_FINALIZED_WITHOUT_RUN",
            reason_zh="非求解任务已有持久化 job 终态。",
            recovery_zh="可检查 worker 输出或重新提交新任务。",
            allowed_actions=(RecoveryAction.REPORT,),
            worker_alive=worker_alive,
            child_alive=child_alive,
            writer_lock_held=lock_held,
        )
    if not worker_alive and not child_alive and not lock_held:
        if control_failure_recorded:
            return RecoveryDecision(
                job_id=spec.job_id,
                state=RecoveryState.ORPHANED_STOPPED,
                code="JOB_STATUS_WRITE_FAILED",
                reason_zh="worker 已停止，且独立证据记录了 job 状态持久化失败。",
                recovery_zh=(
                    "检查 worker 日志；有 partial run 时先固化中断，否则修复存储后重新提交。"
                ),
                allowed_actions=(
                    (RecoveryAction.RECOVER_FINALIZE,)
                    if run_dir is not None
                    else (RecoveryAction.INSPECT,)
                ),
                worker_alive=False,
                child_alive=False,
                writer_lock_held=False,
                run_dir=run_dir,
            )
        if run_dir is None:
            return RecoveryDecision(
                job_id=spec.job_id,
                state=RecoveryState.ORPHANED_STOPPED,
                code="JOB_ORPHANED_STOPPED_WITHOUT_RUN",
                reason_zh="worker 已停止，但任务尚未产生可固化的 run。",
                recovery_zh="检查 worker 日志，并从原始规范输入重新提交任务。",
                allowed_actions=(RecoveryAction.INSPECT,),
                worker_alive=False,
                child_alive=False,
                writer_lock_held=False,
            )
        return RecoveryDecision(
            job_id=spec.job_id,
            state=RecoveryState.ORPHANED_STOPPED,
            code="JOB_ORPHANED_STOPPED",
            reason_zh="worker 与已记录的受监督进程均已消失，且没有终态证据。",
            recovery_zh="可安全固化为中断，或从规范输入完整重跑。",
            allowed_actions=(
                RecoveryAction.RECOVER_FINALIZE,
            ),
            worker_alive=False,
            child_alive=False,
            writer_lock_held=False,
            run_dir=run_dir,
        )
    return RecoveryDecision(
        job_id=spec.job_id,
        state=RecoveryState.EVIDENCE_DAMAGED,
        code="JOB_OWNERSHIP_INCONSISTENT",
        reason_zh="进程身份、writer lock 与持久化状态互相矛盾。",
        recovery_zh="保持只读并检查进程；不得发送信号或固化终态。",
        allowed_actions=(RecoveryAction.INSPECT,),
        worker_alive=worker_alive,
        child_alive=child_alive,
        writer_lock_held=lock_held,
        run_dir=run_dir,
        manifest_issues=("job ownership evidence is inconsistent",),
    )


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reap_if_child(pid: int) -> None:
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass


def _wait_group_exit(pgid: int, pid: int, deadline: float) -> bool:
    while time.monotonic() < deadline:
        _reap_if_child(pid)
        if not _group_exists(pgid):
            return True
        time.sleep(0.01)
    _reap_if_child(pid)
    return not _group_exists(pgid)


def terminate_orphan(
    job_root: str | Path,
    *,
    grace_seconds: float = 2.0,
) -> RecoveryDecision:
    """Terminate only the fully identified orphan process group."""

    if grace_seconds < 0:
        raise ValueError("grace period cannot be negative")
    store = LocalJobStore(job_root)
    decision = reconcile_job(store.root)
    if decision.state != RecoveryState.ORPHANED_ACTIVE:
        raise ValueError(f"JOB_ORPHAN_NOT_ACTIVE: {decision.state.value}")
    status = store.read_status()
    identity = status.current_child
    if identity is None or not process_identity_matches(identity):
        raise ValueError("JOB_CHILD_IDENTITY_MISMATCH")
    if identity.pgid == os.getpgrp():
        raise ValueError("JOB_PROCESS_GROUP_NOT_OWNED")
    with store.writer_lock():
        if not process_identity_matches(identity):
            raise ValueError("JOB_CHILD_IDENTITY_MISMATCH")
        try:
            os.killpg(identity.pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        exited = _wait_group_exit(
            identity.pgid,
            identity.pid,
            time.monotonic() + grace_seconds,
        )
        if not exited:
            try:
                os.killpg(identity.pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            exited = _wait_group_exit(
                identity.pgid,
                identity.pid,
                time.monotonic() + max(grace_seconds, 0.5),
            )
        if not exited:
            raise RuntimeError("JOB_CANCEL_INCOMPLETE: orphan group remains alive")
        store.update_status(current_child=None)
    return reconcile_job(store.root)


def _write_json_exclusive(path: Path, payload: object) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _workflow_evidence(run_dir: Path) -> tuple[int, str | None]:
    path = run_dir / "workflow-events.jsonl"
    sequence = 0
    last_completed: str | None = None
    if not path.is_file() or path.is_symlink():
        return sequence, last_completed
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = WorkflowEvent.model_validate_json(line)
        except ValueError:
            continue
        sequence = max(sequence, event.sequence)
        if event.state == WorkflowEventState.COMPLETED:
            last_completed = event.stage.value
    return sequence, last_completed


def _has_interrupted_final_event(run_dir: Path) -> bool:
    path = run_dir / "workflow-events.jsonl"
    if not path.is_file() or path.is_symlink():
        return False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = WorkflowEvent.model_validate_json(line)
        except ValueError:
            continue
        if (
            event.stage == WorkflowStage.RUN_FINALIZED
            and event.state == WorkflowEventState.INTERRUPTED
        ):
            return True
    return False


def _partial_recovery_payload(run_dir: Path) -> dict[str, object] | None:
    """Recognize only our own incomplete recover-finalize transaction."""

    path = run_dir / "interruption.json"
    manifest = run_dir / ArtifactStore.manifest_name
    if not path.is_file() or path.is_symlink() or manifest.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return None
    if payload.get("code") not in {"HOST_RESTARTED", "WORKER_INTERRUPTED"}:
        return None
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        if not summary_path.is_file() or summary_path.is_symlink():
            return None
        try:
            summary = ArtifactStore.read_summary(run_dir)
        except (OSError, ValueError):
            return None
        if summary.workflow_state != WorkflowState.INTERRUPTED:
            return None
    return payload


def _append_interrupted_event(
    run_dir: Path,
    *,
    sequence: int,
    occurred_at: datetime,
) -> None:
    path = run_dir / "workflow-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    event = WorkflowEvent(
        sequence=sequence,
        stage=WorkflowStage.RUN_FINALIZED,
        state=WorkflowEventState.INTERRUPTED,
        occurred_at=occurred_at,
        detail="The local worker stopped before a canonical terminal outcome.",
        evidence_paths=["interruption.json"],
    )
    needs_separator = path.is_file() and path.stat().st_size > 0
    if needs_separator:
        with path.open("rb") as existing:
            existing.seek(-1, os.SEEK_END)
            needs_separator = existing.read(1) != b"\n"
    with path.open("a", encoding="utf-8") as stream:
        if needs_separator:
            stream.write("\n")
        stream.write(event.model_dump_json() + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _task_id(run_dir: Path, fallback: str) -> str:
    path = run_dir / "task.yaml"
    if not path.is_file() or path.is_symlink():
        return fallback
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return fallback
    if isinstance(payload, dict) and isinstance(payload.get("task_id"), str):
        return payload["task_id"]
    return fallback


def recover_finalize(
    job_root: str | Path,
    *,
    recorded_at: Callable[[], datetime] = _utc_now,
) -> RecoveryDecision:
    """Freeze an ownerless partial solve as neutral interrupted evidence."""

    store = LocalJobStore(job_root)
    decision = reconcile_job(store.root, now=recorded_at)
    if decision.state == RecoveryState.FINALIZED and decision.run_dir is not None:
        summary = ArtifactStore.read_summary(decision.run_dir)
        if summary.workflow_state == WorkflowState.INTERRUPTED:
            status = store.read_status()
            if status.state != JobState.INTERRUPTED:
                if (
                    status.worker is not None
                    and process_identity_matches(status.worker)
                ) or (
                    status.current_child is not None
                    and process_identity_matches(status.current_child)
                ):
                    raise ValueError(
                        "JOB_RECOVERY_NOT_ALLOWED: an owned process is alive"
                    )
                with store.writer_lock():
                    store.update_status(
                        state=JobState.INTERRUPTED,
                        current_child=None,
                        finished_at=recorded_at(),
                        last_heartbeat_at=recorded_at(),
                        terminal_code=(
                            summary.terminal_blocker.code
                            if summary.terminal_blocker is not None
                            else "WORKER_INTERRUPTED"
                        ),
                    )
                return reconcile_job(store.root, now=recorded_at)
            return decision
        raise ValueError("JOB_RECOVERY_NOT_ALLOWED: run already has another terminal state")
    run_dir = decision.run_dir
    partial_recovery = (
        _partial_recovery_payload(run_dir)
        if run_dir is not None
        else None
    )
    if (
        decision.state != RecoveryState.ORPHANED_STOPPED
        and partial_recovery is None
    ):
        raise ValueError(f"JOB_RECOVERY_NOT_ALLOWED: {decision.state.value}")
    if run_dir is None:
        raise ValueError("JOB_RUN_UNAVAILABLE: no partial run can be finalized")

    with store.writer_lock():
        status = store.read_status()
        if (
            status.worker is not None
            and process_identity_matches(status.worker)
        ) or (
            status.current_child is not None
            and process_identity_matches(status.current_child)
        ):
            raise ValueError("JOB_RECOVERY_NOT_ALLOWED: an owned process is alive")
        partial_recovery = _partial_recovery_payload(run_dir)
        if partial_recovery is None and (
            (run_dir / "summary.json").exists()
            or (run_dir / ArtifactStore.manifest_name).exists()
            or (run_dir / "interruption.json").exists()
        ):
            raise ValueError("JOB_RECOVERY_NOT_ALLOWED: terminal evidence already exists")
        if partial_recovery is not None:
            try:
                timestamp = datetime.fromisoformat(
                    str(partial_recovery["recorded_at"])
                )
            except (KeyError, ValueError) as error:
                raise ValueError(
                    "JOB_RECOVERY_NOT_ALLOWED: invalid recovery timestamp"
                ) from error
            code = str(partial_recovery["code"])
        else:
            timestamp = recorded_at()
            code = ""
        current_boot = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip()
        if not code:
            code = (
                "HOST_RESTARTED"
                if status.worker is not None
                and status.worker.boot_id != current_boot
                else "WORKER_INTERRUPTED"
            )
        log_offsets: dict[str, int] = {}
        for path in (
            store.root / "worker.stdout.log",
            store.root / "worker.stderr.log",
            store.root / "job-events.jsonl",
            run_dir / "workflow-events.jsonl",
            run_dir / "activity-events.jsonl",
        ):
            if path.is_file() and not path.is_symlink():
                log_offsets[path.relative_to(store.root).as_posix()] = path.stat().st_size
        sequence, last_completed = _workflow_evidence(run_dir)
        interruption = {
            "schema_version": 1,
            "code": code,
            "recorded_at": timestamp.isoformat(),
            "last_stage": status.current_stage,
            "last_step_id": status.current_step_id,
            "last_heartbeat_at": (
                status.last_heartbeat_at.isoformat()
                if status.last_heartbeat_at is not None
                else None
            ),
            "worker_identity": (
                status.worker.model_dump(mode="json")
                if status.worker is not None
                else None
            ),
            "child_identity": (
                status.current_child.model_dump(mode="json")
                if status.current_child is not None
                else None
            ),
            "cleanup": {
                "worker_alive": False,
                "child_alive": False,
                "writer_lock_acquired": True,
            },
            "log_offsets": log_offsets,
        }
        if partial_recovery is None:
            _write_json_exclusive(run_dir / "interruption.json", interruption)
        if not _has_interrupted_final_event(run_dir):
            _append_interrupted_event(
                run_dir,
                sequence=sequence + 1,
                occurred_at=timestamp,
            )
        blocker = FailureRecord(
            domain=FailureDomain.WORKFLOW,
            code=code,
            retryable=False,
            detail="The durable worker ended without a canonical terminal outcome.",
            message="本机 worker 异常中断，现有证据已安全固化。",
            recovery="从已保存的规范输入完整重跑；不要把它当作 OpenFOAM 断点续算。",
            evidence_paths=["interruption.json"],
        )
        summary = RunSummary(
            task_id=_task_id(run_dir, store.read_spec().job_id),
            workflow_state=WorkflowState.INTERRUPTED,
            last_completed_stage=last_completed,
            primary_failure=None,
            terminal_blocker=blocker,
            resume=ResumeMetadata(
                allowed=False,
                reason="interrupted worker state is not a strict resume checkpoint",
            ),
            message="The interrupted local job was finalized without claiming CFD success.",
        )
        summary_path = run_dir / "summary.json"
        if not summary_path.exists():
            _write_json_exclusive(
                summary_path,
                summary.model_dump(mode="json"),
            )
        ArtifactStore(store.root).finalize(run_dir)
        store.update_status(
            state=JobState.INTERRUPTED,
            current_child=None,
            finished_at=timestamp,
            last_heartbeat_at=timestamp,
            terminal_code=code,
        )
    return reconcile_job(store.root, now=recorded_at)


__all__ = ["reconcile_job", "recover_finalize", "terminate_orphan"]
