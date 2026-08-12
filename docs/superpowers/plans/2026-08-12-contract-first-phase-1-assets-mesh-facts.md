# Phase 1 Asset Bundles and Mesh Facts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept OpenFOAM `polyMesh` directories as immutable atomic assets and provide compact authoritative mesh facts before model reasoning.

**Architecture:** Add generic asset-bundle and capability-extension protocols, then implement a first-party Foundation v10 polyMesh adapter. Keep `TaskSpec.schema_version=2` during this phase by extending `PublicAsset` with an optional typed directory manifest; migrate file assets through the same registry without changing existing fixtures. The canonical solve path stages bundles, writes `input-mesh-facts.json`, and optionally runs a system-owned pre-authoring `checkMesh` for provided native meshes.

**Tech Stack:** Python 3.12, Pydantic 2, gzip/struct/pathlib/hashlib from the standard library, pytest 8, Foundation OpenFOAM 10 `checkMesh`.

## Global Constraints

- Target Foundation OpenFOAM 10 only.
- Treat default-region and `constant/<region>/polyMesh` as separate bundle identities.
- Accept `points` or `points.gz`, never both; apply the same rule to all logical members.
- Required logical members are `points`, `faces`, `owner`, `neighbour`, and `boundary`.
- Preserve optional `cellZones`, `faceZones`, `pointZones`, and `sets/` recursively.
- Reject symlinks, devices, traversal, `.foampilot`, duplicate logical members, oversize bundles, and post-declaration mutation.
- A model-authored file may not overlap any bundle member or ancestor path.
- Do not send raw points/faces/owner/neighbour content to the model.
- Existing single-file public assets and current TaskSpec v2 fixtures remain valid.
- Use TDD and commit after every task.

---

## File Structure

- `src/foampilot/assets/models.py`: generic file/directory bundle manifests and staged-bundle result.
- `src/foampilot/assets/adapters.py`: `AssetAdapter` protocol and deterministic first-party registry.
- `src/foampilot/assets/openfoam_mesh.py`: polyMesh discovery, manifest hashing, atomic staging, and immutable-member checks.
- `src/foampilot/extensions/models.py`: versioned `CapabilityDescriptor`.
- `src/foampilot/extensions/registry.py`: first-party capability registry; no entry-point loading in phase 1.
- `src/foampilot/preprocessing/poly_mesh.py`: OpenFOAM text/gzip parsing and compact `InputMeshFacts`.
- `src/foampilot/preprocessing/mesh_probe.py`: system-owned provided-mesh `checkMesh` probe producing `ExecutedMeshFacts`.
- `src/foampilot/tasks/models.py`: optional `bundle` declaration on `PublicAsset`.
- `src/foampilot/tasks/io.py`: stage both file and directory assets through the registry.
- `src/foampilot/agent/generation.py`: reserve every staged bundle path against model overwrite.
- `src/foampilot/agent/native_orchestrator.py`: persist asset/fact artifacts before routing.
- `src/foampilot/workflow/lineage.py`: fingerprint full bundle manifest and inspector versions.
- `src/foampilot/performance/derived_cache.py`: key provided meshes by the bundle/facts hash without requiring a mesh command.
- `tests/fixtures/poly_mesh/minimal/**`: independently authored 2-cell native mesh fixture with named patches and zones.

### Task 1: Generic asset-bundle contracts and first-party registry

**Files:**
- Create: `src/foampilot/assets/__init__.py`
- Create: `src/foampilot/assets/models.py`
- Create: `src/foampilot/assets/adapters.py`
- Create: `src/foampilot/extensions/__init__.py`
- Create: `src/foampilot/extensions/models.py`
- Create: `src/foampilot/extensions/registry.py`
- Test: `tests/test_asset_contracts.py`
- Test: `tests/test_extension_registry.py`

