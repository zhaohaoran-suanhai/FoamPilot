from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from foampilot.evidence import (
    EvidenceExtractionError,
    EvidenceExtractorRegistry,
    OpenFOAM10EvidenceExtractor,
)
from foampilot.manifests import CaseManifest, CaseRegion
from foampilot.plans import ExecutionPlan, GeneratedFile, NativeCommand
from foampilot.runtime import PlanRunResult, PlanStepResult


FIXTURES = Path(__file__).parent / "fixtures/evidence/openfoam10"
_START = datetime(2026, 8, 13, tzinfo=timezone.utc)


def _plan(*, check: bool = False) -> ExecutionPlan:
    command = NativeCommand(
        step_id="check" if check else "solve",
        stage="check" if check else "solve",
        executable="checkMesh" if check else "pisoFoam",
        timeout_seconds=60,
    )
    return ExecutionPlan(
        compiled_from_design_sha256="a" * 64,
        compiler_identities={"test.fixture": "1.0.0/protocol-1"},
        manifest=CaseManifest(
            solver_executable="pisoFoam",
            solver_family="incompressible-laminar",
            regime="transient",
            physics_family="fluid",
            mesh_family="provided" if check else "blockMesh",
            dimensionality="2d",
            regions=[CaseRegion(name="default", kind="fluid", path_prefix="")],
        ),
        files=[
            GeneratedFile(
                path="system/controlDict",
                content="FoamFile{}\napplication pisoFoam;\n",
            )
        ],
        commands=[command],
    )


def _extract(
    tmp_path: Path,
    fixture: str,
    *,
    check: bool = False,
    final_newline: bool = True,
    max_log_bytes: int = 4 * 1024 * 1024,
):
    case = tmp_path / "run-evidence" / "attempt-01" / "case"
    logs = case / ".foampilot/logs"
    logs.mkdir(parents=True)
    source = FIXTURES / fixture
    payload = source.read_bytes()
    if not final_newline:
        payload = payload.rstrip(b"\n")
    stdout = logs / "step.stdout.log"
    stderr = logs / "step.stderr.log"
    stdout.write_bytes(payload)
    stderr.write_bytes(b"")
    now = _START + timedelta(seconds=1)
    step_id = "check" if check else "solve"
    executable = "checkMesh" if check else "pisoFoam"
    result = PlanRunResult(
        case_dir=case,
        steps=[
            PlanStepResult(
                step_id=step_id,
                command=[
                    f"/opt/OpenFOAM/OpenFOAM-10/platforms/bin/{executable}"
                ],
                return_code=0,
                started_at=_START,
                finished_at=now,
                elapsed_seconds=0.75,
                timed_out=False,
                stdout_path=stdout,
                stderr_path=stderr,
                execution_backend="host",
            )
        ],
    )
    return OpenFOAM10EvidenceExtractor(
        max_log_bytes=max_log_bytes
    ).extract(result, _plan(check=check), case)


def test_registry_resolves_only_foundation_v10() -> None:
    extractor = EvidenceExtractorRegistry.first_party().resolve(
        "foundation", "10"
    )
    assert isinstance(extractor, OpenFOAM10EvidenceExtractor)
    with pytest.raises(LookupError):
        EvidenceExtractorRegistry.first_party().resolve("foundation", "13")


def test_extractor_normalizes_absolute_check_mesh_command(
    tmp_path: Path,
) -> None:
    facts = _extract(tmp_path, "absolute-checkmesh.log", check=True)
    mesh = facts.mesh_checks[0]
    assert mesh.executed is True
    assert mesh.mesh_ok is True
    assert mesh.cells == 8
    assert mesh.max_non_orthogonality == 12.5
    assert facts.native_errors == ()
    assert facts.raw_steps[0].executable == "checkMesh"
    assert facts.raw_steps[0].argv[0].startswith("/opt/OpenFOAM/")


def test_extractor_reports_residual_continuity_and_failure_once(
    tmp_path: Path,
) -> None:
    facts = _extract(tmp_path, "diverging-piso.log")
    assert facts.residuals[-1].field == "p"
    assert facts.residuals[-1].simulation_time == 0.2
    assert facts.continuity[-1].cumulative == 20
    assert facts.courant[-1].maximum == 92
    assert [item.code for item in facts.native_errors] == [
        "FLOATING_POINT_EXCEPTION"
    ]
    assert facts.solver_progress[-1].completed_normally is False


