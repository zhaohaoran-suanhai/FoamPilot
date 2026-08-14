"""System-owned Foundation OpenFOAM 10 observation fragments."""

from __future__ import annotations

import json
import math
from collections import deque
from pathlib import Path, PurePosixPath
import re

from pydantic import BaseModel, ConfigDict, field_validator

from foampilot.authoring import CaseAuthoringError, CaseBundle
from foampilot.plans import GeneratedFile, NativeCommand

from .models import ObservationItem, ObservationPlan
from .registry import first_party_observation_registry


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObservationConfigFragment(_StrictFrozenModel):
    path: str
    content: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.parent != PurePosixPath("system")
            or not path.name.startswith("foampilot-observation")
            or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", path.name)
        ):
            raise ValueError("observation config path is outside system ownership")
        return path.as_posix()


class CompiledObservationFragments(_StrictFrozenModel):
    system_files: tuple[ObservationConfigFragment, ...] = ()
    commands: tuple[NativeCommand, ...] = ()
    system_owned_paths: tuple[str, ...] = ()


_RUNTIME_TYPES = {
    "flow_rate": "surfaceFieldValue",
    "pressure_difference": "surfaceFieldValue",
    "region_average": "volFieldValue",
}
_POSTPROCESS_FILE = {
    "flow_rate": "surfaceFieldValue.dat",
    "pressure_difference": "fieldValueDelta.dat",
    "region_average": "volFieldValue.dat",
}
_FOAM_DICTIONARY_HEADER = """FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    \"system\";
    object      %s;
}

"""


def _dictionary_file(path: str, body: str) -> str:
    return _FOAM_DICTIONARY_HEADER % PurePosixPath(path).name + body


def _expected_collector(item: ObservationItem) -> str:
    return f"foundation10.{item.kind}"


def _runtime_block(item: ObservationItem) -> str:
    collector = item.evidence_strategy.collector_id
    if collector != _expected_collector(item) or item.kind not in _RUNTIME_TYPES:
        raise ValueError(
            f"OBSERVATION_COLLECTOR_UNSUPPORTED: {collector or item.kind}"
        )
    return _function_block(item, runtime=True)


def _field_name(item: ObservationItem) -> str:
    descriptor = first_party_observation_registry().resolve(item.kind)
    contract = descriptor.resolve_quantity_contract(
        item.quantity,
        item.dimension,
    )
    if contract is None:
        raise ValueError(
            "OBSERVATION_QUANTITY_UNSUPPORTED: "
            f"{item.quantity}:{item.dimension}"
        )
    return contract.field


def _function_block(item: ObservationItem, *, runtime: bool) -> str:
    """Render a complete, placeholder-free Foundation v10 function object."""

    control = (
        "    writeControl timeStep;\n    writeInterval 1;\n"
        if runtime
        else "    writeControl writeTime;\n"
    )
    common = (
        '    libs        ("libfieldFunctionObjects.so");\n'
        + control
        + "    writeFields false;\n"
    )
    region_line = (
        f"    region      {item.scope.region};\n"
        if item.scope.region is not None
        else ""
    )
    if item.kind == "flow_rate" and item.scope.kind == "patch":
        return (
            f"{item.observation_id}\n{{\n"
            "    type        surfaceFieldValue;\n"
            + common
            + region_line
            + "    regionType  patch;\n"
            f"    name        {item.scope.names[0]};\n"
            "    operation   sum;\n"
            "    fields      (phi);\n"
            "}\n"
        )
    if item.kind == "pressure_difference" and item.scope.kind == "patch_pair":
        first, second = item.scope.names
        region = lambda name: (
            "    {\n"
            "        type        surfaceFieldValue;\n"
            '        libs        ("libfieldFunctionObjects.so");\n'
            "        writeFields false;\n"
            "        regionType  patch;\n"
            f"        name        {name};\n"
            "        operation   areaAverage;\n"
            "        fields      (p);\n"
            "    }\n"
        )
        return (
            f"{item.observation_id}\n{{\n"
            "    type        fieldValueDelta;\n"
            '    libs        ("libfieldFunctionObjects.so");\n'
            + control
            + region_line
            + "    operation   subtract;\n"
            + "    region1\n"
            + region(first)
            + "    region2\n"
            + region(second)
            + "}\n"
        )
    if item.kind == "region_average" and item.scope.kind in {"cell_zone", "region"}:
        source = "cellZone" if item.scope.kind == "cell_zone" else "all"
        name_line = (
            f"    name        {item.scope.names[0]};\n"
            if source == "cellZone"
            else ""
        )
        return (
            f"{item.observation_id}\n{{\n"
            "    type        volFieldValue;\n"
            + common
            + region_line
            + f"    regionType  {source};\n"
            + name_line
            + "    operation   volAverage;\n"
            + f"    fields      ({_field_name(item)});\n"
            + "}\n"
        )
    raise ValueError(
        f"OBSERVATION_TEMPLATE_UNSUPPORTED: {item.kind}:{item.scope.kind}"
    )


