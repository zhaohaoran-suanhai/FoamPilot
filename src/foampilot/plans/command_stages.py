"""Deterministic stages for OpenFOAM utility executables."""

from __future__ import annotations

from .models import CommandStage


KNOWN_UTILITY_STAGES: dict[str, CommandStage] = {
    "blockMesh": CommandStage.MESH,
    "surfaceCheck": CommandStage.MESH,
    "surfaceFeatureExtract": CommandStage.MESH,
    "snappyHexMesh": CommandStage.MESH,
    "gmsh": CommandStage.MESH,
    "gmshToFoam": CommandStage.MESH,
    "checkMesh": CommandStage.CHECK,
    "setFields": CommandStage.INITIALIZE,
    "topoSet": CommandStage.INITIALIZE,
    "splitMeshRegions": CommandStage.INITIALIZE,
    "decomposePar": CommandStage.DECOMPOSE,
    "reconstructPar": CommandStage.RECONSTRUCT,
    "postProcess": CommandStage.POSTPROCESS,
    "foamPostProcess": CommandStage.POSTPROCESS,
}


__all__ = ["KNOWN_UTILITY_STAGES"]