**Interfaces:**
- Produces: `BundleMember(relative_path: str, logical_name: str, sha256: str, bytes: int)`.
- Produces: `AssetBundle(adapter_id: str, kind: str, source_path: str, install_path: str, region: str | None, members: tuple[BundleMember, ...], manifest_sha256: str)`.
- Produces: `StagedAsset(bundle: AssetBundle, destination: Path)`.
- Produces: `AssetAdapter.inspect(source_root: Path, declaration: PublicAsset) -> AssetBundle` and `stage(bundle, source_root, case_root) -> StagedAsset`.
- Produces: `CapabilityRegistry.first_party()`, `register(descriptor, provider)`, and `resolve(kind, target) -> object`.

- [ ] **Step 1: Write failing strict-schema and registry tests**

```python
def test_asset_bundle_rejects_duplicate_member_paths() -> None:
    member = _member("points")
    with pytest.raises(ValidationError):
        AssetBundle(
            adapter_id="foampilot.asset.openfoam-poly-mesh",
            kind="openfoam_poly_mesh",
            source_path="mesh/openfoam/constant/polyMesh",
            install_path="constant/polyMesh",
            region=None,
            members=(member, member),
            manifest_sha256="0" * 64,
        )


def test_registry_rejects_duplicate_extension_id() -> None:
    registry = CapabilityRegistry()
    descriptor = _descriptor("foampilot.asset.file")
    registry.register(descriptor, object())
    with pytest.raises(CapabilityRegistrationError, match="DUPLICATE_EXTENSION_ID"):
        registry.register(descriptor, object())
```

- [ ] **Step 2: Run the tests and verify collection fails**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_asset_contracts.py tests/test_extension_registry.py -q -p no:cacheprovider
```

Expected: FAIL because `foampilot.assets` and `foampilot.extensions` do not exist.

- [ ] **Step 3: Implement frozen contracts and deterministic registry**

Use this public protocol:

```python
class AssetAdapter(Protocol):
    descriptor: CapabilityDescriptor

    def inspect(self, source_root: Path, declaration: PublicAsset) -> AssetBundle: ...

    def stage(
        self,
        bundle: AssetBundle,
        source_root: Path,
        case_root: Path,
    ) -> StagedAsset: ...


class CapabilityRegistry:
    @classmethod
    def first_party(cls) -> "CapabilityRegistry": ...

    def register(self, descriptor: CapabilityDescriptor, provider: object) -> None: ...

    def resolve(self, kind: str, target: OpenFOAMTarget) -> object: ...
```

Canonicalize JSON with sorted keys and compact separators before computing `manifest_sha256`. Enforce unique member paths and logical names in an `AssetBundle` model validator.

- [ ] **Step 4: Run focused tests**

Run the Step 2 command.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/assets src/foampilot/extensions \
  tests/test_asset_contracts.py tests/test_extension_registry.py
git commit -m "feat: add asset and extension contracts"
```

### Task 2: Declare directory assets without breaking file assets

**Files:**
- Modify: `src/foampilot/tasks/models.py`
- Modify: `src/foampilot/tasks/geometry.py`
- Modify: `src/foampilot/cli/main.py`
- Modify: `src/foampilot/taskbuilder/extraction.py`
- Test: `tests/test_task_spec.py`
- Test: `tests/test_taskbuilder_cli.py`

**Interfaces:**
- Consumes: `AssetBundle` member manifest vocabulary from Task 1.
- Produces: `PublicAsset.kind: Literal["file", "directory"] = "file"`.
- Produces: `PublicAsset.install_path: str | None` and `PublicAsset.bundle_manifest_sha256: str | None`.
- Produces CLI `--asset-dir RELATIVE_PATH --asset-install-path CASE_RELATIVE_PATH` pairs for task drafting.

- [ ] **Step 1: Add failing compatibility and directory-declaration tests**

```python
def test_legacy_file_asset_remains_valid() -> None:
    task = TaskSpec.model_validate(_task_payload())
    assert task.public_assets[0].kind == "file"


def test_directory_asset_requires_install_path_and_manifest_hash() -> None:
    payload = _task_payload()
    payload["public_assets"] = [{
        "path": "mesh/native",
        "sha256": "1" * 64,
        "purpose": "provided mesh",
        "kind": "directory",
        "install_path": "constant/polyMesh",
        "bundle_manifest_sha256": "2" * 64,
    }]
    asset = TaskSpec.model_validate(payload).public_assets[0]
    assert asset.kind == "directory"
```

