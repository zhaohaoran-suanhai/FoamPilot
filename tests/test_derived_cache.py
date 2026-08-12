from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from foampilot.agent import NativeAgent
from foampilot.artifacts import ArtifactStore
from foampilot.performance import DerivedCache, geometry_cache_key
from foampilot.plans import GeneratedFile, NativeCommand
from foampilot.preprocessing import probe_geometry
from foampilot.runtime import PlanRunResult, PlanStepResult

from tests.test_geometry_probe import (
    _asset_root as geometry_asset_root,
    _task as geometry_task,
)
from tests.test_native_agent_state_machine import _runtime_config
from tests.test_native_case_generation import (
    RecordingModel,
    _environment,
    _plan,
    _task,
)
from tests.support.runtime import synthetic_execution_evidence


class PolyMeshRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(
        self,
        *,
        case_dir,
        commands,
        budget,
        risk_report,
        protected_paths,
        execution_seconds_used=0.0,
    ):
        del budget, risk_report, execution_seconds_used
        case = Path(case_dir)
        self.calls.append([item.executable for item in commands])
        log_dir = case / ".foampilot/logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        steps = []
        for index, command in enumerate(commands, start=1):
            if command.stage == "mesh":
                poly_mesh = case / "constant/polyMesh"
                poly_mesh.mkdir(parents=True, exist_ok=True)
                (poly_mesh / "points").write_text("points\n", encoding="utf-8")
                (poly_mesh / "boundary").write_text(
                    "FoamFile{}\n0\n(\n)\n", encoding="utf-8"
                )
            stdout = log_dir / f"{index:02d}-{command.step_id}.stdout.log"
            stderr = log_dir / f"{index:02d}-{command.step_id}.stderr.log"
            if command.executable == "checkMesh":
                stdout.write_text(
                    "points: 8\nfaces: 12\ncells: 4\n"
                    "Number of regions: 1\nMesh OK.\n",
                    encoding="utf-8",
                )
            elif command.stage == "solve":
                stdout.write_text("Time = 1\nEnd\n", encoding="utf-8")
            else:
                stdout.write_text("End\n", encoding="utf-8")
            stderr.write_text("", encoding="utf-8")
            now = datetime.now(timezone.utc)
            steps.append(
                PlanStepResult(
                    step_id=command.step_id,
                    command=[command.executable, *command.args],
                    return_code=0,
                    started_at=now,
                    finished_at=now,
                    elapsed_seconds=0.0,
                    timed_out=False,
                    stdout_path=stdout,
                    stderr_path=stderr,
                    execution_backend="host",
                )
            )
        return PlanRunResult(
            case_dir=case,
            steps=steps,
            **synthetic_execution_evidence(protected_paths),
        )


def _mesh_task():
    payload = _task().model_dump(mode="json")
    payload["mesh"] = {
        "strategy": "blockMesh",
        "quality": {"require_check_mesh_pass": True},
    }
    return _task().model_validate(payload)


def _mesh_plan():
    plan = _plan(
        files=[
            GeneratedFile(
                path="system/blockMeshDict",
                content="FoamFile{}\nconvertToMeters 1;\n",
            )
        ]
    )
    return plan.model_copy(
        update={
            "commands": [
                plan.commands[0],
                NativeCommand(
                    step_id="check-mesh",
                    stage="check",
                    executable="checkMesh",
                    timeout_seconds=30,
                ),
                plan.commands[1],
            ]
        }
    )


def test_geometry_facts_cache_is_content_addressed_and_detects_corruption(
    tmp_path: Path,
) -> None:
    task = geometry_task(
        "closed-tetra.stl",
        format_name="stl",
        length_unit="mm",
    )
    assets = geometry_asset_root(tmp_path, "closed-tetra.stl")
    facts = probe_geometry(task, assets)
    assert facts is not None
    cache = DerivedCache(tmp_path / "cache")
    key = geometry_cache_key(task, assets)

    assert cache.load_geometry(key).status == "miss"
    cache.store_geometry(key, facts)
    hit = cache.load_geometry(key)
    assert hit.status == "hit"
    assert hit.value == facts

    changed = task.model_copy(
        update={
            "geometry": task.geometry.model_copy(
                update={"length_unit": "cm"}
            )
        }
    )
    assert geometry_cache_key(changed, assets) != key

    facts_path = tmp_path / "cache/geometry" / key / "geometry-facts.json"
    facts_path.write_text("{}\n", encoding="utf-8")
    invalid = cache.load_geometry(key)
    assert invalid.status == "miss"
    assert invalid.reason_code == "DERIVED_CACHE_INVALID"
    assert not facts_path.exists()
    assert list((tmp_path / "cache/invalid/geometry").iterdir())


def test_unavailable_cache_store_is_a_non_blocking_miss(
    tmp_path: Path,
) -> None:
    task = geometry_task(
        "closed-tetra.stl",
        format_name="stl",
        length_unit="mm",
    )
    assets = geometry_asset_root(tmp_path, "closed-tetra.stl")
    facts = probe_geometry(task, assets)
    assert facts is not None
    blocked_root = tmp_path / "cache-root-is-a-file"
    blocked_root.write_text("not a directory\n", encoding="utf-8")
    cache = DerivedCache(blocked_root)

    assert cache.store_geometry(geometry_cache_key(task, assets), facts) is False


