# TaskBuilder Code and Test Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan
> task-by-task. Do not delegate interpretation of `AGENTS.md` or `docs/architecture.md`; the primary agent
> must read both completely before editing.

**Goal:** Reduce TaskBuilder implementation and test duplication while preserving every current responsibility,
public contract, provenance rule, failure-closed gate and externally observable result.

**Architecture:** Keep `extract_task_draft()` as the only public extraction entrypoint. Move response protocol,
source authority, provided polyMesh reconciliation, public geometry reconciliation and deterministic question
rebuilding into package-internal single-responsibility modules. Keep the pipeline strictly serial and return
immutable Pydantic values between stages; do not add a second extractor, mutable global state or side effects.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, pytest, Foundation OpenFOAM 10; Qt tests run with
`QT_QPA_PLATFORM=offscreen`.

**Behavior baseline:** production commit `55ab25f`; architecture baseline `docs/architecture.md`; expected
pre-refactor full suite `1220 passed, 13 skipped`. These identifiers are comparison points, not substitutes for
fresh verification.

---

## Global constraints

- Work on the current `main` branch. Do not create a branch, upgrade the version, tag, push or publish.
- This is an equivalence refactor. Do not fix newly noticed product behavior in the same commit.
- Preserve `foampilot.taskbuilder.extract_task_draft` and every name in `taskbuilder.__all__`.
- Preserve `TaskDraft v2`, `TaskSpec v3`, schema vocabulary, CLI options, exit codes, error codes and Chinese
  recovery text.
- Do not infer polyMesh units, expose raw mesh contents, grant authority to model labels or loosen conflict
  handling.
- Do not alter `NativeAgent`, workflow, CLI, Desktop, observation, acceptance, runner or runtime behavior.
- Do not copy implementations into new files and retain compatibility implementations in `extraction.py`.
- Keep model requests serial and keep the current single `TASK_EXTRACTION` call.
- Run the listed focused tests after every task. Stop the current task on a regression; do not accumulate
  unrelated changes hoping the final suite will explain it.
- Use `apply_patch` for edits and preserve user-owned changes. Commit only after the complete final gate.

## Fixed responsibility and test map

| Current symbol/area | Target owner | Input -> output | Equivalent test owner |
|---|---|---|---|
| `ExtractableFactPath`, `_Extracted*`, `_INPUT_QUESTION_PATHS`, `_SYSTEM_PROMPT` | `taskbuilder/extraction_protocol.py` | model schema/prompt -> validated extraction response | `tests/taskbuilder/test_extraction_protocol.py` |
| `_normalized_extracted_facts`, aliases, evidence/value helpers, source downgrade loop | `taskbuilder/authority.py` | extracted facts + request -> authoritative/audit `TaskFact` | `tests/taskbuilder/test_authority.py` |
| `_provided_mesh_route` | `taskbuilder/provided_mesh.py` | facts/questions/assets/topology/request -> reconciled facts/questions | `tests/taskbuilder/test_provided_mesh.py` |
| `_PUBLIC_FILE_GEOMETRY`, `_public_file_geometry_route` | `taskbuilder/public_geometry.py` | facts/questions/assets/bundles/request -> reconciled facts/questions | `tests/taskbuilder/test_public_geometry.py` |
| `_ensure_input_questions` | `taskbuilder/questions.py` | facts/questions/assets -> canonical input questions | route tests plus `tests/taskbuilder/test_questions.py` |
| `_draft_id`, gateway call, protected-path checks, serial stage calls, assumptions/status/TaskDraft assembly | `taskbuilder/extraction.py` | request + assets + ingress + gateway -> `TaskDraft` | `tests/taskbuilder/test_extraction.py` |

All new modules are internal implementation details. Do not export them from `taskbuilder.__init__`; only
`extract_task_draft` remains public.

---

### Task 1: Establish a fresh baseline and the test support layer

**Files:**

- Create: `tests/taskbuilder/conftest.py`
- Create: `tests/taskbuilder/__init__.py` only if imports require a package
- Modify later, not yet: `tests/test_task_extractor.py`

- [ ] Read `docs/current-state.md`, `AGENTS.md`, `docs/architecture.md` and the approved consolidation design
  completely. Record `git status --short` and `git log -8 --oneline --decorate` in the handoff notes.
- [ ] Run the focused baseline exactly:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -m pytest \
  -q -p no:cacheprovider \
  tests/test_task_extractor.py \
  tests/test_task_draft.py \
  tests/test_task_draft_validation.py \
  tests/test_task_compiler.py \
  tests/test_taskbuilder_cli.py \
  tests/test_taskbuilder_semantics.py \
  tests/test_asset_contracts.py \
  tests/test_poly_mesh_inspector.py \
  tests/test_desktop_workspace.py
