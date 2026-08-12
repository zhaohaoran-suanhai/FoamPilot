"""Bounded deterministic inspection of native OpenFOAM polyMesh files."""

from __future__ import annotations

import gzip
from hashlib import sha256
from pathlib import Path
import re

from foampilot.assets import AssetBundle, BundleMember
from foampilot.tasks.geometry import LengthUnit

from .models import (
    BoundingBox,
    InputMeshFacts,
    MeshPatchFact,
    MeshZoneFact,
)


_UNIT_TO_METRES: dict[str, float] = {
    "m": 1.0,
    "cm": 1.0e-2,
    "mm": 1.0e-3,
    "um": 1.0e-6,
    "in": 0.0254,
}
_TOKEN = re.compile(
    r'"(?:\\.|[^"\\])*"|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
    r"|[A-Za-z_][A-Za-z0-9_<>./:+-]*|[{}();]"
)
_COMMENT = re.compile(r"//[^\r\n]*|/\*.*?\*/", re.DOTALL)


class PolyMeshInspectionError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _fail(code: str, detail: str) -> PolyMeshInspectionError:
    return PolyMeshInspectionError(code, detail)


def _digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def _member_path(root: Path, bundle: AssetBundle, logical_name: str) -> Path | None:
    member = next(
        (item for item in bundle.members if item.logical_name == logical_name),
        None,
    )
    if member is None:
        return None
    path = root / member.relative_path
    if path.is_symlink() or not path.is_file():
        raise _fail(
            "POLYMESH_BUNDLE_CHANGED",
            f"mesh member is missing or unsafe: {member.relative_path}",
        )
    if _digest(path) != member.sha256:
        raise _fail(
            "POLYMESH_BUNDLE_CHANGED",
            f"mesh member hash changed: {member.relative_path}",
        )
    return path


def _read_text(path: Path) -> str:
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                text = stream.read()
        else:
            text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise _fail("POLYMESH_PARSE_FAILED", f"cannot read {path.name}") from error
    stripped = _COMMENT.sub("", text)
    if re.search(r"\bformat\s+binary\s*;", stripped):
        raise _fail(
            "POLYMESH_BINARY_UNSUPPORTED",
            f"binary mesh member is unsupported: {path.name}",
        )
    if "$" in stripped or re.search(r"#\s*[A-Za-z_]", stripped):
        raise _fail(
            "POLYMESH_DYNAMIC_INPUT_UNSUPPORTED",
            f"dynamic input is unsupported: {path.name}",
        )
    return stripped


class _Cursor:
    def __init__(self, text: str, member: str) -> None:
        self.tokens = _TOKEN.findall(text)
        self.position = 0
        self.member = member

    def remaining(self) -> int:
        return len(self.tokens) - self.position

    def peek(self) -> str | None:
        return self.tokens[self.position] if self.remaining() else None

    def take(self) -> str:
        if not self.remaining():
            raise _fail(
                "POLYMESH_PARSE_FAILED",
                f"unexpected end of {self.member}",
            )
        value = self.tokens[self.position]
        self.position += 1
        return value

    def expect(self, value: str) -> None:
        observed = self.take()
        if observed != value:
            raise _fail(
                "POLYMESH_PARSE_FAILED",
                f"expected {value!r} in {self.member}, observed {observed!r}",
            )

    def integer(self) -> int:
        token = self.take()
        if not re.fullmatch(r"[-+]?\d+", token):
            raise _fail(
                "POLYMESH_PARSE_FAILED",
                f"expected integer in {self.member}, observed {token!r}",
            )
        return int(token)

    def number(self) -> float:
        token = self.take()
        try:
            return float(token)
        except ValueError as error:
            raise _fail(
                "POLYMESH_PARSE_FAILED",
                f"expected number in {self.member}, observed {token!r}",
            ) from error

    def skip_balanced(self, opening: str, closing: str) -> None:
        self.expect(opening)
        depth = 1
        while depth:
            token = self.take()
            if token == opening:
                depth += 1
            elif token == closing:
                depth -= 1

    def skip_header(self) -> None:
        if self.peek() != "FoamFile":
            raise _fail(
                "POLYMESH_PARSE_FAILED",
                f"missing FoamFile header in {self.member}",
            )
        self.take()
        self.skip_balanced("{", "}")


def _list_cursor(text: str, member: str) -> tuple[_Cursor, int]:
    cursor = _Cursor(text, member)
    cursor.skip_header()
    count = cursor.integer()
    if count < 0:
        raise _fail("POLYMESH_PARSE_FAILED", f"negative count in {member}")
    cursor.expect("(")
    return cursor, count