def _postprocess_path(item: ObservationItem) -> str:
    return f"system/foampilot-observation-{item.observation_id}"


def _postprocess_file(item: ObservationItem) -> ObservationConfigFragment:
    return ObservationConfigFragment(
        path=_postprocess_path(item),
        content=_dictionary_file(
            _postprocess_path(item),
            "functions\n{\n" + _function_block(item, runtime=False) + "}\n",
        ),
    )


def _postprocess_command(
    item: ObservationItem,
    *,
    timeout_seconds: int,
) -> NativeCommand:
    collector = item.evidence_strategy.collector_id
    if collector != _expected_collector(item) or item.kind not in _POSTPROCESS_FILE:
        raise ValueError(
            f"OBSERVATION_COLLECTOR_UNSUPPORTED: {collector or item.kind}"
        )
    region_args = (
        ["-region", item.scope.region]
        if item.scope.region is not None
        else []
    )
    time = item.time_selection
    time_args = (
        ["-time", f"{time.start:g}:{time.end:g}"]
        if time.kind == "time_range"
        else ["-latestTime"]
    )
    return NativeCommand(
        step_id=f"observe-{item.observation_id}".replace(".", "-").replace("_", "-"),
        stage="postprocess",
        executable="postProcess",
        args=["-dict", _postprocess_path(item), *region_args, *time_args],
        mpi_ranks=1,
        timeout_seconds=timeout_seconds,
    )


def compile_foundation10_observations(
    plan: ObservationPlan,
    *,
    postprocess_timeout_seconds: int = 20,
) -> CompiledObservationFragments:
    if postprocess_timeout_seconds < 1:
        raise ValueError("OBSERVATION_TIMEOUT_INVALID")
    blocks: list[str] = []
    files: list[ObservationConfigFragment] = []
    commands: list[NativeCommand] = []
    for item in plan.items:
        strategy = item.evidence_strategy.kind
        if strategy == "runtime_configuration":
            blocks.append(_runtime_block(item))
        elif strategy == "postprocess_command":
            files.append(_postprocess_file(item))
            commands.append(
                _postprocess_command(
                    item,
                    timeout_seconds=postprocess_timeout_seconds,
                )
            )
    if blocks:
        files.insert(
            0,
            ObservationConfigFragment(
                path="system/foampilot-observations",
                content=_dictionary_file(
                    "system/foampilot-observations",
                    "functions\n{\n" + "\n".join(blocks) + "}\n",
                ),
            ),
        )
    return CompiledObservationFragments(
        system_files=tuple(files),
        commands=tuple(commands),
        system_owned_paths=tuple(item.path for item in files),
    )


def _dictionary_tokens(text: str):
    """Yield identifiers and braces outside comments and quoted strings."""

    index = 0
    while index < len(text):
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline + 1
            continue
        if text.startswith("/*", index):
            closing = text.find("*/", index + 2)
            index = len(text) if closing < 0 else closing + 2
            continue
        character = text[index]
        if character in {'"', "'"}:
            quote = character
            index += 1
            while index < len(text):
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        if character in "{}":
            yield character
            index += 1
            continue
        identifier = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[index:])
        if identifier is not None:
            yield identifier.group(0)
            index += len(identifier.group(0))
            continue
        index += 1


