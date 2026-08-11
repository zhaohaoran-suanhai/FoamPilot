from __future__ import annotations

import os
from pathlib import Path
import shutil

import pytest

from foampilot.runtime import (
    ExecutionPolicyDecision,
    RuntimeConfig,
    SandboxProbe,
)


def real_runtime_config() -> RuntimeConfig:
    """Build an opt-in real-gate runtime without workstation defaults."""

    raw_root = os.environ.get("FOAMPILOT_OPENFOAM_ROOT")
    if not raw_root:
        pytest.skip("FOAMPILOT_OPENFOAM_ROOT is required for real OpenFOAM gates")
    isolation = os.environ.get(
        "FOAMPILOT_EXECUTION_ISOLATION",
        "sandbox_preferred",
    )
    bubblewrap = os.environ.get("FOAMPILOT_BUBBLEWRAP") or shutil.which("bwrap")
    return RuntimeConfig(
        openfoam_root=Path(raw_root).expanduser().resolve(),
        isolation=isolation,
        bubblewrap=(Path(bubblewrap).resolve() if bubblewrap else None),
    )


def synthetic_execution_evidence(
    protected_paths,
) -> dict[str, object]:
    """Evidence returned by deterministic Runner test doubles."""

    return {
        "sandbox_probe": SandboxProbe(
            status="passed",
            ok=True,
            builder_sha256="a" * 64,
            namespace_flags=("--unshare-net",),
            mount_count=8,
            protected_path_count=len(protected_paths),
            return_code=0,
            detail="synthetic sandbox probe passed",
        ),
        "execution_policy": ExecutionPolicyDecision(
            requested_isolation="sandbox_preferred",
            actual_backend="bubblewrap",
            allowed=True,
            code="SANDBOX_SELECTED",
        ),
    }
