from __future__ import annotations

from pathlib import Path
import os
import tarfile
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
REMOVED_VALIDATION_MODULES = {
    "foampilot/validation/models.py",
    "foampilot/validation/native.py",
    "foampilot/validation/policies.py",
    "foampilot/validation/public_checks.py",
}
PRODUCTION_PYTHON_SOURCES = tuple(
    path.relative_to(ROOT / "src").as_posix()
    for path in sorted((ROOT / "src" / "foampilot").rglob("*.py"))
)


def _wheel() -> Path:
    if os.environ.get("FOAMPILOT_VERIFY_DISTRIBUTION") != "1":
        pytest.skip("set FOAMPILOT_VERIFY_DISTRIBUTION=1 after building artifacts")
    wheels = tuple(DIST.glob("foampilot-*.whl"))
    assert len(wheels) == 1, "exactly one freshly built FoamPilot wheel is required"
    return wheels[0]


def _sdist() -> Path:
    if os.environ.get("FOAMPILOT_VERIFY_DISTRIBUTION") != "1":
        pytest.skip("set FOAMPILOT_VERIFY_DISTRIBUTION=1 after building artifacts")
    archives = tuple(DIST.glob("foampilot-*.tar.gz"))
    assert len(archives) == 1, "exactly one freshly built FoamPilot sdist is required"
    return archives[0]


def test_built_wheel_does_not_resurrect_deleted_validation_modules() -> None:
    with zipfile.ZipFile(_wheel()) as archive:
        names = set(archive.namelist())
        payload = b"\n".join(
            archive.read(name)
            for name in names
            if archive.getinfo(name).file_size <= 2_000_000
        )
        packaged_sources = {
            name: archive.read(name) for name in PRODUCTION_PYTHON_SOURCES
        }
        packaged_python_sources = {
            name
            for name in names
            if name.startswith("foampilot/") and name.endswith(".py")
        }

    assert REMOVED_VALIDATION_MODULES.isdisjoint(names)
    assert "foampilot/validation/legacy.py" in names
    assert "foampilot/observations/openfoam10.py" in names
    assert "foampilot/postprocessing/openfoam10.py" in names
    assert "foampilot/acceptance/evaluator.py" in names
    assert b"/home/edwin" not in payload
    assert b"feal-venv" not in payload
    assert packaged_python_sources == set(PRODUCTION_PYTHON_SOURCES)
    for name in PRODUCTION_PYTHON_SOURCES:
        assert packaged_sources[name] == (ROOT / "src" / name).read_bytes()


def test_built_sdist_does_not_include_stale_build_tree() -> None:
    with tarfile.open(_sdist(), "r:gz") as archive:
        names = tuple(archive.getnames())
        prefix = names[0].split("/", 1)[0]
        packaged_python_sources = {
            name.removeprefix(f"{prefix}/src/")
            for name in names
            if name.startswith(f"{prefix}/src/foampilot/")
            and name.endswith(".py")
        }
        for name in PRODUCTION_PYTHON_SOURCES:
            member = archive.extractfile(f"{prefix}/src/{name}")
            assert member is not None
            assert member.read() == (ROOT / "src" / name).read_bytes()

    assert not any("/build/lib/" in name for name in names)
    assert packaged_python_sources == set(PRODUCTION_PYTHON_SOURCES)
