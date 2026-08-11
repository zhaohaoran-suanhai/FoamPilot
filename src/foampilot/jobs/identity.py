"""Linux process identity helpers that defend against PID reuse."""

from __future__ import annotations

import os
from pathlib import Path

from .models import ProcessIdentity


_BOOT_ID = Path("/proc/sys/kernel/random/boot_id")


def _boot_id() -> str:
    return _BOOT_ID.read_text(encoding="utf-8").strip()


def _start_token(pid: int) -> int:
    text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    closing = text.rfind(")")
    if closing < 0:
        raise ValueError("invalid process stat record")
    fields_after_command = text[closing + 2 :].split()
    return int(fields_after_command[19])


def process_identity(pid: int) -> ProcessIdentity:
    return ProcessIdentity(
        pid=pid,
        pgid=os.getpgid(pid),
        start_token=_start_token(pid),
        boot_id=_boot_id(),
    )


def current_process_identity() -> ProcessIdentity:
    return process_identity(os.getpid())


def process_identity_matches(identity: ProcessIdentity) -> bool:
    try:
        current = process_identity(identity.pid)
    except (OSError, ValueError):
        return False
    return current == identity


__all__ = [
    "current_process_identity",
    "process_identity",
    "process_identity_matches",
]