def _has_top_level_functions_dictionary(text: str) -> bool:
    tokens = tuple(_dictionary_tokens(text))
    depth = 0
    for index, token in enumerate(tokens):
        if (
            depth == 0
            and token == "functions"
            and index + 1 < len(tokens)
            and tokens[index + 1] == "{"
        ):
            return True
        if token == "{":
            depth += 1
        elif token == "}" and depth > 0:
            depth -= 1
    return False


def inject_observation_fragments(
    bundle: CaseBundle,
    plan: ObservationPlan,
) -> tuple[CaseBundle, CompiledObservationFragments]:
    authored_function_files = sorted(
        item.path
        for item in bundle.files
        if _has_top_level_functions_dictionary(item.content)
    )
    if authored_function_files:
        raise CaseAuthoringError(
            "OBSERVATION_FUNCTIONS_OWNERSHIP_COLLISION: "
            + ", ".join(authored_function_files)
        )
    fragments = compile_foundation10_observations(plan)
    existing = {item.path for item in bundle.files}
    collisions = sorted(existing & set(fragments.system_owned_paths))
    if collisions:
        raise CaseAuthoringError(
            "OBSERVATION_SYSTEM_PATH_COLLISION: " + ", ".join(collisions)
        )
    files = list(bundle.files)
    if fragments.system_files:
        if "system/foampilot-observations" in fragments.system_owned_paths:
            control_index = next(
                (
                    index
                    for index, item in enumerate(files)
                    if item.path == "system/controlDict"
                ),
                None,
            )
            if control_index is None:
                raise CaseAuthoringError("OBSERVATION_CONTROL_DICT_MISSING")
            control = files[control_index]
            if "foampilot-observations" in control.content:
                raise CaseAuthoringError("OBSERVATION_INCLUDE_COLLISION")
            files[control_index] = control.model_copy(
                update={
                    "content": control.content.rstrip()
                    + '\n#include "foampilot-observations"\n'
                }
            )
        files.extend(
            GeneratedFile(path=item.path, content=item.content)
            for item in fragments.system_files
        )
    return bundle.model_copy(update={"files": files}), fragments


_SCALAR_TOKEN = re.compile(
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
)


def _parse_value(value: str) -> float | tuple[float, ...]:
    stripped = value.strip()
    if stripped.startswith("("):
        if not stripped.endswith(")"):
            raise ValueError("OBSERVATION_OUTPUT_VALUE_INVALID")
        tokens = stripped[1:-1].split()
    else:
        tokens = [stripped]
    if not tokens or any(_SCALAR_TOKEN.fullmatch(item) is None for item in tokens):
        raise ValueError("OBSERVATION_OUTPUT_VALUE_INVALID")
    numbers = tuple(float(item) for item in tokens)
    if any(not math.isfinite(item) for item in numbers):
        raise ValueError("OBSERVATION_OUTPUT_VALUE_NONFINITE")
    if stripped.startswith("("):
        if not numbers:
            raise ValueError("OBSERVATION_OUTPUT_VALUE_INVALID")
        return numbers
    if len(numbers) != 1:
        raise ValueError("OBSERVATION_OUTPUT_VALUE_INVALID")
    return numbers[0]


_MAX_OUTPUT_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_OUTPUT_BYTES = 64 * 1024 * 1024
_MAX_OUTPUT_LINES = 1_000_000
_MAX_CANDIDATE_FILES = 4096
_MAX_FIELD_HEADER_BYTES = 64 * 1024