```

- [ ] Run the full baseline with `QT_QPA_PLATFORM=offscreen`; confirm any difference from `1220 passed,
  13 skipped` before editing.
- [ ] In `tests/taskbuilder/conftest.py`, extract reusable pytest fixtures/factories for:
  `RecordingExtractionGateway`, the Task Extraction `ModelBudgetWindow`, base response payload, verified public
  file ingress context, atomic provided-mesh asset and compact `PolyMeshTopologyFacts` payload.
- [ ] Factories must accept explicit overrides; do not hide test-specific evidence, source, role names, mesh
  strategy or topology names in a global fixture.
- [ ] Keep `gateway.requests` observable and retain the assertion that its budget stage is
  `ModelStage.TASK_EXTRACTION`.
- [ ] Convert only two representative existing tests to the new fixtures, then run those tests. This proves the
  fixture API before the giant test file is split.
- [ ] Do not delete the old local helpers until every moved test has stopped using them.

**Expected fixture shape:**

```python
@pytest.fixture
def extraction_gateway_factory():
    def build(payload):
        return RecordingExtractionGateway(payload)
    return build

@pytest.fixture
def extraction_payload_factory():
    def build(*, source="user_text", confirmed=True, **fact_overrides):
        ...
    return build
```

The exact implementation may differ, but every test must keep its semantically relevant values visible at the
call site.

---

### Task 2: Extract the model protocol without changing the call boundary

**Files:**

- Create: `src/foampilot/taskbuilder/extraction_protocol.py`
- Modify: `src/foampilot/taskbuilder/extraction.py`
- Create: `tests/taskbuilder/test_extraction_protocol.py`
- Modify: `tests/test_task_extractor.py`

- [ ] Move, without semantic edits, `ExtractableFactPath`, `_INPUT_QUESTION_PATHS`, `_ExtractedFact`,
  `_ExtractedAssumption`, `_ExtractedQuestion`, `_ExtractedTaskDraft` and `_SYSTEM_PROMPT` into
  `extraction_protocol.py`.
- [ ] Keep `extra="forbid"`, JSON-text validators, unique assumption/question IDs and exact fact path vocabulary.
- [ ] Import these internal values in `extraction.py`; do not re-export them from package `__init__.py`.
- [ ] Move these existing tests into `tests/taskbuilder/test_extraction_protocol.py` without changing assertions:
  - `test_extraction_response_schema_encodes_arbitrary_fact_values_as_json_text`
  - `test_extraction_transport_model_rejects_invalid_domain_path_early`
  - `test_extraction_transport_rejects_fact_path_outside_declared_vocabulary`
  - schema/prompt assertions currently inside `test_extractor_uses_structured_stage_for_chinese_request`
- [ ] Preserve the private test import from the new module only; no production caller may import
  `_ExtractedTaskDraft`.
- [ ] Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -m pytest \
  -q -p no:cacheprovider \
  tests/taskbuilder/test_extraction_protocol.py tests/test_task_extractor.py
```

- [ ] Use `rg` to prove each moved class/constant has exactly one definition.

---

### Task 3: Extract provenance and user-evidence authority

**Files:**

- Create: `src/foampilot/taskbuilder/authority.py`
- Modify: `src/foampilot/taskbuilder/extraction.py`
- Create: `tests/taskbuilder/test_authority.py`
- Modify: `tests/test_task_extractor.py`

- [ ] Move these pure responsibilities into `authority.py`: duplicate normalization; value aliases and exclusive
  groups; numeric tokenization; English/Chinese negation; scalar leaves and semantic keys; geometry component
  support; balanced quote verification; model-source downgrade and `TaskFact` construction.
- [ ] Introduce one explicit coordinator-level helper, for example:

```python
def reconcile_extracted_facts(
    extracted: list[_ExtractedFact], request: str
) -> list[TaskFact]:
    ...
```

  It must preserve the current sorted duplicate handling and the current rule that only deterministic ingress can
  mint `PUBLIC_ASSET`, only the UI/user can mint `USER_CONFIRMATION`, and only the compiler can mint defaults.
- [ ] Do not make evidence matching more permissive or more restrictive during this move.
- [ ] Move these tests to `tests/taskbuilder/test_authority.py`: the three duplicate-path tests; invented fact;
  missing/verbatim/unrelated evidence; compressible/incompressible; all five negation parameter IDs; scientific
  notation; semantic field name; boolean; nested values; public-asset forgery; balanced Chinese quotes; model
  `user_confirmation` forgery.