def test_extractor_records_normal_end_and_written_outputs(
    tmp_path: Path,
) -> None:
    facts = _extract(tmp_path, "normal-end.log")
    case = tmp_path / "run-evidence/attempt-01/case"
    for path in (case / "0.2/U", case / "0.2/p"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("field\n", encoding="utf-8")
    facts = OpenFOAM10EvidenceExtractor().extract(
        PlanRunResult.model_validate_json(
            (lambda value: value.model_dump_json())(
                PlanRunResult(
                    case_dir=case,
                    steps=[
                        PlanStepResult(
                            step_id="solve",
                            command=["/opt/OpenFOAM/bin/pisoFoam"],
                            return_code=0,
                            started_at=_START,
                            finished_at=_START + timedelta(seconds=1),
                            elapsed_seconds=1.0,
                            timed_out=False,
                            stdout_path=case / ".foampilot/logs/step.stdout.log",
                            stderr_path=case / ".foampilot/logs/step.stderr.log",
                            execution_backend="host",
                        )
                    ],
                )
            )
        ),
        _plan(),
        case,
    )
    assert facts.solver_progress[-1].simulation_time == 0.2
    assert facts.solver_progress[-1].completed_normally is True
    assert facts.written_times == (0.2,)
    assert facts.output_files == ("0.2/U", "0.2/p")


@pytest.mark.parametrize(
    ("fixture", "code"),
    [
        ("non-finite.log", "NON_FINITE_VALUE"),
        ("segmentation-fault.log", "SEGMENTATION_FAULT"),
    ],
)
def test_extractor_normalizes_native_errors(
    tmp_path: Path,
    fixture: str,
    code: str,
) -> None:
    facts = _extract(tmp_path, fixture)
    assert [item.code for item in facts.native_errors] == [code]


def test_extractor_preserves_missing_end_and_truncated_final_line(
    tmp_path: Path,
) -> None:
    missing = _extract(tmp_path / "missing", "no-end.log")
    assert missing.solver_progress[-1].completed_normally is False

    truncated = _extract(
        tmp_path / "line",
        "truncated-final-line.log",
        final_newline=False,
    )
    assert truncated.residuals[-1].field == "p"


def test_extractor_tracks_region_context(tmp_path: Path) -> None:
    facts = _extract(tmp_path, "multiple-regions.log")
    assert [(item.region, item.field) for item in facts.residuals] == [
        ("fluid", "Ux"),
        ("solid", "T"),
    ]


def test_extractor_hashes_exact_sources_and_marks_parse_truncation(
    tmp_path: Path,
) -> None:
    facts = _extract(
        tmp_path,
        "absolute-checkmesh.log",
        check=True,
        max_log_bytes=40,
    )
    source = (
        tmp_path
        / "run-evidence/attempt-01/case/.foampilot/logs/step.stdout.log"
    )
    relative = ".foampilot/logs/step.stdout.log"
    assert facts.source_sha256[relative] == sha256(source.read_bytes()).hexdigest()
    assert facts.mesh_checks[0].parse_truncated is True
    assert facts.mesh_checks[0].mesh_ok is not True


def test_extractor_rejects_compressed_or_external_logs(tmp_path: Path) -> None:
    case = tmp_path / "run-evidence/attempt-01/case"
    case.mkdir(parents=True)
    compressed = case / "solve.log.gz"
    compressed.write_bytes(b"compressed")
    result = PlanRunResult(
        case_dir=case,
        steps=[
            PlanStepResult(
                step_id="solve",
                command=["pisoFoam"],
                return_code=1,
                started_at=_START,
                finished_at=_START,
                elapsed_seconds=0,
                timed_out=False,
                stdout_path=compressed,
                stderr_path=case / "stderr.log",
                execution_backend="host",
            )
        ],
    )
    (case / "stderr.log").write_bytes(b"")
    with pytest.raises(EvidenceExtractionError, match="COMPRESSED_LOG"):
        OpenFOAM10EvidenceExtractor().extract(result, _plan(), case)

    outside = tmp_path / "outside.log"
    outside.write_bytes(b"End\n")
    result.steps[0].stdout_path = outside
    with pytest.raises(EvidenceExtractionError, match="LOG_OUTSIDE_CASE"):
        OpenFOAM10EvidenceExtractor().extract(result, _plan(), case)
