"""Deterministic local-job reconciliation and identity-safe orphan control."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import os
from pathlib import Path, PurePosixPath
import signal
import time

from foampilot.artifacts import ArtifactStore

from .identity import process_identity_matches
from .models import (
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
            allowed_actions=(RecoveryAction.INSPECT, RecoveryAction.RERUN),
            worker_alive=worker_alive,
            child_alive=child_alive,
            writer_lock_held=lock_held,
            run_dir=run_dir,
            manifest_issues=tuple(issues) or ("terminal run is invalid",),
        )
    if status.state in _TERMINAL_STATES and run_dir is None:
        return RecoveryDecision(
            job_id=spec.job_id,
            state=RecoveryState.FINALIZED,
            code="JOB_FINALIZED_WITHOUT_RUN",
            reason_zh="非求解任务已有持久化 job 终态。",
            recovery_zh="可检查 worker 输出或重新提交新任务。",
            allowed_actions=(RecoveryAction.REPORT, RecoveryAction.RERUN),
            worker_alive=worker_alive,
            child_alive=child_alive,
            writer_lock_held=lock_held,
        )
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
    if not worker_alive and not child_alive and not lock_held:
        return RecoveryDecision(
            job_id=spec.job_id,
            state=RecoveryState.ORPHANED_STOPPED,
            code="JOB_ORPHANED_STOPPED",
            reason_zh="worker 与已记录的受监督进程均已消失，且没有终态证据。",
            recovery_zh="可安全固化为中断，或从规范输入完整重跑。",
            allowed_actions=(
                RecoveryAction.RECOVER_FINALIZE,
                RecoveryAction.RERUN,
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


__all__ = ["reconcile_job", "terminate_orphan"]