Add a CLI test proving a directory is enumerated and the produced draft contains one directory asset, not one public asset per child file.

- [ ] **Step 2: Verify the tests fail on the current file-only contract**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_task_spec.py tests/test_taskbuilder_cli.py -q -p no:cacheprovider
```

Expected: FAIL because `kind`, `install_path`, and directory CLI handling are absent.

- [ ] **Step 3: Extend the schema and CLI declaration path**

Add these exact invariants:

```python
class PublicAsset(StrictModel):
    path: str
    sha256: str
    purpose: str
    kind: Literal["file", "directory"] = "file"
    install_path: str | None = None
    bundle_manifest_sha256: str | None = None

    @model_validator(mode="after")
    def validate_kind(self) -> Self:
        if self.kind == "file" and (
            self.install_path is not None
            or self.bundle_manifest_sha256 is not None
        ):
            raise ValueError("file asset must not declare directory fields")
        if self.kind == "directory" and (
            self.install_path is None
            or self.bundle_manifest_sha256 is None
        ):
            raise ValueError("directory asset requires install path and manifest hash")
        return self
```

For directory `sha256`, use the same canonical bundle manifest digest as `bundle_manifest_sha256`; keep both fields during TaskSpec v2 compatibility and assert equality. The TaskBuilder model receives only directory path, purpose, manifest hash, member count, and logical member names.

- [ ] **Step 4: Run focused tests**

Run the Step 2 command.

Expected: PASS with all prior file-asset tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/tasks src/foampilot/cli/main.py \
  src/foampilot/taskbuilder/extraction.py tests/test_task_spec.py \
  tests/test_taskbuilder_cli.py
git commit -m "feat: declare atomic directory assets"
```

### Task 3: Implement `OpenFOAMMeshBundle`

**Files:**
- Create: `src/foampilot/assets/openfoam_mesh.py`
- Modify: `src/foampilot/assets/__init__.py`
- Modify: `src/foampilot/extensions/registry.py`
- Create: `tests/fixtures/poly_mesh/minimal/points`
- Create: `tests/fixtures/poly_mesh/minimal/faces`
- Create: `tests/fixtures/poly_mesh/minimal/owner`
- Create: `tests/fixtures/poly_mesh/minimal/neighbour`
- Create: `tests/fixtures/poly_mesh/minimal/boundary`
- Create: `tests/fixtures/poly_mesh/minimal/cellZones`
- Create: `tests/fixtures/poly_mesh/minimal/faceZones`
- Create: `tests/test_openfoam_mesh_bundle.py`

**Interfaces:**
- Produces: `OpenFOAMPolyMeshAdapter.inspect(...) -> AssetBundle`.
- Produces: `OpenFOAMPolyMeshAdapter.stage(...) -> StagedAsset`.
- Produces stable errors `ASSET_BUNDLE_INCOMPLETE`, `ASSET_BUNDLE_AMBIGUOUS`, `ASSET_BUNDLE_UNSAFE`, `ASSET_BUNDLE_HASH_MISMATCH`.

- [ ] **Step 1: Write failing discovery and atomic-staging tests**

```python
def test_poly_mesh_bundle_preserves_optional_zones(tmp_path: Path) -> None:
    source = copy_fixture(tmp_path)
    adapter = OpenFOAMPolyMeshAdapter()
    bundle = adapter.inspect(source.parent, _directory_declaration(source))
    staged = adapter.stage(bundle, source.parent, tmp_path / "case")
    assert (staged.destination / "cellZones").is_file()
    assert {m.logical_name for m in bundle.members} >= {
        "points", "faces", "owner", "neighbour", "boundary", "cellZones"
    }


def test_poly_mesh_bundle_rejects_plain_and_gzip_duplicate(tmp_path: Path) -> None:
    source = copy_fixture(tmp_path)
    gzip_copy(source / "points", source / "points.gz")
    with pytest.raises(AssetBundleError, match="ASSET_BUNDLE_AMBIGUOUS"):
        OpenFOAMPolyMeshAdapter().inspect(source.parent, _directory_declaration(source))
```