def _read_samples(path: Path):
    if path.stat().st_size > _MAX_OUTPUT_BYTES:
        raise ValueError(f"OBSERVATION_OUTPUT_TOO_LARGE: {path.name}")
    found = False
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if line_number > _MAX_OUTPUT_LINES:
                raise ValueError(f"OBSERVATION_OUTPUT_TOO_MANY_LINES: {path.name}")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            pieces = stripped.split(maxsplit=1)
            if len(pieces) != 2:
                continue
            try:
                time = float(pieces[0])
                value = _parse_value(pieces[1])
            except ValueError as error:
                raise ValueError(
                    f"OBSERVATION_OUTPUT_PARSE_FAILED: {path.name}"
                ) from error
            if not math.isfinite(time) or time < 0:
                raise ValueError(
                    f"OBSERVATION_OUTPUT_PARSE_FAILED: {path.name}"
                )
            found = True
            yield {"time": time, "value": value}
    if not found:
        raise ValueError(f"OBSERVATION_OUTPUT_EMPTY: {path.name}")


def _candidate_sort_key(path: Path) -> tuple[float, str]:
    try:
        return float(path.parent.name), path.as_posix()
    except ValueError:
        return float("inf"), path.as_posix()


def _is_safe_output(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root)
    except (FileNotFoundError, ValueError):
        return False
    current = path
    while current != root:
        if current.is_symlink():
            return False
        current = current.parent
    return True


def _sample_in_range(item: ObservationItem, sample: dict[str, object]) -> bool:
    time = item.time_selection
    if time.kind == "time_range":
        assert time.start is not None and time.end is not None
        return time.start <= float(sample["time"]) <= time.end
    return True


def _metric_unit(item: ObservationItem) -> str:
    descriptor = first_party_observation_registry().resolve(item.kind)
    contract = descriptor.resolve_quantity_contract(
        item.quantity,
        item.dimension,
    )
    if contract is None:
        raise ValueError(
            "OBSERVATION_QUANTITY_UNSUPPORTED: "
            f"{item.quantity}:{item.dimension}"
        )
    return contract.unit


def _verify_observation_field_dimensions(
    case_root: str | Path,
    item: ObservationItem,
) -> None:
    """Verify bounded written-field headers before labeling observation values."""

    root = Path(case_root).resolve()
    dimension_pattern = re.compile(r"(?m)^\s*dimensions\s+\[([^]]+)\]\s*;")
    descriptor = first_party_observation_registry().resolve(item.kind)
    contract = descriptor.resolve_quantity_contract(
        item.quantity,
        item.dimension,
    )
    if contract is None:
        return
    for field_name, expected_dimension in contract.evidence_field_dimensions:
        base = root
        if item.scope.region is not None and (root / item.scope.region).is_dir():
            base = root / item.scope.region
        candidates: list[tuple[float, Path]] = []
        if base.is_dir() and _is_safe_output(base, root):
            for directory in base.iterdir():
                try:
                    simulation_time = float(directory.name)
                except ValueError:
                    continue
                field = directory / field_name
                if item.scope.region is not None and base == root:
                    regional = directory / item.scope.region / field_name
                    if regional.is_file():
                        field = regional
                if field.is_file() and _is_safe_output(field, root):
                    candidates.append((simulation_time, field))
        if not candidates:
            raise ValueError(
                "OBSERVATION_FIELD_HEADER_MISSING: "
                f"{item.scope.region or 'default'}:{field_name}"
            )
        field = max(candidates, key=lambda candidate: candidate[0])[1]
        with field.open("rb") as stream:
            header = stream.read(_MAX_FIELD_HEADER_BYTES).decode(
                "utf-8",
                errors="replace",
            )
        match = dimension_pattern.search(header)
        observed = " ".join(match.group(1).split()) if match is not None else None
        if observed != expected_dimension:
            raise ValueError(
                "OBSERVATION_FIELD_DIMENSION_MISMATCH: "
                f"{field_name} expected [{expected_dimension}], got [{observed}]"
            )


def audit_observation_field_dimensions(
    case_root: str | Path,
    plan: ObservationPlan,
) -> dict[str, str]:
    """Return per-observation field-semantic failures without erasing siblings."""

    failures: dict[str, str] = {}
    for item in plan.items:
        if item.evidence_strategy.kind in {"unavailable", "run_facts"}:
            continue
        try:
            _verify_observation_field_dimensions(case_root, item)
        except (OSError, UnicodeError, ValueError) as error:
            failures[item.observation_id] = str(error)
    return failures