- [ ] Parameterize only cases that share the same contract and assertion. Retain readable IDs such as
  `chinese-prefix`, `chinese-phrase`, `english-not`, `nested-role` so a failure identifies the risk.
- [ ] Run the new authority tests plus `test_taskbuilder_semantics.py` and `test_task_compiler.py`.
- [ ] Confirm `authority.py` has no model gateway, filesystem, subprocess or TaskDraft assembly imports.

---

### Task 4: Extract provided polyMesh reconciliation

**Files:**

- Create: `src/foampilot/taskbuilder/provided_mesh.py`
- Modify: `src/foampilot/taskbuilder/extraction.py`
- Create: `tests/taskbuilder/test_provided_mesh.py`
- Modify: `tests/test_task_extractor.py`

- [ ] Move `_provided_mesh_route` to a package-internal function named clearly, for example
  `reconcile_provided_mesh(...)`. Keep its arguments explicit: facts, questions, assets, ingress context and
  normalized request.
- [ ] Reuse authority helpers for trusted evidence and geometry components; do not copy alias or evidence code.
- [ ] Preserve all current outcomes: `openfoam_mesh`, `provided`, public-asset evidence, separate user unit and
  dimensionality facts, empty-patch conflict, exact patch/region role topology matching, malformed role blocking,
  and one length-unit question when unit is absent.
- [ ] Move the eight provided-mesh tests beginning with
  `test_provided_mesh_reconciliation_removes_design_and_topology_questions` through
  `test_provided_mesh_rejects_malformed_role_shape_without_crashing`.
- [ ] Replace their repeated topology payloads with explicit fixture overrides. Keep each patch/zone name visible
  in the test body; never use porousBlockage-specific production logic.
- [ ] Run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -m pytest \
  -q -p no:cacheprovider \
  tests/taskbuilder/test_provided_mesh.py \
  tests/test_poly_mesh_inspector.py \
  tests/test_asset_contracts.py \
  tests/test_task_compiler.py