Also test missing `neighbour`, symlink members, nested traversal, mutation between inspect/stage, named-region install path, and `sets/` recursion.

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_openfoam_mesh_bundle.py -q -p no:cacheprovider
```

Expected: FAIL because the adapter is missing.

- [ ] **Step 3: Implement inspection and atomic staging**

Use the logical-member table:

```python
REQUIRED = ("points", "faces", "owner", "neighbour", "boundary")
OPTIONAL = ("cellZones", "faceZones", "pointZones")
```

Enumerate `sets/` only when it is a real directory containing regular, non-symlink files. Stage into a temporary sibling directory, fsync each file, re-hash every source member, then rename the completed directory into the exact install path. Refuse an existing target instead of merging.

- [ ] **Step 4: Run focused tests**

Run the Step 2 command.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/assets tests/fixtures/poly_mesh tests/test_openfoam_mesh_bundle.py
git commit -m "feat: stage native polyMesh bundles"
```

### Task 4: Parse compact authoritative `InputMeshFacts`

**Files:**
- Modify: `src/foampilot/preprocessing/models.py`
- Create: `src/foampilot/preprocessing/poly_mesh.py`
- Modify: `src/foampilot/preprocessing/__init__.py`
- Test: `tests/test_poly_mesh_inspector.py`

**Interfaces:**
- Consumes: `AssetBundle` from Task 3.
- Produces: `MeshPatchFact`, `MeshZoneFact`, `InputMeshFacts`.
- Produces: `inspect_poly_mesh(bundle_root: Path, bundle: AssetBundle, *, length_unit: LengthUnit) -> InputMeshFacts`.

- [ ] **Step 1: Write failing mesh-fact tests**

```python
def test_inspector_reports_patch_and_zone_facts() -> None:
    facts = inspect_fixture()
    assert facts.points > 0
    assert facts.faces > 0
    assert facts.cells == 2
    assert [(p.name, p.patch_type) for p in facts.patches] == [
        ("inlet", "patch"),
        ("outlet", "patch"),
        ("frontAndBack", "empty"),
    ]
    assert facts.cell_zones[0].name == "zoneA"
    assert facts.cell_zones[0].element_count == 1
    assert facts.raw_content_included is False
```

Also assert bounding box in metres, owner/neighbour index consistency, boundary face coverage, gzip equivalence, dimension observations, malformed token rejection, and a serialized-size ceiling of 64 KiB for the fixture.

- [ ] **Step 2: Run and verify failure**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_poly_mesh_inspector.py -q -p no:cacheprovider
```

Expected: FAIL because `InputMeshFacts` is absent.

- [ ] **Step 3: Implement a bounded OpenFOAM token reader**

Parse only the header, top-level list counts, point tuples, owner/neighbour labels, boundary dictionaries, and zone label lists. Strip C/C++ comments and handle quoted tokens; reject `#include`, macro expansion, dynamic code, binary format, and unknown compression rather than attempting a universal dictionary parser.

Use these core models:

```python
class MeshPatchFact(StrictModel):
    name: str
    patch_type: str
    start_face: int = Field(ge=0)
    face_count: int = Field(ge=0)


class MeshZoneFact(StrictModel):
    name: str
    element_count: int = Field(ge=0)


class InputMeshFacts(StrictModel):
    schema_version: Literal[1] = 1
    bundle_manifest_sha256: str
    inspector_id: str
    inspector_version: str
    region: str | None
    points: int
    faces: int
    internal_faces: int
    cells: int
    bounding_box_m: BoundingBox
    patches: tuple[MeshPatchFact, ...]
    cell_zones: tuple[MeshZoneFact, ...]
    face_zones: tuple[MeshZoneFact, ...]
    point_zones: tuple[MeshZoneFact, ...]
    dimensionality_observations: tuple[str, ...]
    topology_observations: tuple[str, ...]
    warnings: tuple[str, ...]
    raw_content_included: Literal[False] = False
```

- [ ] **Step 4: Run focused tests**

