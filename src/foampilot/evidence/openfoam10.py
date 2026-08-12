"""One-pass Foundation OpenFOAM v10 native evidence extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import re

from foampilot.plans import ExecutionPlan, NativeCommand
from foampilot.runtime import PlanRunResult, PlanStepResult

from .extractors import EvidenceExtractionError
from .models import (
    ContinuityFact,
    CourantFact,
    FieldOperationFact,
    MeshCheckFact,
    NativeErrorFact,
    RawCommandEvidence,
    ReusedCommandEvidence,
    ResidualFact,
    RunFacts,
    SolverProgressFact,
)


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
_TIME = re.compile(
    rf"^\s*(?:Time|Iteration)\s*(?:=|:)\s*({_NUMBER})\s*s?\s*$",
    re.IGNORECASE,
)
_REGION = re.compile(
    r"^\s*(?:Region|Solving for region)\s*:?[ \t]+([A-Za-z0-9_.:-]+)\s*$",
    re.IGNORECASE,
)
_RESIDUAL = re.compile(
    rf"Solving for\s+([^,\s]+),\s*"
    rf"Initial residual\s*=\s*({_NUMBER}),\s*"
    rf"Final residual\s*=\s*({_NUMBER}),\s*"
    rf"No Iterations\s+(\d+)",
    re.IGNORECASE,
)
_CONTINUITY = re.compile(
    rf"continuity errors\s*:\s*sum local\s*=\s*({_NUMBER}),\s*"
    rf"global\s*=\s*({_NUMBER}),\s*cumulative\s*=\s*({_NUMBER})",
    re.IGNORECASE,
)
_COURANT = re.compile(
    rf"Courant Number\s+mean\s*:\s*({_NUMBER})\s+max\s*:\s*({_NUMBER})",
    re.IGNORECASE,
)
_COURANT_ALT = re.compile(
    rf"Mean and max Courant Numbers\s*=\s*({_NUMBER})\s+({_NUMBER})",
    re.IGNORECASE,
)
_COURANT_BARE = re.compile(
    rf"Courant Number\s*[:=]?\s*({_NUMBER})",
    re.IGNORECASE,
)
_COUNT = {
    "points": re.compile(r"^\s*points\s*:\s*(\d+)\b", re.IGNORECASE),
    "faces": re.compile(r"^\s*faces\s*:\s*(\d+)\b", re.IGNORECASE),
    "cells": re.compile(r"^\s*cells\s*:\s*(\d+)\b", re.IGNORECASE),
    "regions": re.compile(
        r"^\s*Number of regions\s*:\s*(\d+)\b", re.IGNORECASE
    ),
}
_NON_ORTHOGONALITY = re.compile(
    rf"Mesh non-orthogonality\s+Max\s*:\s*({_NUMBER})",
    re.IGNORECASE,
)
_SKEWNESS = re.compile(rf"Max skewness\s*=\s*({_NUMBER})", re.IGNORECASE)
_NEGATIVE_VOLUMES = re.compile(
    r"(?:negative volume cells|cells with negative volume)\s*:\s*(\d+)",
    re.IGNORECASE,
)
_NON_FINITE = re.compile(
    r"(?<![A-Za-z])(?:nan|[-+]?inf)(?![A-Za-z])",
    re.IGNORECASE,
)
_FIELD_OPERATION = re.compile(
    rf"\b(min|max|volIntegrate)\s*"
    rf"(?:\(\s*([^\s)]+)\s*\)|\(\s*\)\s+of\s+([^\s=]+))"
    rf"\s*=\s*({_NUMBER})",
    re.IGNORECASE,
)
_MISSING_KEYWORD = re.compile(
    r"\bkeyword\s+([^\s]+)\s+(?:is\s+)?(?:undefined|not\s+found)",
    re.IGNORECASE,
)
_MISSING_OBJECT = re.compile(
    r"(?:cannot\s+find|could\s+not\s+find|unknown)\s+"
    r"(?:object|field)\s+([^\s,;]+)",
    re.IGNORECASE,
)
_MISSING_CASE_FILE = re.compile(
    r"(?:cannot\s+find|could\s+not\s+find|no\s+such\s+file)\s+"
    r"(?:file\s+)?((?:0|constant|system)/[^\s,;]+)",
    re.IGNORECASE,
)
_CASE_PATH = re.compile(
    r"(?:^|\s)(?:/[^\s:]*/case/|/case/)"
    r"((?:0|constant|system)/[^\s:,;\[\]]+)"
)


@dataclass
class _StepAccumulator:
    step_id: str
    stage: str
    simulation_time: float | None = None
    region: str | None = None
    progress: list[SolverProgressFact] = field(default_factory=list)
    residuals: list[ResidualFact] = field(default_factory=list)
    continuity: list[ContinuityFact] = field(default_factory=list)
    courant: list[CourantFact] = field(default_factory=list)
    errors: list[NativeErrorFact] = field(default_factory=list)
    field_operations: list[FieldOperationFact] = field(default_factory=list)
    error_codes: set[str] = field(default_factory=set)
    normal_end: bool = False
    mesh_ok_marker: bool = False
    points: int | None = None
    faces: int | None = None
    cells: int | None = None
    regions: int | None = None
    max_non_orthogonality: float | None = None
    max_skewness: float | None = None
    negative_volume_cells: int | None = None
    parse_truncated: bool = False
    mesh_diagnostics: list[str] = field(default_factory=list)
    line_number: int = 0

    def _error(
        self,
        code: str,
        detail: str,
        *,
        subject: str | None = None,
        path: str | None = None,
    ) -> None:
        if code in self.error_codes:
            return
        self.error_codes.add(code)
        self.errors.append(
            NativeErrorFact(
                step_id=self.step_id,
                code=code,
                detail=detail[:500],
                subject=subject,
                path=path,
                line_number=self.line_number,
            )
        )

    def feed(self, line: str) -> None:
        self.line_number += 1
        stripped = line.rstrip("\r\n")
        time_match = _TIME.match(stripped)
        if time_match is not None:
            self.simulation_time = float(time_match.group(1))
            self.progress.append(
                SolverProgressFact(
                    step_id=self.step_id,
                    simulation_time=self.simulation_time,
                    line_number=self.line_number,
                )
            )
        region_match = _REGION.match(stripped)
        if region_match is not None:
            self.region = region_match.group(1)
        residual_match = _RESIDUAL.search(stripped)
        if residual_match is not None:
            self.residuals.append(
                ResidualFact(
                    step_id=self.step_id,
                    simulation_time=self.simulation_time,
                    region=self.region,
                    field=residual_match.group(1),
                    initial=float(residual_match.group(2)),
                    final=float(residual_match.group(3)),
                    iterations=int(residual_match.group(4)),
                    line_number=self.line_number,
                )
            )
        continuity_match = _CONTINUITY.search(stripped)
        if continuity_match is not None:
            self.continuity.append(
                ContinuityFact(
                    step_id=self.step_id,
                    simulation_time=self.simulation_time,
                    region=self.region,
                    local=float(continuity_match.group(1)),
                    global_value=float(continuity_match.group(2)),
                    cumulative=float(continuity_match.group(3)),
                    line_number=self.line_number,
                )
            )
        courant_match = (
            _COURANT.search(stripped)
            or _COURANT_ALT.search(stripped)
            or _COURANT_BARE.search(stripped)
        )
        if courant_match is not None:
            maximum_group = 2 if courant_match.lastindex == 2 else 1
            self.courant.append(
                CourantFact(
                    step_id=self.step_id,
                    simulation_time=self.simulation_time,
                    region=self.region,
                    mean=float(courant_match.group(1)),
                    maximum=float(courant_match.group(maximum_group)),
                    line_number=self.line_number,
                )
            )
        for name, pattern in _COUNT.items():
            match = pattern.search(stripped)
            if match is not None:
                setattr(self, name, int(match.group(1)))
        non_orthogonality = _NON_ORTHOGONALITY.search(stripped)
        if non_orthogonality is not None:
            self.max_non_orthogonality = float(non_orthogonality.group(1))
        skewness = _SKEWNESS.search(stripped)
        if skewness is not None:
            self.max_skewness = float(skewness.group(1))
        negative_volumes = _NEGATIVE_VOLUMES.search(stripped)
        if negative_volumes is not None:
            self.negative_volume_cells = int(negative_volumes.group(1))
        if stripped.lstrip().startswith("***") or re.search(
            r"\bFailed\s+\d+\s+mesh checks?\b", stripped
        ):
            diagnostic = stripped.lstrip("*").strip()[:500]
            if diagnostic and diagnostic not in self.mesh_diagnostics:
                self.mesh_diagnostics.append(diagnostic)
        field_operation = _FIELD_OPERATION.search(stripped)
        if field_operation is not None:
            self.field_operations.append(
                FieldOperationFact(
                    step_id=self.step_id,
                    simulation_time=self.simulation_time,
                    operation=field_operation.group(1),
                    field=field_operation.group(2) or field_operation.group(3),
                    value=float(field_operation.group(4)),
                    line_number=self.line_number,
                )
            )

        if re.fullmatch(r"\s*End\s*", stripped):
            self.normal_end = True
        if re.search(r"\bMesh OK\b", stripped):
            self.mesh_ok_marker = True
        keyword = _MISSING_KEYWORD.search(stripped)
        missing_object = _MISSING_OBJECT.search(stripped)
        missing_file = _MISSING_CASE_FILE.search(stripped)
        path_match = _CASE_PATH.search(stripped)
        if re.match(r"^\s*(?:bwrap|prlimit):", stripped):
            self._error("EXECUTION_BACKEND_ERROR", stripped)
        elif keyword is not None:
            self._error(
                "MISSING_DICTIONARY_KEYWORD",
                stripped,
                subject=keyword.group(1).rstrip(".:,;"),
                path=(
                    path_match.group(1).rstrip(".\")'")
                    if path_match is not None
                    else None
                ),
            )
        elif missing_object is not None:
            self._error(
                "MISSING_REGISTRY_OBJECT",
                stripped,
                subject=missing_object.group(1).rstrip(".:,;"),
            )
        elif missing_file is not None:
            self._error(
                "MISSING_CASE_FILE",
                stripped,
                path=missing_file.group(1).rstrip(".:,;\")'"),
            )
        elif "unknown function type" in stripped.casefold() or (
            "unknown function object" in stripped.casefold()
        ):
            self._error("UNKNOWN_FUNCTION_OBJECT_TYPE", stripped)
        elif "different dimensions" in stripped.casefold() or (
            "inconsistent dimensions" in stripped.casefold()
        ) or "dimension mismatch" in stripped.casefold():
            self._error("DIMENSION_MISMATCH", stripped)
        elif "invalid option" in stripped.casefold() or (
            "unknown option" in stripped.casefold()
        ) or "unrecognized option" in stripped.casefold():
            self._error("INVALID_OPTION", stripped)
        elif re.search(
            r"^\s*(?:floating point exception\b|"
            r".*(?:caught|received|signal).*\bsigfpe\b)",
            stripped,
            re.I,
        ):
            self._error("FLOATING_POINT_EXCEPTION", stripped)
        elif re.search(r"segmentation fault|\bsigsegv\b", stripped, re.I):
            self._error("SEGMENTATION_FAULT", stripped)
        elif re.search(r"FOAM\s+FATAL", stripped, re.I):
            self._error("FOAM_FATAL_ERROR", stripped)
        elif _NON_FINITE.search(stripped):
            self._error("NON_FINITE_VALUE", stripped)

    def finalized_progress(self) -> tuple[SolverProgressFact, ...]:
        if not self.progress:
            return ()
        result = list(self.progress)
        result[-1] = result[-1].model_copy(
            update={
                "completed_normally": (
                    self.normal_end
                    and not self.errors
                    and not self.parse_truncated
                )
            }
        )
        return tuple(result)


class OpenFOAM10EvidenceExtractor:
    identity = "foampilot.evidence.foundation10/1.0.0/protocol-1"

    def __init__(self, *, max_log_bytes: int = 4 * 1024 * 1024) -> None:
        if max_log_bytes < 1:
            raise ValueError("max_log_bytes must be positive")
        self.max_log_bytes = max_log_bytes

    @staticmethod
    def _relative_log(path: Path, case_root: Path) -> str:
        if path.suffix.casefold() in {".gz", ".bz2", ".xz", ".zip"}:
            raise EvidenceExtractionError(
                "COMPRESSED_LOG",
                f"compressed log input is not accepted: {path.name}",
            )
        try:
            return path.resolve().relative_to(case_root).as_posix()
        except ValueError as error:
            raise EvidenceExtractionError(
                "LOG_OUTSIDE_CASE",
                f"log is outside the attempt case: {path}",
            ) from error

    def _read_once(
        self,
        path: Path,
        case_root: Path,
        accumulator: _StepAccumulator,
    ) -> tuple[str, str]:
        relative = self._relative_log(path, case_root)
        digest = sha256()
        parsed_bytes = 0
        pending = b""
        try:
            handle = path.open("rb")
        except OSError as error:
            raise EvidenceExtractionError(
                "LOG_READ_FAILED", f"cannot read {relative}: {error}"
            ) from error
        with handle:
            while chunk := handle.read(64 * 1024):
                digest.update(chunk)
                if parsed_bytes >= self.max_log_bytes:
                    accumulator.parse_truncated = True
                    continue
                remaining = self.max_log_bytes - parsed_bytes
                selected = chunk[:remaining]
                parsed_bytes += len(selected)
                if len(selected) < len(chunk):
                    accumulator.parse_truncated = True
                combined = pending + selected
                lines = combined.split(b"\n")
                pending = lines.pop()
                for line in lines:
                    accumulator.feed(
                        line.decode("utf-8", errors="replace") + "\n"
                    )
            if pending and not accumulator.parse_truncated:
                accumulator.feed(pending.decode("utf-8", errors="replace"))
        return relative, digest.hexdigest()

    @staticmethod
    def _plan_sha256(plan: ExecutionPlan) -> str:
        payload = json.dumps(
            plan.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(payload).hexdigest()

    @staticmethod
    def _attempt_identity(case_root: Path) -> tuple[str, int]:
        attempt_root = case_root.parent
        match = re.fullmatch(r"attempt-(\d+)", attempt_root.name)
        return (
            attempt_root.parent.name or "unknown-run",
            int(match.group(1)) if match is not None else 1,
        )

    @staticmethod
    def _outputs(case_root: Path) -> tuple[tuple[float, ...], tuple[str, ...]]:
        directories: list[tuple[float, Path]] = []
        for candidate in case_root.iterdir():
            if not candidate.is_dir():
                continue
            try:
                value = float(candidate.name)
            except ValueError:
                continue
            if value >= 0:
                directories.append((value, candidate))
        directories.sort(key=lambda item: item[0])
        files = tuple(
            path.relative_to(case_root).as_posix()
            for _, directory in directories
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        )
        return tuple(value for value, _ in directories), files

    def extract(
        self,
        run_result: PlanRunResult,
        plan: ExecutionPlan,
        case_root: Path,
    ) -> RunFacts:
        case = case_root.resolve()
        if run_result.case_dir.resolve() != case:
            raise EvidenceExtractionError(
                "CASE_ROOT_MISMATCH", "run result and extraction root differ"
            )
        commands = {item.step_id: item for item in plan.commands}
        raw_steps: list[RawCommandEvidence] = []
        mesh_checks: list[MeshCheckFact] = []
        progress: list[SolverProgressFact] = []
        residuals: list[ResidualFact] = []
        continuity: list[ContinuityFact] = []
        courant: list[CourantFact] = []
        errors: list[NativeErrorFact] = []
        field_operations: list[FieldOperationFact] = []
        sources: dict[str, str] = {}

        for result_step in run_result.steps:
            command = commands.get(result_step.step_id)
            if command is None:
                raise EvidenceExtractionError(
                    "PLAN_STEP_MISSING",
                    f"result step is absent from plan: {result_step.step_id}",
                )
            accumulator = _StepAccumulator(
                step_id=result_step.step_id,
                stage=str(command.stage),
            )
            stdout_path, stdout_hash = self._read_once(
                result_step.stdout_path, case, accumulator
            )
            stderr_path, stderr_hash = self._read_once(
                result_step.stderr_path, case, accumulator
            )
            sources[stdout_path] = stdout_hash
            sources[stderr_path] = stderr_hash
            raw_steps.append(
                self._raw_step(
                    result_step,
                    command,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    stdout_hash=stdout_hash,
                    stderr_hash=stderr_hash,
                )
            )
            progress.extend(accumulator.finalized_progress())
            residuals.extend(accumulator.residuals)
            continuity.extend(accumulator.continuity)
            courant.extend(accumulator.courant)
            errors.extend(accumulator.errors)
            field_operations.extend(accumulator.field_operations)
            if str(command.stage) == "check":
                successful = (
                    result_step.return_code == 0
                    and not result_step.timed_out
                    and not result_step.cancelled
                    and not accumulator.errors
                    and not accumulator.parse_truncated
                )
                mesh_checks.append(
                    MeshCheckFact(
                        step_id=result_step.step_id,
                        executed=True,
                        mesh_ok=(
                            successful and accumulator.mesh_ok_marker
                        ),
                        points=accumulator.points,
                        faces=accumulator.faces,
                        cells=accumulator.cells,
                        regions=accumulator.regions,
                        max_non_orthogonality=(
                            accumulator.max_non_orthogonality
                        ),
                        max_skewness=accumulator.max_skewness,
                        negative_volume_cells=(
                            accumulator.negative_volume_cells
                        ),
                        parse_truncated=accumulator.parse_truncated,
                        diagnostics=tuple(accumulator.mesh_diagnostics[:3]),
                    )
                )

        run_id, attempt = self._attempt_identity(case)
        written_times, output_files = self._outputs(case)
        return RunFacts(
            run_id=run_id,
            attempt=attempt,
            plan_sha256=self._plan_sha256(plan),
            extractor_identities={"foundation-10": self.identity},
            raw_steps=tuple(raw_steps),
            mesh_checks=tuple(mesh_checks),
            solver_progress=tuple(progress),
            residuals=tuple(residuals),
            continuity=tuple(continuity),
            courant=tuple(courant),
            native_errors=tuple(errors),
            field_operations=tuple(field_operations),
            reused_steps=tuple(
                ReusedCommandEvidence(
                    step_id=item.step_id,
                    stage=item.stage,
                    executable=item.executable,
                    source_kind=item.source_kind,
                    source_id=item.source_id,
                    reason_codes=tuple(item.reason_codes),
                )
                for item in run_result.reused_steps
            ),
            written_times=written_times,
            output_files=output_files,
            source_sha256=sources,
        )

    @staticmethod
    def _raw_step(
        result: PlanStepResult,
        command: NativeCommand,
        *,
        stdout_path: str,
        stderr_path: str,
        stdout_hash: str,
        stderr_hash: str,
    ) -> RawCommandEvidence:
        return RawCommandEvidence(
            step_id=result.step_id,
            stage=str(command.stage),
            executable=command.executable,
            argv=tuple(result.command),
            return_code=result.return_code,
            started_at=result.started_at,
            finished_at=result.finished_at,
            elapsed_seconds=result.elapsed_seconds,
            timed_out=result.timed_out,
            cancelled=result.cancelled,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            stdout_sha256=stdout_hash,
            stderr_sha256=stderr_hash,
            execution_backend=result.execution_backend,
        )


__all__ = ["OpenFOAM10EvidenceExtractor"]
