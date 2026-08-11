"""Conservative static guard for deciding whether host fallback is allowed."""

from __future__ import annotations

import re
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path

from foampilot.plans import NativeCommand

from .models import ExecutionRiskReport, RiskFinding


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_INCLUDE = re.compile(
    r"#\s*(includeIfPresent|includeEtc|include)\s*[\"<]([^\">]+)[\">]"
)
_DIRECTIVE = re.compile(r"#\s*([A-Za-z_][A-Za-z0-9_]*)")
_LIBS = re.compile(
    r"\b(?:libs|[A-Za-z_][A-Za-z0-9_]*Libs)\s*\((.*?)\)\s*;",
    re.DOTALL | re.IGNORECASE,
)
_LIB_ITEM = re.compile(r'"([^"]+)"|([^\s;()]+)')
_CONTEXT_OVERRIDE_OPTIONS = frozenset(
    {"-case", "--case", "-roots", "--roots", "-hostroots", "--hostroots"}
)
_RECOGNIZED_DIRECTIVES = {
    "include",
    "includeifpresent",
    "includeetc",
    "codestream",
}
_REVIEWED_HOST_EXECUTABLES = frozenset(
    {
        "SRFPimpleFoam",
        "SRFSimpleFoam",
        "blockMesh",
        "buoyantFoam",
        "checkMesh",
        "chtMultiRegionFoam",
        "compressibleInterFoam",
        "decomposePar",
        "denseParticleFoam",
        "driftFluxFoam",
        "dsmcFoam",
        "dsmcInitialise",
        "electrostaticFoam",
        "foamPostProcess",
        "icoFoam",
        "interFoam",
        "mhdFoam",
        "multiphaseEulerFoam",
        "pimpleFoam",
        "pisoFoam",
        "porousSimpleFoam",
        "postProcess",
        "potentialFoam",
        "reactingFoam",
        "reconstructPar",
        "rhoCentralFoam",
        "rhoPimpleFoam",
        "rhoSimpleFoam",
        "scalarTransportFoam",
        "setFields",
        "shallowWaterFoam",
        "simpleFoam",
        "snappyHexMesh",
        "solidDisplacementFoam",
        "solidEquilibriumDisplacementFoam",
        "splitMeshRegions",
        "surfaceCheck",
        "surfaceFeatureExtract",
        "topoSet",
        "twoLiquidMixingFoam",
    }
)
_HIGH_RISK_CODES = frozenset(
    {
        "ABSOLUTE_INCLUDE",
        "CALC_ENTRY",
        "CODED_BOUNDARY",
        "CODED_FUNCTION",
        "CODE_STREAM",
        "COMMAND_LIBRARY_LOAD",
        "COMMAND_DYNAMIC_BEHAVIOR",
        "COMMAND_CONTEXT_OVERRIDE",
        "DYNAMIC_CODE",
        "ENVIRONMENT_INCLUDE",
        "EXTERNAL_INCLUDE",
        "FILE_UPDATE_FUNCTION",
        "INCLUDE_ESCAPES_CASE",
        "PATH_LIBRARY",
        "SYSTEM_CALL_FUNCTION",
        "VARIABLE_LIBRARY",
        "VARIABLE_TYPE",
    }
)
_TEXT_RISK_PATTERNS = (
    (
        "CALC_ENTRY",
        re.compile(r"#\s*calc\b"),
        "#calc delegates to codeStream and may compile generated code",
    ),
    (
        "CODE_STREAM",
        re.compile(r"#\s*codeStream\b"),
        "#codeStream may compile and execute generated code",
    ),
    (
        "CODED_FUNCTION",
        re.compile(r"\btype\s+coded\s*;"),
        "coded function object requires dynamic compilation",
    ),
    (
        "CODED_BOUNDARY",
        re.compile(r"\bcoded(?:FixedValue|Mixed)\b"),
        "coded boundary condition requires dynamic compilation",
    ),
    (
        "DYNAMIC_CODE",
        re.compile(r"\bdynamicCode\b"),
        "dynamicCode entry requires dynamic compilation",
    ),
    (
        "SYSTEM_CALL_FUNCTION",
        re.compile(r"\bsystemCall\b"),
        "systemCall token may expand into a function object that executes host commands",
    ),
    (
        "FILE_UPDATE_FUNCTION",
        re.compile(r"\btimeActivatedFileUpdate\b"),
        "timeActivatedFileUpdate may overwrite arbitrary host files",
    ),
    (
        "VARIABLE_TYPE",
        re.compile(r"\btype\s+\$"),
        "macro-expanded type selection is unsafe for host fallback",
    ),
    (
        "VARIABLE_LIBRARY",
        re.compile(
            r"\b(?:libs|[A-Za-z_][A-Za-z0-9_]*Libs)\s+\$",
            re.IGNORECASE,
        ),
        "macro-expanded dynamic library list is unsafe for host fallback",
    ),
)