def verify_observation_field_dimensions(
    case_root: str | Path,
    plan: ObservationPlan,
) -> None:
    """Compatibility fail-fast wrapper over the per-observation field audit."""

    failures = audit_observation_field_dimensions(case_root, plan)
    if failures:
        observation_id = sorted(failures)[0]
        raise ValueError(f"{observation_id}: {failures[observation_id]}")


def collect_foundation10_observation_artifacts(
    case_root: str | Path,
    plan: ObservationPlan,
) -> dict[str, Path]:
    """Normalize declared Foundation output tables without reading solver logs."""

    root = Path(case_root).resolve()
    destination = root / ".foampilot/observations"
    if (
        (root / ".foampilot").is_symlink()
        or destination.is_symlink()
        or (
        destination.exists() and not _is_safe_output(destination, root)
        )
    ):
        raise ValueError("OBSERVATION_DESTINATION_UNSAFE")
    result: dict[str, Path] = {}
    for item in plan.items:
        filename = _POSTPROCESS_FILE.get(item.kind)
        if filename is None or item.evidence_strategy.kind not in {
            "postprocess_command",
            "runtime_configuration",
        }:
            continue
        output_root = root / "postProcessing"
        if item.scope.region is not None:
            output_root /= item.scope.region
        output_root /= item.observation_id
        candidates = []
        discovery_error = None
        if output_root.is_dir() and _is_safe_output(output_root, root):
            for index, directory in enumerate(output_root.iterdir(), start=1):
                if index > _MAX_CANDIDATE_FILES:
                    candidates = []
                    discovery_error = "OBSERVATION_OUTPUT_FILE_LIMIT_EXCEEDED"
                    break
                candidate = directory / filename
                if candidate.is_file() and _is_safe_output(candidate, root):
                    candidates.append(candidate)
        candidates.sort(key=_candidate_sort_key)
        if candidates and sum(path.stat().st_size for path in candidates) > _MAX_TOTAL_OUTPUT_BYTES:
            candidates = []
            discovery_error = "OBSERVATION_OUTPUT_TOTAL_SIZE_EXCEEDED"
        if not candidates and discovery_error is None:
            continue
        samples = deque(maxlen=1000)
        error_detail = discovery_error
        latest_time: float | None = None
        truncated = False
        try:
            for path in candidates:
                for sample in _read_samples(path):
                    if not _sample_in_range(item, sample):
                        continue
                    sample_time = float(sample["time"])
                    if item.time_selection.kind in {"latest", "final"}:
                        if latest_time is None or sample_time > latest_time:
                            samples.clear()
                            latest_time = sample_time
                            truncated = False
                        if sample_time == latest_time:
                            truncated = truncated or len(samples) == samples.maxlen
                            samples.append(sample)
                    else:
                        truncated = truncated or len(samples) == samples.maxlen
                        samples.append(sample)
        except (OSError, UnicodeError, ValueError) as error:
            error_detail = str(error)
        bounded = list(samples) if error_detail is None else []
        destination.mkdir(parents=True, exist_ok=True)
        artifact = destination / f"{item.observation_id}.json"
        if artifact.is_symlink() or (
            artifact.exists() and not _is_safe_output(artifact, root)
        ):
            raise ValueError("OBSERVATION_DESTINATION_UNSAFE")
        payload = (
            {
                "unit": _metric_unit(item),
                "samples": bounded,
                **(
                    {
                        "status": "PARTIAL",
                        "detail": "bounded projection retained the latest 1000 samples",
                    }
                    if truncated
                    else {}
                ),
            }
            if bounded
            else {
                "status": "UNAVAILABLE",
                "detail": error_detail or "declared time selection has no samples",
            }
        )
        artifact.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        result[item.observation_id] = artifact
    return result


__all__ = [
    "CompiledObservationFragments",
    "ObservationConfigFragment",
    "audit_observation_field_dimensions",
    "collect_foundation10_observation_artifacts",
    "compile_foundation10_observations",
    "inject_observation_fragments",
    "verify_observation_field_dimensions",
]