def test_mesh_cache_skips_generator_but_rechecks_mesh_and_solver(
    tmp_path: Path,
) -> None:
    store = ArtifactStore(tmp_path / "runs")
    cache_root = tmp_path / "cache"
    environment = _environment("blockMesh", "checkMesh", "icoFoam")
    plan = _mesh_plan()
    cold_runner = PolyMeshRunner()
    cold = NativeAgent(
        gateway=RecordingModel([plan]),
        runtime_config=_runtime_config(),
        artifact_store=store,
        environment_snapshot=environment,
        runner=cold_runner,
    ).solve(_mesh_task(), derived_cache=cache_root)

    assert cold.status == "PUBLIC_VALIDATION_PASS"
    assert cold_runner.calls == [["blockMesh", "checkMesh", "icoFoam"]]
    assert list((cache_root / "mesh").iterdir())

    warm_runner = PolyMeshRunner()
    warm = NativeAgent(
        gateway=RecordingModel([plan]),
        runtime_config=_runtime_config(),
        artifact_store=store,
        environment_snapshot=environment,
        runner=warm_runner,
    ).solve(_mesh_task(), derived_cache=cache_root)

    assert warm.status == "PUBLIC_VALIDATION_PASS"
    assert warm_runner.calls == [["checkMesh", "icoFoam"]]
    assert (
        warm.run_dir / "attempt-01/case/constant/polyMesh/points"
    ).is_file()
    risk = json.loads(
        (warm.run_dir / "attempt-01/execution-risk-report.json").read_text(
            encoding="utf-8"
        )
    )
    assert "constant/polyMesh/points" in risk["scanned_file_sha256"]
    run_result = json.loads(
        (warm.run_dir / "attempt-01/run-result.json").read_text(
            encoding="utf-8"
        )
    )
    assert [item["step_id"] for item in run_result["reused_steps"]] == [
        "mesh"
    ]
    execution_reuse = json.loads(
        (warm.run_dir / "attempt-01/execution-reuse.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution_reuse["commands_to_execute"] == [
        "check-mesh",
        "solve",
    ]
    performance = json.loads(
        (warm.run_dir / "performance-summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert performance["path_kind"] == "warm_mesh"
    assert performance["reuse"]["mesh"] == "hit"


def test_mesh_cache_key_changes_when_mesh_dictionary_changes(
    tmp_path: Path,
) -> None:
    from foampilot.performance import mesh_cache_key

    task = _mesh_task()
    environment = _environment("blockMesh", "checkMesh", "icoFoam")
    first = _mesh_plan()
    changed_files = [
        (
            item.model_copy(update={"content": item.content + "// changed\n"})
            if item.path == "system/blockMeshDict"
            else item
        )
        for item in first.files
    ]
    second = first.model_copy(update={"files": changed_files})

    first_key = mesh_cache_key(
        task,
        geometry_facts=None,
        plan=first,
        environment=environment,
        public_asset_root=tmp_path,
    )
    second_key = mesh_cache_key(
        task,
        geometry_facts=None,
        plan=second,
        environment=environment,
        public_asset_root=tmp_path,
    )

    assert first_key.cacheable and second_key.cacheable
    assert first_key.key != second_key.key


def test_provided_mesh_cache_key_does_not_require_a_mesh_command(
    tmp_path: Path,
) -> None:
    from foampilot.performance import mesh_cache_key
    from foampilot.preprocessing import BoundingBox, InputMeshFacts

    payload = _task().model_dump(mode="json")
    payload["mesh"] = {"strategy": "provided"}
    payload["public_assets"] = [
        {
            "path": "mesh/native",
            "sha256": "a" * 64,
            "purpose": "provided mesh",
            "kind": "directory",
            "install_path": "constant/polyMesh",
            "bundle_manifest_sha256": "a" * 64,
        }
    ]
    task = _task().model_validate(payload)
    facts = InputMeshFacts(
        bundle_manifest_sha256="a" * 64,
        inspector_id="foampilot.mesh.poly-mesh",
        inspector_version="1.0.0",
        region=None,
        declared_length_unit="m",
        source_member_sha256={"points": "b" * 64},
        points=8,
        faces=6,
        internal_faces=0,
        cells=1,
        bounding_box_m=BoundingBox(
            minimum=(0, 0, 0),
            maximum=(1, 1, 1),
        ),
        patches=(),
        cell_zones=(),
        face_zones=(),
        point_zones=(),
        dimensionality_observations=(),
        topology_observations=(),
        warnings=(),
    )
    plan = _plan().model_copy(
        update={
            "commands": [
                command
                for command in _plan().commands
                if command.stage != "mesh"
            ]
        }
    )

    key = mesh_cache_key(
        task,
        geometry_facts=None,
        input_mesh_facts=facts,
        plan=plan,
        environment=_environment("checkMesh", "icoFoam"),
        public_asset_root=tmp_path,
    )

    assert key.cacheable is True
    assert key.key is not None
    assert key.reason_code is None
