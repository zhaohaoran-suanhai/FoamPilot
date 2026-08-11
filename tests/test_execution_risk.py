from __future__ import annotations

from pathlib import Path

import pytest

from foampilot.plans import NativeCommand
from foampilot.runtime.models import (
    ExecutionRiskReport,
    RuntimeConfig,
    SandboxProbe,
)
from foampilot.runtime.policy import decide_execution_policy
from foampilot.runtime.risk import scan_execution_risk


def _case(tmp_path: Path, relative: str, content: str) -> Path:
    case = tmp_path / "case"
    target = case / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return case


@pytest.mark.parametrize(
    ("relative", "content", "code"),
    [
        (
            "system/controlDict",
            "functions { x { type coded; code #{ int x; #}; } }",
            "CODED_FUNCTION",
        ),
        (
            "system/controlDict",
            "#codeStream { code #{ int x; #}; }",
            "CODE_STREAM",
        ),
        ("system/controlDict", '#calc "1 + 1"', "CALC_ENTRY"),
        ("0/U", "type codedFixedValue;", "CODED_BOUNDARY"),
        ("system/controlDict", '#include "/tmp/foreign"', "ABSOLUTE_INCLUDE"),
        (
            "system/controlDict",
            '#include "$HOME/.OpenFOAM/10/prefs"',
            "ENVIRONMENT_INCLUDE",
        ),
        (
            "system/controlDict",
            '#include "$FOAM_CASE/../../tmp/evil"',
            "ENVIRONMENT_INCLUDE",
        ),
        (
            "system/controlDict",
            '#include "$FOAM_TUTORIALS/incompressible/icoFoam/cavity"',
            "ENVIRONMENT_INCLUDE",
        ),
        (
            "system/controlDict",
            'foreign "../../tmp/evil";\n#include "$foreign"',
            "ENVIRONMENT_INCLUDE",
        ),
        (
            "system/controlDict",
            '#include\n "/tmp/foreign"',
            "ABSOLUTE_INCLUDE",
        ),
        (
            "system/controlDict",
            '#include "../../foreign"',
            "INCLUDE_ESCAPES_CASE",
        ),
        (
            "system/controlDict",
            'libs ("/tmp/libevil.so");',
            "PATH_LIBRARY",
        ),
        (
            "system/controlDict",
            'functionObjectLibs ("/tmp/libevil.so");',
            "PATH_LIBRARY",
        ),
        (
            "system/controlDict",
            "functions { shell { type systemCall; executeCalls (\"id\"); } }",
            "SYSTEM_CALL_FUNCTION",
        ),
        (
            "system/controlDict",
            "functions { update { type timeActivatedFileUpdate; "
            'fileToUpdate "/home/user/.bashrc"; } }',
            "FILE_UPDATE_FUNCTION",
        ),
        (
            "system/controlDict",
            "danger timeActivatedFileUpdate; functions { update { type $danger; } }",
            "FILE_UPDATE_FUNCTION",
        ),
        (
            "system/controlDict",
            "danger systemCall; functions { shell { type $danger; } }",
            "SYSTEM_CALL_FUNCTION",
        ),
        (
            "system/controlDict",
            "danger coded; functions { generated { type $danger; } }",
            "VARIABLE_TYPE",
        ),
        (
            "system/controlDict",
            'libPath "/tmp/libevil.so"; libs ($libPath);',
            "VARIABLE_LIBRARY",
        ),
        (
            "system/controlDict",
            'evil ("/tmp/libevil.so"); libs $evil;',
            "VARIABLE_LIBRARY",
        ),
        (
            "system/controlDict",
            "functions { shell { type\n systemCall; executeCalls (\"id\"); } }",
            "SYSTEM_CALL_FUNCTION",
        ),
        (
            "system/controlDict",
            "functions { generated { type\n coded; code #{ int x; #}; } }",
            "CODED_FUNCTION",
        ),
    ],
)
def test_risk_scanner_marks_host_unsafe_constructs(
    tmp_path: Path,
    relative: str,
    content: str,
    code: str,
) -> None:
    case = _case(tmp_path, relative, content)

    report = scan_execution_risk(
        case,
        openfoam_root=tmp_path / "OpenFOAM-10",
    )

    assert report.risk_level == "high"
    assert code in {finding.code for finding in report.findings}


def test_unknown_execution_directive_blocks_host(tmp_path: Path) -> None:
    case = _case(tmp_path, "system/controlDict", "#unknownExec foo;")

    report = scan_execution_risk(
        case,
        openfoam_root=tmp_path / "OpenFOAM-10",
    )

    assert report.risk_level == "unknown"
    assert report.findings[0].code == "UNKNOWN_EXECUTION_DIRECTIVE"


@pytest.mark.parametrize("executable", ["wmake", "python3", "foamExec"])
def test_unreviewed_executable_is_unknown_host_risk(
    tmp_path: Path,
    executable: str,
) -> None:
    case = _case(tmp_path, "system/controlDict", "application icoFoam;\n")

    report = scan_execution_risk(
        case,
        openfoam_root=tmp_path / "OpenFOAM-10",
        commands=(
            NativeCommand(
                step_id="unreviewed",
                stage="solve",
                executable=executable,
                timeout_seconds=30,
            ),
        ),
    )

    assert report.risk_level == "unknown"
    assert report.findings[-1].code == "UNREVIEWED_EXECUTABLE"
    assert report.findings[-1].path == "<execution-plan>"