Run Step 2.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/preprocessing tests/test_poly_mesh_inspector.py
git commit -m "feat: inspect authoritative polyMesh facts"
```

### Task 5: Add system-owned pre-authoring `checkMesh`

**Files:**
- Create: `src/foampilot/preprocessing/mesh_probe.py`
- Modify: `src/foampilot/preprocessing/models.py`
- Modify: `src/foampilot/preprocessing/__init__.py`
- Test: `tests/test_mesh_probe.py`
- Test: `tests/test_real_poly_mesh_probe_gate.py`

**Interfaces:**
- Produces: `ExecutedMeshFacts(schema_version=1, source="pre_authoring_probe", mesh_check: MeshCheckFact, metrics: MeshQualityReport)`.
- Produces: `probe_provided_mesh(case_root: Path, environment: EnvironmentSnapshot, runtime_config: RuntimeConfig, budget_seconds: int) -> ExecutedMeshFacts`.

- [ ] **Step 1: Write failing fake-runner and real-gate tests**

```python
def test_probe_owns_the_check_mesh_command(fake_runner) -> None:
    facts = probe_provided_mesh(...)
    assert fake_runner.commands == [
        NativeCommand(
            step_id="inspect-provided-mesh",
            stage="check",
            executable="checkMesh",
            args=[],
            mpi_ranks=1,
            timeout_seconds=60,
        )
    ]
    assert facts.mesh_check.mesh_ok is True
```

Mark the real gate `@pytest.mark.real_openfoam` and skip with `OPENFOAM10_NOT_AVAILABLE` when preflight fails. It stages the synthetic fixture, runs the canonical discovered `checkMesh`, and asserts return code 0 and `mesh_ok=True`.

- [ ] **Step 2: Run deterministic tests and verify failure**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_mesh_probe.py -q -p no:cacheprovider
```

Expected: FAIL because the probe is absent.

- [ ] **Step 3: Implement the probe through the existing Runner boundary**

The system writes only the minimum valid `system/controlDict` needed by `checkMesh`, calls the canonical executable path through the same runtime policy as solve, records stdout/stderr, and converts output once through the Phase 1 `MeshCheckExtractor`. Treat this extractor as the temporary canonical mesh-check parser; Phase 4 must move it into the unified evidence registry rather than add a second parser. Do not accept any model-authored command.

- [ ] **Step 4: Run deterministic and real gates**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_mesh_probe.py -q -p no:cacheprovider
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_real_poly_mesh_probe_gate.py -q -p no:cacheprovider
```

Expected: deterministic PASS; real PASS on this configured host or an explicit environment skip.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/preprocessing tests/test_mesh_probe.py \
  tests/test_real_poly_mesh_probe_gate.py
git commit -m "feat: probe provided meshes before authoring"
```

### Task 6: Integrate bundle staging and facts into the canonical solve path

**Files:**
- Modify: `src/foampilot/tasks/io.py`
- Modify: `src/foampilot/agent/generation.py`
- Modify: `src/foampilot/plans/validation.py`
- Modify: `src/foampilot/agent/native_orchestrator.py`
- Modify: `src/foampilot/workflow/lineage.py`
- Modify: `src/foampilot/performance/derived_cache.py`
- Test: `tests/test_task_spec.py`
- Test: `tests/test_native_case_generation.py`
- Test: `tests/test_execution_plan.py`
- Test: `tests/test_continuation.py`
- Test: `tests/test_derived_cache.py`
- Test: `tests/test_native_agent_state_machine.py`

**Interfaces:**
- Consumes: asset registry, `InputMeshFacts`, and `ExecutedMeshFacts` from Tasks 1–5.
- Produces run files `asset-bundles.json`, `input-mesh-facts.json`, and optional `pre-authoring-mesh-facts.json` before model generation.
- Updates model context to include facts hashes and compact fact payload only.

- [ ] **Step 1: Add failing integration and overwrite-guard tests**