```

- [ ] Confirm `provided_mesh.py` never reads raw mesh files and never mutates/stages assets; it consumes only
  declared `PublicAsset` plus `TaskIngressContext` facts.

---

### Task 5: Extract public STL/OBJ/GEO geometry reconciliation

**Files:**

- Create: `src/foampilot/taskbuilder/public_geometry.py`
- Modify: `src/foampilot/taskbuilder/extraction.py`
- Create: `tests/taskbuilder/test_public_geometry.py`
- Modify: `tests/test_task_extractor.py`

- [ ] Move `_PUBLIC_FILE_GEOMETRY` and `_public_file_geometry_route` into the new module with an explicit
  `reconcile_public_geometry(...)` function.
- [ ] Reuse authority helpers; do not duplicate source/evidence logic.
- [ ] Preserve deterministic recognition of `.stl`, `.obj` and `.geo`, auxiliary non-geometry assets, mode
  conflicts, compatible strategy rules, generated Gmsh strategy, user roles for later probe, and model question
  ID rebuilding.
- [ ] Move these tests: public file authority parameterization, auxiliary asset, conflicting strategy, preserved
  user roles, rebuilt question IDs and forged conflict ID.
- [ ] Run new public-geometry tests with asset contracts, TaskDraft validation and compiler tests.
- [ ] Confirm this module does not inspect arbitrary files itself; it consumes the asset bundles already verified
  by deterministic ingress.

---

### Task 6: Extract deterministic question rebuilding

**Files:**

- Create: `src/foampilot/taskbuilder/questions.py`
- Modify: `src/foampilot/taskbuilder/extraction.py`
- Create: `tests/taskbuilder/test_questions.py`
- Modify: route test files as needed

- [ ] Move `_ensure_input_questions` into `questions.py` as one pure function. Keep effective geometry projection,
  authoritative source filtering, deterministic conflict IDs, asset-reference checks, `GeometryInput` and
  `MeshIntent` validation in this owner.
- [ ] Move `test_extractor_discards_design_owned_model_questions` and the two question-ID tests to the new owner,
  unless a route-specific assertion remains clearer in its route file.
- [ ] Add no new question types. Preserve canonical IDs and the rule that solver/material/time/resource design
  questions are discarded at TaskBuilder ingress.
- [ ] Run question, public geometry, provided mesh, TaskDraft validation, CLI and Desktop workspace tests.
- [ ] Confirm there is one and only one function that rebuilds final input questions.

---

### Task 7: Reduce `extraction.py` to a thin serial orchestrator

**Files:**

- Modify: `src/foampilot/taskbuilder/extraction.py`
- Create: `tests/taskbuilder/test_extraction.py`
- Delete after all moves: `tests/test_task_extractor.py`
- Verify: `src/foampilot/taskbuilder/__init__.py`

- [ ] Leave in `extraction.py` only: request normalization; blank/protected-path gates; `ModelRequest` assembly;
  one structured gateway call; protected model-output check; ordered calls to authority, provided mesh, public
  geometry and questions; assumption conversion; status calculation; deterministic draft ID; final TaskDraft
  construction.
- [ ] Preserve the exact stage order:

```text
validate public request
-> one structured model call
-> validate protected output
-> normalize/reconcile fact authority
-> provided polyMesh reconciliation
-> public file geometry reconciliation
-> deterministic input-question rebuilding
-> status and TaskDraft assembly
```

- [ ] Move the remaining integration tests to `tests/taskbuilder/test_extraction.py`: structured Chinese request;
  declared metadata only; compact topology context; deterministic ingress size limit; protected path before call;
  protected path in output.
- [ ] Remove `tests/test_task_extractor.py` only when `pytest --collect-only` shows all 45 original test cases or
  named parameter IDs have an explicit new owner. Test count may change only through documented parameterization,
  never through lost risk cases.
- [ ] Target `extraction.py <= 300` lines and each responsibility module roughly `<= 400` lines. If clean code
  needs more, document why; do not create empty forwarding modules to satisfy line counts.
- [ ] Run the entire TaskBuilder/asset/Desktop focused set from Task 1.
- [ ] Run import-boundary and repository-doc tests; the public `taskbuilder.__all__` must be unchanged.

---

### Task 8: Perform the deletion and duplication audit

**Files:**

- Modify: `docs/architecture.md`
- Modify: `docs/current-state.md`
- Modify only if actual decisions changed: the approved consolidation design

- [ ] Produce a temporary mapping table with every moved symbol, old location, new owner and tests. Compare it to
  the fixed map at the top of this plan.
- [ ] Run `rg` for every old private symbol and prove there is only one implementation; imports and tests may
  reference it, but duplicate definitions are forbidden.
- [ ] Search for copied blocks among the six TaskBuilder modules and shared fixtures. Merge only pure identical
  helpers into the responsibility owner; do not create a generic `utils.py` dumping ground.
- [ ] Review all test deletions. For each removed case, record the retained parameterized case ID and why input
  contract, risk and owner are identical. Restore any case that cannot meet all three conditions.
- [ ] Update the file-level responsibility table in `docs/architecture.md` with all five new production modules
  and the thin `extraction.py` role.
- [ ] Update `docs/current-state.md` with new line counts, test organization and fresh test results. Keep the
  previously recorded capability matrix unchanged unless a gate disproves it.
- [ ] Do not edit historical reports to make old test counts current.

---

### Task 9: Run final deterministic and distribution gates

**Files:**

- Verify only: full repository and `dist/`

- [ ] Review changes before testing:

```bash
git status --short
git diff --stat
git diff --check
git diff -- src/foampilot/taskbuilder tests/taskbuilder tests/test_task_extractor.py \
  docs/architecture.md docs/current-state.md
```

- [ ] Compile and run focused tests:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -m compileall -q src tests

PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -m pytest \
  -q -p no:cacheprovider \
  tests/taskbuilder \
  tests/test_task_draft.py \
  tests/test_task_draft_validation.py \
  tests/test_task_compiler.py \
  tests/test_taskbuilder_cli.py \
  tests/test_taskbuilder_semantics.py \
  tests/test_asset_contracts.py \
  tests/test_poly_mesh_inspector.py \
  tests/test_desktop_workspace.py \
  tests/test_repository_docs.py \
  tests/test_import_boundary.py
```

- [ ] Run the complete deterministic/Qt-offscreen suite:

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  /home/edwin/feal-venv-py312/bin/python -m pytest \
  -q -p no:cacheprovider tests
```

- [ ] Build fresh artifacts **without deleting unknown files**. First inspect `dist/`; move the two known
  `foampilot-0.2.0*` artifacts to a new `mktemp -d` backup. The current environment has no `build` module, so
  use the repository's verified setuptools/pip route: build an sdist with `build_meta`, then build the wheel from
  that sdist rather than the source tree:

```bash
/home/edwin/feal-venv-py312/bin/python -c \
  'from setuptools import build_meta; print(build_meta.build_sdist("dist"))'