def test_reviewed_solver_command_keeps_low_risk_case_low(tmp_path: Path) -> None:
    case = _case(tmp_path, "system/controlDict", "application icoFoam;\n")

    report = scan_execution_risk(
        case,
        openfoam_root=tmp_path / "OpenFOAM-10",
        commands=(
            NativeCommand(
                step_id="solve",
                stage="solve",
                executable="icoFoam",
                timeout_seconds=30,
            ),
        ),
    )

    assert report.risk_level == "low"


@pytest.mark.parametrize(
    "args",
    [["-lib", "libcustom.so"], ["-func", "systemCall(command=id)"]],
)
def test_reviewed_command_with_dynamic_loader_argument_is_high_risk(
    tmp_path: Path,
    args: list[str],
) -> None:
    case = _case(tmp_path, "system/controlDict", "application icoFoam;\n")

    report = scan_execution_risk(
        case,
        openfoam_root=tmp_path / "OpenFOAM-10",
        commands=(
            NativeCommand(
                step_id="solve",
                stage="solve",
                executable="icoFoam",
                args=args,
                timeout_seconds=30,
            ),
        ),
    )

    assert report.risk_level == "high"


@pytest.mark.parametrize(
    "args",
    [
        ["-case", "/tmp/unscanned-case"],
        ["-case=/tmp/unscanned-case"],
        ["-roots", "other-root"],
        ["-hostRoots=host-a:/tmp/unscanned-case"],
    ],
)
def test_reviewed_command_with_case_context_override_is_high_risk(
    tmp_path: Path,
    args: list[str],
) -> None:
    case = _case(tmp_path, "system/controlDict", "application icoFoam;\n")

    report = scan_execution_risk(
        case,
        openfoam_root=tmp_path / "OpenFOAM-10",
        commands=(
            NativeCommand(
                step_id="solve",
                stage="solve",
                executable="icoFoam",
                args=args,
                timeout_seconds=30,
            ),
        ),
    )

    assert report.risk_level == "high"
    assert "COMMAND_CONTEXT_OVERRIDE" in {
        finding.code for finding in report.findings
    }


def test_comments_and_case_local_include_remain_low_risk(tmp_path: Path) -> None:
    case = _case(
        tmp_path,
        "system/controlDict",
        "// #codeStream ignored\n"
        "/* type coded; */\n"
        '#include "local.inc"\n',
    )
    (case / "system/local.inc").write_text("value 1;\n", encoding="utf-8")

    first = scan_execution_risk(
        case,
        openfoam_root=tmp_path / "OpenFOAM-10",
    )
    second = scan_execution_risk(
        case,
        openfoam_root=tmp_path / "OpenFOAM-10",
    )

    assert first.risk_level == "low"
    assert first.model_dump_json() == second.model_dump_json()
    assert set(first.scanned_file_sha256) == {
        "system/controlDict",
        "system/local.inc",
    }


def _probe(
    ok: bool,
    *,
    failure_code: str | None = None,
) -> SandboxProbe:
    return SandboxProbe(
        status="passed" if ok else "failed",
        ok=ok,
        builder_sha256="a" * 64 if ok else None,
        namespace_flags=("unshare-net", "unshare-pid"),
        mount_count=8 if ok else 0,
        protected_path_count=0,
        failure_code=failure_code,
        return_code=0 if ok else 1,
        detail="ok" if ok else "Operation not permitted",
    )


@pytest.mark.parametrize(
    ("isolation", "probe_ok", "risk", "opt_in", "backend", "code"),
    [
        (
            "sandbox_required",
            True,
            "high",
            False,
            "bubblewrap",
            "SANDBOX_SELECTED",
        ),
        (
            "sandbox_required",
            False,
            "low",
            False,
            None,
            "SANDBOX_REQUIRED_UNAVAILABLE",
        ),
        (
            "sandbox_preferred",
            True,
            "unknown",
            False,
            "bubblewrap",
            "SANDBOX_SELECTED",
        ),
        (
            "sandbox_preferred",
            False,
            "low",
            False,
            "host",
            "HOST_FALLBACK_SELECTED",
        ),
        (
            "sandbox_preferred",
            False,
            "high",
            True,
            None,
            "HOST_DYNAMIC_CODE_BLOCKED",
        ),
        (
            "trusted_host",
            True,
            "low",
            False,
            "host",
            "TRUSTED_HOST_SELECTED",
        ),
        (
            "trusted_host",
            True,
            "high",
            False,
            None,
            "HOST_DYNAMIC_CODE_BLOCKED",
        ),
        (
            "trusted_host",
            True,
            "high",
            True,
            "host",
            "TRUSTED_HOST_DYNAMIC_CODE_OPT_IN",
        ),
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
    probe = _probe(
        probe_ok,
        failure_code=None if probe_ok else "NAMESPACE_UNAVAILABLE",
    )

    decision = decide_execution_policy(config, risk_report, probe)

    assert decision.actual_backend == backend
    assert decision.code == code
    assert decision.allowed is (backend is not None)
    if backend == "host":
        assert decision.unisolated_warning


@pytest.mark.parametrize(
    "failure_code",
    ["SANDBOX_SETUP_FAILED", "TRUSTED_RUNTIME_ROOT_INVALID"],
)
def test_mount_plan_failures_never_fall_back_to_host(
    tmp_path: Path,
    failure_code: str,
) -> None:
    decision = decide_execution_policy(
        RuntimeConfig(
            openfoam_root=tmp_path,
            isolation="sandbox_preferred",
        ),
        ExecutionRiskReport(risk_level="low", scanned_file_sha256={}),
        _probe(False, failure_code=failure_code),
    )

    assert decision.allowed is False
    assert decision.actual_backend is None
    assert decision.code == failure_code
