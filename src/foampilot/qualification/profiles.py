"""OpenFOAM field sampling helpers owned by the evaluator."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pyvista as pv


class OpenFOAMCaseData:
    """Read generated fields through VTK's OpenFOAM reader."""

    def __init__(
        self,
        case_dir: str | Path,
        *,
        region: str | None = None,
    ) -> None:
        self.case_dir = Path(case_dir).resolve()
        self.region = region
        marker = self.case_dir / "foampilot.foam"
        marker.touch(exist_ok=True)
        self.reader = pv.OpenFOAMReader(str(marker))
        self.times = tuple(float(value) for value in self.reader.time_values)
        if not self.times:
            raise ValueError(f"No OpenFOAM times found in {self.case_dir}")

    @property
    def latest_time(self) -> float:
        return max(self.times)

    def _read(self, time_value: float | None = None):
        selected = self.latest_time if time_value is None else time_value
        self.reader.set_active_time_value(float(selected))
        return self.reader.read()

    def _region_output(self, time_value: float | None = None):
        output = self._read(time_value)
        if self.region is None:
            return output
        if self.region not in output.keys():
            raise KeyError(self.region)
        return output[self.region]

    def internal_mesh(self, time_value: float | None = None):
        output = self._region_output(time_value)
        if "internalMesh" not in output.keys():
            raise KeyError("internalMesh")
        return output["internalMesh"]

    def boundary_patch(
        self,
        patch_name: str,
        *,
        time_value: float | None = None,
    ):
        output = self._region_output(time_value)
        if "boundary" not in output.keys():
            raise KeyError("boundary")
        boundary = output["boundary"]
        if patch_name not in boundary.keys():
            raise KeyError(patch_name)
        return boundary[patch_name]

    def sample(
        self,
        field: str,
        coordinates: Iterable[Iterable[float]],
        *,
        time_value: float | None = None,
        allow_invalid: bool = False,
    ) -> np.ndarray:
        mesh = self.internal_mesh(time_value)
        source = mesh.cell_data_to_point_data(pass_cell_data=True)
        points = np.asarray(list(coordinates), dtype=float)
        sampled = pv.PolyData(points).sample(source)
        if field not in sampled.point_data:
            raise KeyError(field)
        valid = sampled.point_data.get("vtkValidPointMask")
        if (
            not allow_invalid
            and valid is not None
            and not np.all(valid)
        ):
            raise ValueError(f"Sampling {field} produced points outside mesh")
        return np.asarray(sampled.point_data[field], dtype=float)

    def volume_integral(
        self, field: str, *, time_value: float | None = None
    ) -> float:
        mesh = self.internal_mesh(time_value)
        if field not in mesh.cell_data:
            raise KeyError(field)
        volumes = np.asarray(
            mesh.compute_cell_sizes(
                length=False, area=False, volume=True
            ).cell_data["Volume"],
            dtype=float,
        )
        values = np.asarray(mesh.cell_data[field], dtype=float)
        if values.ndim != 1:
            raise ValueError(f"Volume integral requires scalar field: {field}")
        return float(np.sum(values * volumes))

    def volume_mean(
        self, field: str, *, time_value: float | None = None
    ) -> np.ndarray:
        mesh = self.internal_mesh(time_value)
        if field not in mesh.cell_data:
            raise KeyError(field)
        volumes = np.asarray(
            mesh.compute_cell_sizes(
                length=False, area=False, volume=True
            ).cell_data["Volume"],
            dtype=float,
        )
        values = np.asarray(mesh.cell_data[field], dtype=float)
        return np.sum(values * volumes[:, None], axis=0) / np.sum(volumes) if (
            values.ndim > 1
        ) else np.asarray(np.sum(values * volumes) / np.sum(volumes))

    def boundary_fluxes(
        self,
        *,
        time_value: float | None = None,
        field: str = "U",
    ) -> list[tuple[np.ndarray, float]]:
        output = self._region_output(time_value)
        boundary = output["boundary"]
        cells: list[tuple[np.ndarray, float]] = []
        for name in boundary.keys():
            patch = boundary[name]
            if not isinstance(patch, pv.PolyData) or patch.n_cells == 0:
                continue
            if field not in patch.cell_data:
                continue
            with_sizes = patch.compute_cell_sizes(
                length=False, area=True, volume=False
            )
            with_normals = with_sizes.compute_normals(
                cell_normals=True,
                point_normals=False,
                auto_orient_normals=False,
                consistent_normals=True,
            )
            velocity = np.asarray(
                with_normals.cell_data[field],
                dtype=float,
            )
            normals = np.asarray(
                with_normals.cell_data["Normals"], dtype=float
            )
            areas = np.asarray(with_normals.cell_data["Area"], dtype=float)
            centers = np.asarray(with_normals.cell_centers().points)
            flux = np.einsum("ij,ij,i->i", velocity, normals, areas)
            cells.extend(zip(centers, flux))
        return cells

    def flux_on_plane(
        self,
        axis: int,
        coordinate: float,
        *,
        tolerance: float,
        field: str = "U",
    ) -> float:
        selected = [
            flux
            for center, flux in self.boundary_fluxes(field=field)
            if abs(float(center[axis]) - coordinate) <= tolerance
        ]
        if not selected:
            raise ValueError(
                f"No boundary faces at axis {axis}={coordinate}"
            )
        return float(sum(selected))


def flatten_arrays(*arrays: np.ndarray) -> list[float]:
    if not arrays:
        return []
    return np.concatenate(
        [np.asarray(array, dtype=float).reshape(-1) for array in arrays]
    ).tolist()