def _parse_points(text: str) -> tuple[tuple[float, float, float], ...]:
    cursor, count = _list_cursor(text, "points")
    values = []
    for _ in range(count):
        cursor.expect("(")
        values.append((cursor.number(), cursor.number(), cursor.number()))
        cursor.expect(")")
    cursor.expect(")")
    return tuple(values)


def _parse_faces(text: str) -> tuple[tuple[int, ...], ...]:
    cursor, count = _list_cursor(text, "faces")
    values = []
    for _ in range(count):
        point_count = cursor.integer()
        cursor.expect("(")
        points = tuple(cursor.integer() for _ in range(point_count))
        cursor.expect(")")
        values.append(points)
    cursor.expect(")")
    return tuple(values)


def _parse_labels(text: str, member: str) -> tuple[int, ...]:
    cursor, count = _list_cursor(text, member)
    values = tuple(cursor.integer() for _ in range(count))
    cursor.expect(")")
    return values


def _take_block(cursor: _Cursor) -> list[str]:
    cursor.expect("{")
    depth = 1
    result: list[str] = []
    while depth:
        token = cursor.take()
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
            if depth == 0:
                break
        result.append(token)
    return result


def _value_after(tokens: list[str], key: str) -> str:
    try:
        index = tokens.index(key)
        return tokens[index + 1]
    except (ValueError, IndexError) as error:
        raise _fail(
            "POLYMESH_PARSE_FAILED",
            f"missing {key} in boundary patch",
        ) from error


def _parse_boundary(text: str) -> tuple[MeshPatchFact, ...]:
    cursor, count = _list_cursor(text, "boundary")
    patches = []
    for _ in range(count):
        name = cursor.take().strip('"')
        tokens = _take_block(cursor)
        try:
            face_count = int(_value_after(tokens, "nFaces"))
            start_face = int(_value_after(tokens, "startFace"))
        except ValueError as error:
            raise _fail(
                "POLYMESH_PARSE_FAILED",
                f"invalid face range for patch {name}",
            ) from error
        patches.append(
            MeshPatchFact(
                name=name,
                patch_type=_value_after(tokens, "type"),
                start_face=start_face,
                face_count=face_count,
            )
        )
    cursor.expect(")")
    return tuple(patches)


def _zone_count(tokens: list[str], field_name: str, zone_name: str) -> int:
    try:
        index = tokens.index(field_name) + 1
    except ValueError as error:
        raise _fail(
            "POLYMESH_PARSE_FAILED",
            f"zone {zone_name} has no {field_name}",
        ) from error
    if index < len(tokens) and tokens[index].startswith("List<"):
        index += 1
    try:
        count = int(tokens[index])
        if tokens[index + 1] != "(":
            raise ValueError
    except (IndexError, ValueError) as error:
        raise _fail(
            "POLYMESH_PARSE_FAILED",
            f"zone {zone_name} has an invalid {field_name} list",
        ) from error
    cursor = index + 2
    observed = 0
    depth = 1
    while cursor < len(tokens) and depth:
        token = tokens[cursor]
        cursor += 1
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif depth == 1:
            observed += 1
    if depth or observed != count:
        raise _fail(
            "POLYMESH_PARSE_FAILED",
            f"zone {zone_name} count does not match its labels",
        )
    return count


def _parse_zones(
    text: str,
    member: str,
    labels_field: str,
) -> tuple[MeshZoneFact, ...]:
    cursor, count = _list_cursor(text, member)
    zones = []
    for _ in range(count):
        name = cursor.take().strip('"')
        tokens = _take_block(cursor)
        zones.append(
            MeshZoneFact(
                name=name,
                element_count=_zone_count(tokens, labels_field, name),
            )
        )
    cursor.expect(")")
    return tuple(zones)


