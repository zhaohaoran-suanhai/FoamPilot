"""Immutable contracts for public file and directory asset bundles."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


class StrictFrozenModel(BaseModel):
    """Base for serialized contracts that cannot gain fields or mutate."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _safe_relative_path(value: str, *, label: str) -> str:
    parsed = PurePosixPath(value)
    if (
        not value
        or parsed.is_absolute()
        or ".." in parsed.parts
        or ".foampilot" in parsed.parts
        or parsed.as_posix() in {"", "."}
    ):
        raise ValueError(f"{label} must be a safe relative path: {value!r}")
    return parsed.as_posix()


class BundleMember(StrictFrozenModel):
    """One content-addressed regular file in an atomic asset bundle."""

    relative_path: str
    logical_name: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _safe_relative_path(value, label="bundle member path")

    @field_validator("logical_name")
    @classmethod
    def validate_logical_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("logical name must not be blank")
        return normalized


def _ordered_members(
    members: Iterable[BundleMember],
) -> tuple[BundleMember, ...]:
    return tuple(
        sorted(
            members,
            key=lambda item: (item.relative_path, item.logical_name),
        )
    )


def compute_bundle_manifest_sha256(
    *,
    adapter_id: str,
    kind: str,
    source_path: str,
    install_path: str,
    region: str | None,
    members: Iterable[BundleMember],
) -> str:
    """Return the canonical digest for one bundle declaration."""

    payload = {
        "schema_version": 1,
        "adapter_id": adapter_id,
        "kind": kind,
        "source_path": source_path,
        "install_path": install_path,
        "region": region,
        "members": [
            item.model_dump(mode="json") for item in _ordered_members(members)
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


class AssetBundle(StrictFrozenModel):
    """Canonical manifest for an asset staged as one immutable unit."""

    schema_version: Literal[1] = 1
    adapter_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    install_path: str = Field(min_length=1)
    region: str | None = None
    members: tuple[BundleMember, ...] = Field(min_length=1)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("adapter_id", "kind")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"invalid asset identifier: {value!r}")
        return value

    @field_validator("source_path", "install_path")
    @classmethod
    def validate_bundle_path(cls, value: str) -> str:
        return _safe_relative_path(value, label="bundle path")

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or "/" in normalized or normalized in {".", ".."}:
            raise ValueError(f"invalid region identity: {value!r}")
        return normalized

    @field_validator("members")
    @classmethod
    def order_members(
        cls,
        value: tuple[BundleMember, ...],
    ) -> tuple[BundleMember, ...]:
        return _ordered_members(value)

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        member_paths = [item.relative_path for item in self.members]
        if len(member_paths) != len(set(member_paths)):
            raise ValueError("duplicate member path in asset bundle")
        logical_names = [item.logical_name for item in self.members]
        if len(logical_names) != len(set(logical_names)):
            raise ValueError("duplicate logical name in asset bundle")
        expected = compute_bundle_manifest_sha256(
            adapter_id=self.adapter_id,
            kind=self.kind,
            source_path=self.source_path,
            install_path=self.install_path,
            region=self.region,
            members=self.members,
        )
        if self.manifest_sha256 != expected:
            raise ValueError(
                "bundle manifest SHA256 does not match canonical content"
            )
        return self


class StagedAsset(StrictFrozenModel):
    """A verified bundle and its controlled case destination."""

    bundle: AssetBundle
    destination: Path


__all__ = [
    "AssetBundle",
    "BundleMember",
    "StagedAsset",
    "compute_bundle_manifest_sha256",
]