/home/edwin/feal-venv-py312/bin/python -m pip wheel \
  dist/foampilot-0.2.0.tar.gz --no-deps --no-build-isolation \
  --wheel-dir /tmp/foampilot-consolidation-wheel
cp /tmp/foampilot-consolidation-wheel/foampilot-0.2.0-py3-none-any.whl dist/
FOAMPILOT_VERIFY_DISTRIBUTION=1 PYTHONPATH=src \
  /home/edwin/feal-venv-py312/bin/python -m pytest \
  -q -p no:cacheprovider tests/test_distribution_contents.py
```

- [ ] If the exact versioned artifact names differ, inspect the generated filenames and substitute only those
  explicit paths; do not use a broad destructive glob. Do not substitute an editable install for the distribution
  gate.
- [ ] Install the wheel into a fresh `mktemp -d` target with `pip install --no-deps --target <target> <wheel>`;
  run Python from outside the repository and assert imports for `foampilot`, public
  `foampilot.taskbuilder.extract_task_draft`, and the five new internal modules resolve from that target.
- [ ] Record wheel/sdist SHA256 and import paths in `docs/current-state.md` or the final handoff; these hashes are
  revision-specific and must not overwrite the historical v0.2.0 release report.

---

### Task 10: Revalidate the real polyMesh input boundary and commit

**Files:**

- Verify: the external porousBlockage asset and TaskDraft behavior
- Modify only for fresh evidence: `docs/current-state.md`

- [ ] If `/tmp/foampilot-porous-ingress-rerun2-20260813/task-draft.yaml` still exists, verify its SHA256 equals
  `d947d1264c0da6fe3e7f6d15b2dd90ddec67598754544db8ad72ae361dade185` and run:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -m foampilot.cli.main \
  task validate-draft \
  /tmp/foampilot-porous-ingress-rerun2-20260813/task-draft.yaml --json
```

- [ ] Confirm the only blocking issue is `TASK_UNIT_AMBIGUOUS` at `geometry.length_unit`; four resource defaults
  may remain advisory. Do not call this a new solve.
- [ ] If the temp artifact is absent, do not fabricate it. Verify the deterministic equivalent through the
  provided-mesh tests, then optionally regenerate a real draft only if the original request file and model backend
  remain available. The source asset is:
  `/home/edwin/workplace/openfoam-v2512-selected-100-results-from-server-20260807/`
  `case-incompressible-pisofoam-laminar-porousblockage-205447969d3f/mesh/openfoam/constant/polyMesh`.
- [ ] If regenerating, use asset root
  `/home/edwin/workplace/openfoam-v2512-selected-100-results-from-server-20260807/`
  `case-incompressible-pisofoam-laminar-porousblockage-205447969d3f`, asset-dir
  `mesh/openfoam/constant/polyMesh`, and install path `constant/polyMesh`. The original request copy, if still
  present, is `/tmp/foampilot-porous-phase5-20260813/request.md` with SHA256
  `119ddebe47f5038bcb2442bc4cd23fdf6700aa22f964ad30b877ac465bc156c8`.
- [ ] Do not run a new CFD solve or qualification in this consolidation task. The required real boundary is input
  recognition and truthful unit blocking; existing solve evidence remains separately documented.
- [ ] Reread every changed production file and the final test split. Run `git diff --check` and inspect
  `git status --short` one final time.
- [ ] Commit the exact refactor/docs set on `main` with a scoped message such as
  `refactor: consolidate taskbuilder extraction`. Do not include `dist/`, `.foampilot/`, `/tmp` artifacts or
  unrelated user files.
- [ ] After commit, verify `git status --short` is empty and report: commit hash; source/test line-count delta;
  retained test scenario count; focused/full/distribution outputs; artifact hashes; real TaskDraft validation;
  and every deferred issue discovered but not fixed.

## Completion definition

The work is complete only when all of the following are simultaneously true:

1. `extraction.py` is a thin serial orchestrator and each moved responsibility has exactly one owner;
2. public imports, schemas, model request count, status/error behavior and authority boundaries are unchanged;
3. all 45 original extractor test scenarios have an explicit new owner or documented named parameter equivalent;
4. focused, full, distribution and external wheel import gates pass with fresh evidence;
5. the real provided-polyMesh draft still stops only for unknown length unit;
6. architecture/current-state docs match the final file tree;
7. the commit contains no new capability and the worktree is clean.