def _validate_topology(
    points: tuple[tuple[float, float, float], ...],
    faces: tuple[tuple[int, ...], ...],
    owner: tuple[int, ...],
    neighbour: tuple[int, ...],
    patches: tuple[MeshPatchFact, ...],
    zones: tuple[tuple[MeshZoneFact, ...], ...],
) -> int:
    if len(owner) != len(faces):
        raise _fail(
            "POLYMESH_TOPOLOGY_INVALID",
            "owner count must equal face count",
        )
    if len(neighbour) > len(faces):
        raise _fail(
            "POLYMESH_TOPOLOGY_INVALID",
            "neighbour count exceeds face count",
        )
    if any(not face or any(index < 0 or index >= len(points) for index in face) for face in faces):
        raise _fail(
            "POLYMESH_TOPOLOGY_INVALID",
            "face references an invalid point",
        )
    cell_labels = set(owner) | set(neighbour)
    if any(item < 0 for item in cell_labels):
        raise _fail("POLYMESH_TOPOLOGY_INVALID", "negative cell label")
    cells = max(cell_labels, default=-1) + 1
    if cell_labels != set(range(cells)):
        raise _fail(
            "POLYMESH_TOPOLOGY_INVALID",
            "cell labels are not contiguous",
        )
    expected_start = len(neighbour)
    for patch in patches:
        if patch.start_face != expected_start:
            raise _fail(
                "POLYMESH_TOPOLOGY_INVALID",
                f"boundary patch {patch.name} is not contiguous",
            )
        expected_start += patch.face_count
    if expected_start != len(faces):
        raise _fail(
            "POLYMESH_TOPOLOGY_INVALID",
            "boundary patches do not cover all boundary faces",
        )
    limits = (cells, len(faces), len(points))
    for group, limit in zip(zones, limits, strict=True):
        for zone in group:
            if zone.element_count > limit:
                raise _fail(
                    "POLYMESH_TOPOLOGY_INVALID",
                    f"zone {zone.name} exceeds mesh entity count",
                )
    return cells


def inspect_poly_mesh(
    bundle_root: Path,
    bundle: AssetBundle,
    *,
    length_unit: LengthUnit,
) -> InputMeshFacts:
    """Return compact authoritative facts without exposing raw mesh content."""

    root = Path(bundle_root).resolve()
    required_paths = {
        name: _member_path(root, bundle, name)
        for name in ("points", "faces", "owner", "neighbour", "boundary")
    }
    if any(path is None for path in required_paths.values()):
        raise _fail(
            "POLYMESH_PARSE_FAILED",
            "bundle manifest lacks a required mesh member",
        )
    points = _parse_points(_read_text(required_paths["points"]))  # type: ignore[arg-type]
    faces = _parse_faces(_read_text(required_paths["faces"]))  # type: ignore[arg-type]
    owner = _parse_labels(_read_text(required_paths["owner"]), "owner")  # type: ignore[arg-type]
    neighbour = _parse_labels(
        _read_text(required_paths["neighbour"]),  # type: ignore[arg-type]
        "neighbour",
    )
    patches = _parse_boundary(_read_text(required_paths["boundary"]))  # type: ignore[arg-type]

    zone_specs = (
        ("cellZones", "cellLabels"),
        ("faceZones", "faceLabels"),
        ("pointZones", "pointLabels"),
    )
    parsed_zones: list[tuple[MeshZoneFact, ...]] = []
    for member, field in zone_specs:
        path = _member_path(root, bundle, member)
        parsed_zones.append(
            _parse_zones(_read_text(path), member, field) if path else ()
        )
    cell_zones, face_zones, point_zones = parsed_zones
    cells = _validate_topology(
        points,
        faces,
        owner,
        neighbour,
        patches,
        (cell_zones, face_zones, point_zones),
    )
    if not points:
        raise _fail("POLYMESH_TOPOLOGY_INVALID", "mesh has no points")
    scale = _UNIT_TO_METRES[length_unit]
    minimum = tuple(min(item[axis] for item in points) * scale for axis in range(3))
    maximum = tuple(max(item[axis] for item in points) * scale for axis in range(3))
    dimensionality = tuple(
        f"empty patch {patch.name}"
        for patch in patches
        if patch.patch_type == "empty"
    )
    source_hashes = {
        member.logical_name: member.sha256
        for member in bundle.members
    }
    return InputMeshFacts(
        bundle_manifest_sha256=bundle.manifest_sha256,
        inspector_id="foampilot.mesh.poly-mesh",
        inspector_version="1.0.0",
        region=bundle.region,
        declared_length_unit=length_unit,
        source_member_sha256=source_hashes,
        points=len(points),
        faces=len(faces),
        internal_faces=len(neighbour),
        cells=cells,
        bounding_box_m=BoundingBox(minimum=minimum, maximum=maximum),
        patches=patches,
        cell_zones=cell_zones,
        face_zones=face_zones,
        point_zones=point_zones,
        dimensionality_observations=dimensionality,
        topology_observations=(
            "owner count equals face count",
            "cell labels are contiguous",
            "boundary face coverage is contiguous",
        ),
        warnings=(),
    )


__all__ = ["PolyMeshInspectionError", "inspect_poly_mesh"]