def _without_comments(text: str) -> str:
    def preserve_lines(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub(preserve_lines, text))


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _finding(
    code: str,
    relative: str,
    line: int,
    detail: str,
) -> RiskFinding:
    return RiskFinding(code=code, path=relative, line=line, detail=detail)


def _scan_text(
    *,
    text: str,
    path: Path,
    relative: str,
    case_root: Path,
    openfoam_root: Path,
) -> list[RiskFinding]:
    clean = _without_comments(text)
    findings: list[RiskFinding] = []
    for match in _INCLUDE.finditer(clean):
        directive, target = match.groups()
        target_path = Path(target)
        line_number = clean.count("\n", 0, match.start()) + 1
        if "$" in target or target.startswith("~"):
            findings.append(
                _finding(
                    "ENVIRONMENT_INCLUDE",
                    relative,
                    line_number,
                    "environment-expanded include is unsafe for host fallback",
                )
            )
        elif directive == "includeEtc":
            resolved = (openfoam_root / "etc" / target_path).resolve()
            if not _within(resolved, openfoam_root):
                findings.append(
                    _finding(
                        "EXTERNAL_INCLUDE",
                        relative,
                        line_number,
                        "#includeEtc target escapes the verified OpenFOAM root",
                    )
                )
        elif target_path.is_absolute():
            findings.append(
                _finding(
                    "ABSOLUTE_INCLUDE",
                    relative,
                    line_number,
                    "absolute include is unsafe for host fallback",
                )
            )
        else:
            resolved = (path.parent / target_path).resolve()
            if not _within(resolved, case_root):
                findings.append(
                    _finding(
                        "INCLUDE_ESCAPES_CASE",
                        relative,
                        line_number,
                        "relative include escapes the materialized case",
                    )
                )

    for line_number, line in enumerate(clean.splitlines(), start=1):
        for match in _DIRECTIVE.finditer(line):
            name = match.group(1)
            lowered = name.casefold()
            if lowered in _RECOGNIZED_DIRECTIVES:
                continue
            if any(token in lowered for token in ("code", "exec", "include", "load")):
                findings.append(
                    _finding(
                        "UNKNOWN_EXECUTION_DIRECTIVE",
                        relative,
                        line_number,
                        f"unrecognized execution-related directive: #{name}",
                    )
                )

    for code, pattern, detail in _TEXT_RISK_PATTERNS:
        for match in pattern.finditer(clean):
            findings.append(
                _finding(
                    code,
                    relative,
                    clean.count("\n", 0, match.start()) + 1,
                    detail,
                )
            )

    for match in _LIBS.finditer(clean):
        body = match.group(1)
        line_number = clean.count("\n", 0, match.start()) + 1
        for item in _LIB_ITEM.finditer(body):
            library = item.group(1) or item.group(2)
            if "$" in library:
                findings.append(
                    _finding(
                        "VARIABLE_LIBRARY",
                        relative,
                        line_number,
                        "macro-expanded dynamic library is unsafe for host fallback",
                    )
                )
                break
            if "/" in library or "\\" in library:
                findings.append(
                    _finding(
                        "PATH_LIBRARY",
                        relative,
                        line_number,
                        "path-bearing dynamic library is unsafe for host fallback",
                    )
                )
                break
    return findings


