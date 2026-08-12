"""Protocol implemented by deterministic public-asset adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, TYPE_CHECKING

from foampilot.extensions import CapabilityDescriptor

from .models import AssetBundle, StagedAsset

if TYPE_CHECKING:
    from foampilot.tasks import PublicAsset


class AssetAdapter(Protocol):
    descriptor: CapabilityDescriptor

    def inspect(
        self,
        source_root: Path,
        declaration: PublicAsset,
    ) -> AssetBundle: ...

    def stage(
        self,
        bundle: AssetBundle,
        source_root: Path,
        case_root: Path,
    ) -> StagedAsset: ...


__all__ = ["AssetAdapter"]
