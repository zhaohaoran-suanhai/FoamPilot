"""Explicit content-addressed caches for public geometry and native meshes."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Generic, Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from foampilot.environment import EnvironmentSnapshot
from foampilot.plans import ExecutionPlan
from foampilot.preprocessing import GeometryFacts, InputMeshFacts, MeshQualityReport
from foampilot.tasks import TaskSpec


GEOMETRY_PROBE_IMPLEMENTATION_VERSION = "1"
DERIVED_CACHE_SCHEMA_VERSION = 1
T = TypeVar("T")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return sha256(_canonical_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_observations(task: TaskSpec, root: Path) -> list[dict[str, str]]:
    directory = root.resolve()
    declared_geometry = {
        item.path: item for item in (task.geometry.assets if task.geometry else [])
    }
    observations: list[dict[str, str]] = []
    for asset in task.public_assets:
        if asset.kind == "directory":
            observations.append(
                {
                    "path": asset.path,
                    "sha256": asset.sha256,
                    "role": (
                        declared_geometry[asset.path].role
                        if asset.path in declared_geometry
                        else "public_asset"
                    ),
                    "format": (
                        declared_geometry[asset.path].format
                        if asset.path in declared_geometry
                        else "other"
                    ),
                }
            )
            continue
        path = (directory / asset.path).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise ValueError(f"public asset is missing: {asset.path}")
        observed = _file_sha256(path)
        if observed != asset.sha256:
            raise ValueError(f"public asset SHA256 mismatch: {asset.path}")
        geometry_ref = declared_geometry.get(asset.path)
        observations.append(
            {
                "path": asset.path,
                "sha256": observed,
                "role": geometry_ref.role if geometry_ref else "public_asset",
                "format": geometry_ref.format if geometry_ref else "other",
            }
        )
    return observations


def geometry_cache_key(task: TaskSpec, asset_root: str | Path) -> str:
    """Hash only declared geometry inputs and their observed bytes."""

    if task.geometry is None:
        raise ValueError("geometry cache key requires TaskSpec.geometry")
    geometry = task.geometry.model_dump(mode="json")
    return _canonical_sha256(
        {
            "schema_version": DERIVED_CACHE_SCHEMA_VERSION,
            "probe_implementation_version": (
                GEOMETRY_PROBE_IMPLEMENTATION_VERSION
            ),
            "geometry": geometry,
            "assets": _asset_observations(task, Path(asset_root)),
        }
    )


@dataclass(frozen=True)
class MeshKeyResult:
    cacheable: bool
    key: str | None
    reason_code: str | None = None


_MESH_FILE_NAMES = {
    "blockMeshDict",
    "snappyHexMeshDict",
    "surfaceFeatureExtractDict",
    "meshQualityDict",
    "topoSetDict",
    "createPatchDict",
    "refineMeshDict",
    "extrudeMeshDict",
    "decomposeParDict",
}
_DYNAMIC_MARKERS = {
    "dynamicMeshDict",
    "dynamicCode",
}
_QUOTED_INCLUDE = re.compile(r'^\s*#include\s+"([^"]+)"', re.MULTILINE)


def _mesh_dependency_files(plan: ExecutionPlan) -> tuple[list[dict[str, str]], str | None]:
    by_path = {item.path: item.content for item in plan.files}
    selected: set[str] = {
        path
        for path in by_path
        if PurePosixPath(path).name in _MESH_FILE_NAMES
        or path.startswith("constant/triSurface/")
        or path.endswith((".geo", ".msh"))
    }
    for command in plan.commands:
        if command.stage != "mesh":
            continue
        for argument in command.args:
            if argument in by_path:
                selected.add(argument)
    if any(
        PurePosixPath(path).name in _DYNAMIC_MARKERS
        for path in by_path
    ):
        return [], "DYNAMIC_MESH_UNCACHEABLE"
    if not selected:
        return [], "MESH_DEPENDENCY_UNRESOLVED"
    pending = list(selected)
    while pending:
        path = pending.pop()
        parent = PurePosixPath(path).parent
        for included in _QUOTED_INCLUDE.findall(by_path[path]):
            candidate = (parent / included).as_posix()
            if candidate not in by_path:
                candidate = PurePosixPath(included).as_posix()
            if candidate not in by_path:
                return [], "MESH_INCLUDE_DEPENDENCY_UNRESOLVED"
            if candidate not in selected:
                selected.add(candidate)
                pending.append(candidate)
    return [
        {"path": path, "sha256": sha256(by_path[path].encode()).hexdigest()}
        for path in sorted(selected)
    ], None


def mesh_cache_key(
    task: TaskSpec,
    *,
    geometry_facts: GeometryFacts | None,
    input_mesh_facts: InputMeshFacts | None = None,
    plan: ExecutionPlan,
    environment: EnvironmentSnapshot,
    public_asset_root: str | Path,
) -> MeshKeyResult:
    """Return a conservative mesh key or an auditable uncacheable reason."""

    provided = task.mesh is not None and task.mesh.strategy == "provided"
    dependencies, reason = (
        ([], None) if provided else _mesh_dependency_files(plan)
    )
    if reason is not None:
        return MeshKeyResult(False, None, reason)
    mesh_commands = [
        {
            "executable": item.executable,
            "args": item.args,
            "mpi_ranks": item.mpi_ranks,
        }
        for item in plan.commands
        if item.stage == "mesh"
    ]
    if not mesh_commands and not provided:
        return MeshKeyResult(False, None, "MESH_COMMAND_MISSING")
    if provided and input_mesh_facts is None:
        return MeshKeyResult(False, None, "INPUT_MESH_FACTS_MISSING")
    mesh_intent = (
        task.mesh.model_dump(exclude={"quality"}, mode="json")
        if task.mesh is not None
        else None
    )
    assets = _asset_observations(task, Path(public_asset_root))
    gmsh_fingerprint: dict[str, object] | None = None
    if any(item["executable"] == "gmsh" for item in mesh_commands):
        if environment.gmsh is None or not environment.gmsh.is_file():
            return MeshKeyResult(False, None, "GMSH_UNAVAILABLE")
        stat = environment.gmsh.stat()
        gmsh_fingerprint = {
            "path": str(environment.gmsh),
            "bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    key = _canonical_sha256(
        {
            "schema_version": DERIVED_CACHE_SCHEMA_VERSION,
            "geometry_facts": (
                geometry_facts.model_dump(mode="json")
                if geometry_facts is not None
                else None
            ),
            "input_mesh_facts": (
                input_mesh_facts.model_dump(mode="json")
                if input_mesh_facts is not None
                else None
            ),
            "mesh_intent": mesh_intent,
            "mesh_dependency_files": dependencies,
            "public_assets": assets,
            "mesh_commands": mesh_commands,
            "openfoam": {
                "distribution": environment.distribution,
                "version": environment.version,
            },
            "gmsh": gmsh_fingerprint,
            "regions": [
                item.model_dump(mode="json")
                for item in plan.manifest.regions
            ],
        }
    )
    return MeshKeyResult(True, key)


@dataclass(frozen=True)
class CacheLookup(Generic[T]):
    status: Literal["hit", "miss"]
    key: str
    value: T | None = None
    reason_code: str | None = None


class _MeshMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    cache_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    mesh_paths: list[str]
    source_run_id: str
    source_attempt: int = Field(ge=1)
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DerivedCache:
    """Small explicit cache with atomic entries and corruption quarantine."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def _entry(self, kind: str, key: str) -> Path:
        if len(key) != 64 or any(item not in "0123456789abcdef" for item in key):
            raise ValueError("cache key must be a lowercase SHA256")
        return self.root / kind / key

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _manifest(directory: Path) -> dict[str, dict[str, object]]:
        values: dict[str, dict[str, object]] = {}
        for path in sorted(directory.rglob("*")):
            if path.is_dir() or path.name == "content-manifest.json":
                continue
            if path.is_symlink():
                raise ValueError("cache entries must not contain symlinks")
            values[path.relative_to(directory).as_posix()] = {
                "bytes": path.stat().st_size,
                "sha256": _file_sha256(path),
            }
        return values

    def _finalize_temporary(self, temporary: Path, destination: Path) -> None:
        self._write_json(
            temporary / "content-manifest.json",
            {
                "schema_version": 1,
                "files": self._manifest(temporary),
            },
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(temporary, destination)
        except OSError:
            if not destination.is_dir():
                raise

    def _verify(self, entry: Path) -> bool:
        manifest = entry / "content-manifest.json"
        if not manifest.is_file():
            return False
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            return payload.get("files") == self._manifest(entry)
        except (OSError, ValueError, json.JSONDecodeError):
            return False

    def _quarantine(self, kind: str, key: str, entry: Path) -> None:
        destination = (
            self.root / "invalid" / kind / f"{key}-{uuid4().hex[:8]}"
        )
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(entry, destination)
        except OSError:
            return

    def load_geometry(self, key: str) -> CacheLookup[GeometryFacts]:
        entry = self._entry("geometry", key)
        if not entry.is_dir():
            return CacheLookup("miss", key, reason_code="DERIVED_CACHE_MISS")
        if not self._verify(entry):
            self._quarantine("geometry", key, entry)
            return CacheLookup("miss", key, reason_code="DERIVED_CACHE_INVALID")
        try:
            facts = GeometryFacts.model_validate_json(
                (entry / "geometry-facts.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            self._quarantine("geometry", key, entry)
            return CacheLookup("miss", key, reason_code="DERIVED_CACHE_INVALID")
        return CacheLookup("hit", key, facts)

    def store_geometry(self, key: str, facts: GeometryFacts) -> bool:
        temporary: Path | None = None
        try:
            destination = self._entry("geometry", key)
            if destination.is_dir():
                return True
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(
                tempfile.mkdtemp(prefix=f".{key}.", dir=destination.parent)
            )
            self._write_json(
                temporary / "geometry-facts.json",
                facts.model_dump(mode="json"),
            )
            self._finalize_temporary(temporary, destination)
            return destination.is_dir()
        except OSError:
            return False
        finally:
            if temporary is not None and temporary.exists():
                shutil.rmtree(temporary)

    @staticmethod
    def _poly_mesh_paths(case_root: Path) -> list[Path]:
        constant = case_root / "constant"
        if not constant.is_dir():
            return []
        return sorted(
            path
            for path in constant.rglob("polyMesh")
            if path.is_dir()
            and not path.is_symlink()
            and path.relative_to(constant).parts[-1] == "polyMesh"
        )

    def store_mesh(
        self,
        key: str,
        *,
        case_root: str | Path,
        mesh_quality: MeshQualityReport,
        plan: ExecutionPlan,
        source_run_id: str,
        source_attempt: int,
    ) -> bool:
        if not mesh_quality.passed or mesh_quality.check_mesh_passed is not True:
            return False
        case = Path(case_root).resolve()
        mesh_paths = self._poly_mesh_paths(case)
        if not mesh_paths:
            return False
        temporary: Path | None = None
        try:
            destination = self._entry("mesh", key)
            if destination.is_dir():
                return True
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(
                tempfile.mkdtemp(prefix=f".{key}.", dir=destination.parent)
            )
            relative_paths = [
                path.relative_to(case).as_posix() for path in mesh_paths
            ]
            metadata = _MeshMetadata(
                cache_key=key,
                mesh_paths=relative_paths,
                source_run_id=source_run_id,
                source_attempt=source_attempt,
                plan_sha256=_canonical_sha256(plan.model_dump(mode="json")),
            )
            self._write_json(
                temporary / "metadata.json",
                metadata.model_dump(mode="json"),
            )
            self._write_json(
                temporary / "mesh-quality-report.json",
                mesh_quality.model_dump(mode="json"),
            )
            for source, relative in zip(mesh_paths, relative_paths, strict=True):
                shutil.copytree(source, temporary / "content" / relative)
            self._finalize_temporary(temporary, destination)
            return destination.is_dir()
        except OSError:
            return False
        finally:
            if temporary is not None and temporary.exists():
                shutil.rmtree(temporary)

    def restore_mesh(
        self,
        key: str,
        *,
        case_root: str | Path,
    ) -> CacheLookup[dict[str, object]]:
        entry = self._entry("mesh", key)
        if not entry.is_dir():
            return CacheLookup("miss", key, reason_code="DERIVED_CACHE_MISS")
        if not self._verify(entry):
            self._quarantine("mesh", key, entry)
            return CacheLookup("miss", key, reason_code="DERIVED_CACHE_INVALID")
        try:
            metadata = _MeshMetadata.model_validate_json(
                (entry / "metadata.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            self._quarantine("mesh", key, entry)
            return CacheLookup("miss", key, reason_code="DERIVED_CACHE_INVALID")
        case = Path(case_root).resolve()
        for relative in metadata.mesh_paths:
            destination = (case / relative).resolve()
            if not destination.is_relative_to(case):
                self._quarantine("mesh", key, entry)
                return CacheLookup("miss", key, reason_code="DERIVED_CACHE_INVALID")
            if destination.exists():
                return CacheLookup(
                    "miss",
                    key,
                    reason_code="DERIVED_CACHE_DESTINATION_CONFLICT",
                )
        destinations = [case / relative for relative in metadata.mesh_paths]
        try:
            for relative, destination in zip(
                metadata.mesh_paths,
                destinations,
                strict=True,
            ):
                shutil.copytree(entry / "content" / relative, destination)
        except OSError:
            for destination in destinations:
                if destination.is_dir():
                    shutil.rmtree(destination, ignore_errors=True)
                elif destination.exists():
                    try:
                        destination.unlink()
                    except OSError:
                        pass
            return CacheLookup(
                "miss",
                key,
                reason_code="DERIVED_CACHE_RESTORE_FAILED",
            )
        return CacheLookup(
            "hit",
            key,
            value=metadata.model_dump(mode="json"),
        )