def with_command_risk(
    report: ExecutionRiskReport,
    commands: Sequence[NativeCommand],
) -> ExecutionRiskReport:
    """Add typed-plan facts required before an audited host decision."""

    findings = list(report.findings)
    for index, command in enumerate(commands, start=1):
        if command.executable not in _REVIEWED_HOST_EXECUTABLES:
            findings.append(
                _finding(
                    "UNREVIEWED_EXECUTABLE",
                    "<execution-plan>",
                    index,
                    (
                        f"{command.executable} is not in the audited host "
                        "solver/utility allowlist"
                    ),
                )
            )
        for argument in command.args:
            lowered = argument.casefold()
            option = lowered.split("=", 1)[0]
            if option in _CONTEXT_OVERRIDE_OPTIONS:
                findings.append(
                    _finding(
                        "COMMAND_CONTEXT_OVERRIDE",
                        "<execution-plan>",
                        index,
                        "command may select a case or distributed root that was not scanned",
                    )
                )
                break
            if lowered in {"-lib", "--lib", "-libs", "--libs"} or lowered.startswith(
                ("-lib=", "--lib=", "-libs=", "--libs=")
            ):
                findings.append(
                    _finding(
                        "COMMAND_LIBRARY_LOAD",
                        "<execution-plan>",
                        index,
                        "command-line library loading is unsafe for host fallback",
                    )
                )
                break
            if any(
                marker in lowered
                for marker in (
                    "systemcall",
                    "codestream",
                    "dynamiccode",
                    "codedfixedvalue",
                    "codedmixed",
                )
            ):
                findings.append(
                    _finding(
                        "COMMAND_DYNAMIC_BEHAVIOR",
                        "<execution-plan>",
                        index,
                        "command argument requests dynamic or command execution behavior",
                    )
                )
                break
    unique = {
        (item.path, item.line, item.code, item.detail): item
        for item in findings
    }
    ordered = tuple(
        sorted(
            unique.values(),
            key=lambda item: (item.path, item.line, item.code),
        )
    )
    if any(item.code in _HIGH_RISK_CODES for item in ordered):
        level = "high"
    elif ordered:
        level = "unknown"
    else:
        level = "low"
    return report.model_copy(update={"risk_level": level, "findings": ordered})


def scan_execution_risk(
    case_root: str | Path,
    *,
    openfoam_root: Path,
    trusted_readonly_roots: Sequence[Path] = (),
    commands: Sequence[NativeCommand] = (),
) -> ExecutionRiskReport:
    """Scan a materialized case; this is a host-fallback guard, not a proof."""

    del trusted_readonly_roots
    root = Path(case_root).resolve()
    verified_openfoam = openfoam_root.resolve()
    findings: list[RiskFinding] = []
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative_path = path.relative_to(root)
        if ".foampilot" in relative_path.parts:
            continue
        relative = relative_path.as_posix()
        payload = path.read_bytes()
        hashes[relative] = sha256(payload).hexdigest()
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(
            _scan_text(
                text=text,
                path=path,
                relative=relative,
                case_root=root,
                openfoam_root=verified_openfoam,
            )
        )

    ordered = tuple(
        sorted(findings, key=lambda item: (item.path, item.line, item.code))
    )
    if any(item.code in _HIGH_RISK_CODES for item in ordered):
        level = "high"
    elif ordered:
        level = "unknown"
    else:
        level = "low"
    report = ExecutionRiskReport(
        risk_level=level,
        findings=ordered,
        scanned_file_sha256=hashes,
    )
    return with_command_risk(report, commands)