```python
def test_model_cannot_overwrite_any_bundle_member() -> None:
    plan = _plan(files=[GeneratedFile(
        path="constant/polyMesh/points",
        content="replacement",
    )])
    issues = validate_execution_plan(plan, _provided_mesh_task(), {"checkMesh", "pisoFoam"})
    assert "PUBLIC_ASSET_OVERWRITE" in {item.code for item in issues}


def test_provided_mesh_facts_exist_before_first_model_call(scripted_gateway, tmp_path):
    outcome = _agent(scripted_gateway, tmp_path).solve(...)
    request = scripted_gateway.requests[0]
    assert (outcome.run_dir / "input-mesh-facts.json").is_file()
    assert "points\n(" not in request.user_prompt
```

Add fingerprint tests proving zone/member changes reject strict resume, and a derived-cache test proving `mesh.strategy=provided` does not require a model-authored mesh command.

- [ ] **Step 2: Run focused integration tests and verify failure**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_task_spec.py tests/test_native_case_generation.py \
  tests/test_execution_plan.py tests/test_continuation.py \
  tests/test_derived_cache.py tests/test_native_agent_state_machine.py \
  -q -p no:cacheprovider
```

Expected: FAIL on directory staging, fact persistence, and bundle overwrite protection.

- [ ] **Step 3: Route all public assets through the adapter registry**

Replace direct file-copy assumptions in `stage_public_assets` with:

```python
def stage_public_assets(
    task: TaskSpec,
    source_root: str | Path,
    case_root: str | Path,
    *,
    registry: CapabilityRegistry | None = None,
) -> list[StagedAsset]: ...
```

Persist the two fact artifacts before building route/context. Add every bundle member to the reserved asset-path set used by plan validation and materialization. Hash the bundle manifest plus inspector identity in resume and cache fingerprints.

- [ ] **Step 4: Run focused and full deterministic suites**

Run the Step 2 command, then:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/foampilot/tasks/io.py src/foampilot/agent/generation.py \
  src/foampilot/plans/validation.py src/foampilot/agent/native_orchestrator.py \
  src/foampilot/workflow/lineage.py src/foampilot/performance/derived_cache.py \
  tests/test_task_spec.py tests/test_native_case_generation.py \
  tests/test_execution_plan.py tests/test_continuation.py \
  tests/test_derived_cache.py tests/test_native_agent_state_machine.py
git commit -m "feat: make mesh facts authoritative before generation"
```

### Task 7: Phase 1 user-facing docs and real vertical gate

**Files:**
- Modify: `README.md`
- Modify: `docs/independent-agent-quickstart.md`
- Modify: `docs/architecture.md`
- Create: `examples/tasks/provided-poly-mesh.yaml`
- Create: `tests/test_real_provided_mesh_vertical_gate.py`

**Interfaces:**
- Consumes all Phase 1 interfaces.
- Produces one documented CLI route for declaring a polyMesh directory and observing pre-authoring mesh facts.

- [ ] **Step 1: Add a failing documentation contract test**

```python
def test_quickstart_documents_poly_mesh_as_one_directory_asset() -> None:
    text = Path("docs/independent-agent-quickstart.md").read_text()
    assert "--asset-dir" in text
    assert "input-mesh-facts.json" in text
    assert "constant/polyMesh/points" not in text
```

- [ ] **Step 2: Run the documentation and real vertical tests**

Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  tests/test_repository_docs.py tests/test_real_provided_mesh_vertical_gate.py \
  -q -p no:cacheprovider
```

Expected: documentation test FAIL before updates; real gate FAIL or skip before its implementation.

- [ ] **Step 3: Document the canonical workflow and implement the gate**

The real gate must use the synthetic mesh fixture or another independently authored mesh, never copy an official tutorial. It proves: directory declaration, atomic staging, compact facts, system `checkMesh`, no model overwrite, and a model request that contains facts rather than raw mesh contents.

- [ ] **Step 4: Run Phase 1 release gates**

Run:

```bash
git diff --check
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests
```

Expected: all deterministic tests pass; real gate passes on the configured Foundation v10 host or reports its explicit skip reason.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/independent-agent-quickstart.md docs/architecture.md \
  examples/tasks/provided-poly-mesh.yaml tests/test_repository_docs.py \
  tests/test_real_provided_mesh_vertical_gate.py
git commit -m "docs: publish provided mesh workflow"
```
