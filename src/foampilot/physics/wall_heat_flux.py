"""Foundation OpenFOAM wallHeatFlux parsing and isolated auditing."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import re
from collections.abc import Callable, Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PatchHeatFlow(StrictModel):
    time: float
    patch: str
    minimum_w_m2: float
    maximum_w_m2: float
    integrated_heat_flow_w: float
    mean_heat_flux_w_m2: float


class WallHeatBalance(StrictModel):
    time: float
    hot_patch: str
    cold_patch: str
    hot_heat_flow_w: float
    cold_heat_flow_w: float
    normalized_imbalance: float
    rows: list[PatchHeatFlow]


WallHeatFluxRunner = Callable[
    [Path, Path], subprocess.CompletedProcess[str]
]


def parse_wall_heat_flux_data(text: str) -> list[PatchHeatFlow]:
    """Parse the six-column `wallHeatFlux.dat` format."""

    rows: list[PatchHeatFlow] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        columns = stripped.split()
        if len(columns) != 6:
            raise ValueError(
                f"wallHeatFlux row {line_number} has {len(columns)} "
                "columns; expected 6"
            )
        try:
            time, minimum, maximum, integrated, mean = (
                float(columns[index]) for index in (0, 2, 3, 4, 5)
            )
        except ValueError as error:
            raise ValueError(
                f"wallHeatFlux row {line_number} contains non-numeric data"
            ) from error
        rows.append(
            PatchHeatFlow(
                time=time,
                patch=columns[1],
                minimum_w_m2=minimum,
                maximum_w_m2=maximum,
                integrated_heat_flow_w=integrated,
                mean_heat_flux_w_m2=mean,
            )
        )
    if not rows:
        raise ValueError("wallHeatFlux output contains no data rows")
    return rows


def heat_balance(
    rows: Iterable[PatchHeatFlow],
    *,
    hot_patch: str,
    cold_patch: str,
) -> WallHeatBalance:
    """Compute the latest common-time integrated hot/cold heat balance."""

    materialized = list(rows)
    common_times = {
        row.time for row in materialized if row.patch == hot_patch
    } & {row.time for row in materialized if row.patch == cold_patch}
    if not common_times:
        raise ValueError(
            f"no common wallHeatFlux time for {hot_patch!r} and "
            f"{cold_patch!r}"
        )
    latest_time = max(common_times)
    latest = [
        row
        for row in materialized
        if row.time == latest_time
        and row.patch in {hot_patch, cold_patch}
    ]
    by_patch = {row.patch: row for row in latest}
    if set(by_patch) != {hot_patch, cold_patch}:
        raise ValueError("duplicate or missing latest wallHeatFlux patch row")
    hot = by_patch[hot_patch].integrated_heat_flow_w
    cold = by_patch[cold_patch].integrated_heat_flow_w
    scale = max(abs(hot), abs(cold))
    imbalance = abs(hot + cold) / scale if scale else float("inf")
    return WallHeatBalance(
        time=latest_time,
        hot_patch=hot_patch,
        cold_patch=cold_patch,
        hot_heat_flow_w=hot,
        cold_heat_flow_w=cold,
        normalized_imbalance=imbalance,
        rows=latest,
    )


def _run_wall_heat_flux(
    case_copy: Path,
    openfoam_root: Path,
) -> subprocess.CompletedProcess[str]:
    control = case_copy / "system" / "controlDict"
    text = control.read_text(encoding="utf-8")
    application_match = re.search(
        r"(?m)^\s*application\s+([A-Za-z][A-Za-z0-9_]*)\s*;",
        text,
    )
    if not application_match:
        raise ValueError("controlDict has no safe application entry")
    application = application_match.group(1)
    script = (
        'source "$1/etc/bashrc"\n'
        'cd "$2"\n'
        '"$3" -postProcess -func wallHeatFlux -latestTime'
    )
    return subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            script,
            "_",
            str(openfoam_root),
            str(case_copy),
            application,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _read_audit_rows(case_copy: Path) -> list[PatchHeatFlow]:
    candidates = sorted(
        case_copy.glob(
            "postProcessing/wallHeatFlux/*/wallHeatFlux.dat"
        )
    )
    if not candidates:
        raise RuntimeError(
            "postProcess completed without wallHeatFlux.dat output"
        )
    rows: list[PatchHeatFlow] = []
    for candidate in candidates:
        rows.extend(
            parse_wall_heat_flux_data(
                candidate.read_text(encoding="utf-8")
            )
        )
    return rows


def audit_wall_heat_flux(
    case_dir: Path,
    *,
    openfoam_root: Path,
    hot_patch: str,
    cold_patch: str,
    command_runner: WallHeatFluxRunner | None = None,
) -> WallHeatBalance:
    """Run `wallHeatFlux` on a temporary case copy and return its balance."""

    source = case_dir.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"case directory not found: {source}")
    runner = command_runner or _run_wall_heat_flux
    with tempfile.TemporaryDirectory(
        prefix="foampilot-wall-heat-flux-"
    ) as temporary:
        case_copy = Path(temporary) / "case"
        shutil.copytree(source, case_copy, symlinks=True)
        completed = runner(case_copy, openfoam_root.resolve())
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(
                "wallHeatFlux post-processing failed"
                + (f": {detail}" if detail else "")
            )
        return heat_balance(
            _read_audit_rows(case_copy),
            hot_patch=hot_patch,
            cold_patch=cold_patch,
        )
