# TaskBuilder Code and Test Consolidation Implementation Plan

Status: **Complete; implementation, delivery gates and local main commit finished**

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan
> task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The primary agent must read
> `docs/current-state.md`, `AGENTS.md`, `docs/architecture.md` and the approved consolidation design completely;
> do not delegate their interpretation.

**Goal:** Reduce TaskBuilder implementation and test duplication without changing behavior, while preserving one
explicit responsibility, dependency direction and side-effect boundary for every production file.

**Architecture:** Keep `extract_task_draft()` as the only model-backed extraction entrypoint and the only owner of
the serial pipeline. Move transport protocol, source authority, provided-polyMesh reconciliation, public-file
geometry reconciliation and deterministic input-question reconstruction into package-internal modules with fixed
interfaces. Keep the existing 45 extractor scenarios stationary while production code moves; reorganize tests only
after the production split is stable.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, pytest, Foundation OpenFOAM 10; Qt tests use
`QT_QPA_PLATFORM=offscreen`.

## Global Constraints

- Work directly on the current `main` branch. Do not create a branch or worktree, upgrade the version, tag, push or
  publish.
- Preserve user-owned changes. Before every edit, inspect `git status --short`; if an unexpected overlapping edit
  appears, stop that task and report it.
- This is an equivalence refactor. Do not fix newly noticed product behavior in the same change.
- Preserve `foampilot.taskbuilder.extract_task_draft` and every name in `foampilot.taskbuilder.__all__`.
- Preserve TaskDraft v2, TaskSpec v3, extraction schema vocabulary, prompt meaning, CLI options, JSON structure,
  exit codes, stable English codes and Chinese recovery text.
- Preserve exactly one logical `TASK_EXTRACTION` model request and the existing serial stage order.
- Do not infer polyMesh units, expose raw mesh contents, grant authority to model labels, loosen conflicts or add a
  fallback path.
- Do not alter `NativeAgent`, workflow, CLI, Desktop, observations, acceptance, Runner, runtime or qualification
  behavior.
- Do not copy an implementation into a new file while retaining a compatibility implementation in its old owner.
- A line-count target never overrides a file responsibility. If a move requires the target file to cross the
  responsibility table below, stop the move and report the conflict.
- Run the original black-box extractor tests after each production move. A regression must be resolved in that
  task; do not accumulate failures for the final suite.
- Use `apply_patch` for edits. Commit only after all final gates pass; do not push.

## Fixed Production Responsibility Map

| File | Sole responsibility | May depend on | Must not depend on or perform |
|---|---|---|---|
| `taskbuilder/extraction_protocol.py` | model response schema, extractable fact vocabulary and system prompt | Pydantic, `FactSource` | gateway calls, asset routing, authority decisions, question policy |
| `taskbuilder/authority.py` | normalize extracted facts and bind source/evidence authority | protocol models, `TaskFact` | gateway, filesystem, assets/topology reconciliation, questions, TaskDraft assembly |
| `taskbuilder/provided_mesh.py` | reconcile verified native polyMesh facts into geometry/mesh facts | authority helpers, `TaskIngressContext`, `PublicAsset` | raw file reads, asset staging, model calls, final validation |
| `taskbuilder/public_geometry.py` | reconcile verified STL/OBJ/GEO metadata into geometry/mesh facts | authority helpers, ingress metadata | arbitrary file inspection, gateway, final validation |
| `taskbuilder/questions.py` | own input-question path policy and rebuild final input questions | projection, task contracts | model calls, I/O, source minting, DraftReview construction |
| `taskbuilder/projection.py` | pure authority-aware fact and geometry projections shared downstream | TaskDraft/TaskFact models | question generation, I/O, model calls |
| `taskbuilder/extraction.py` | validate request, make one model call, invoke stages serially, assemble TaskDraft | all modules above, model gateway | embedded evidence/geometry/question algorithms, execution |

Only `extraction.py` may import `ModelGateway`, `ModelRequest`, `ModelBudgetWindow` or `ModelTraceSink`. None of
the new modules may import `foampilot.runtime`, `plans`, `agent`, `workflow`, `desktop`, `cli` or `qualification`.

## Fixed Interfaces

| Owner | Exact interface |
|---|---|
| `authority.py` | `reconcile_extracted_facts(extracted: list[_ExtractedFact], request: str) -> list[TaskFact]` |
| `authority.py` | `verified_user_evidence(evidence: str, request: str) -> bool` |
| `authority.py` | `geometry_component_supported(value: object, evidence: str, *, trusted_confirmation: bool) -> bool` |
| `provided_mesh.py` | `reconcile_provided_mesh(*, facts: list[TaskFact], questions: list[TaskQuestion], assets: list[PublicAsset], context: TaskIngressContext, request: str) -> tuple[list[TaskFact], list[TaskQuestion]]` |
| `public_geometry.py` | `reconcile_public_geometry(*, facts: list[TaskFact], questions: list[TaskQuestion], assets: list[PublicAsset], context: TaskIngressContext, request: str) -> tuple[list[TaskFact], list[TaskQuestion]]` |
| `projection.py` | `compilable_fact_map_from_facts(facts: Iterable[TaskFact]) -> dict[str, TaskFact]` |
| `questions.py` | `INPUT_QUESTION_PATHS: frozenset[str]` |
| `questions.py` | `rebuild_input_questions(facts: list[TaskFact], questions: list[TaskQuestion], assets: list[PublicAsset]) -> list[TaskQuestion]` |

These are package-internal interfaces and must not be added to `taskbuilder.__all__`.

---

### Task 1: Establish the fresh baseline and scenario inventory

**Files:**

- Verify: repository only
- Temporary output: a directory created with `mktemp -d`

**Interfaces:**

- Consumes: current `main` at the beginning of implementation
- Produces: fresh focused/full baseline and a normalized inventory of the 45 extractor scenario IDs

- [x] Read the four authoritative documents completely and record the current status and recent commits:

```bash
git status --short
git log -8 --oneline --decorate
git diff --check
```

Expected before implementation: `HEAD` remains `9afb78b`; the worktree may contain only the reviewed revisions to
this plan and its design specification. Any additional path must be identified as a later user-owned change and
preserved before implementation starts.

- [x] Save the original collected scenario IDs outside the repository:

```bash
TASKBUILDER_AUDIT_DIR=$(mktemp -d /tmp/foampilot-taskbuilder-audit.XXXXXX)
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -m pytest \
  --collect-only -q -p no:cacheprovider tests/test_task_extractor.py \
  > "$TASKBUILDER_AUDIT_DIR/original-collect.txt"
sed -n 's#^tests/test_task_extractor.py::##p' \
  "$TASKBUILDER_AUDIT_DIR/original-collect.txt" \
  > "$TASKBUILDER_AUDIT_DIR/original-scenarios.txt"
wc -l "$TASKBUILDER_AUDIT_DIR/original-scenarios.txt"
```

Expected: `45` normalized scenarios.

- [x] Run the focused baseline exactly:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
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

Expected comparison point: `126 passed`.

- [x] Run the complete deterministic/Qt-offscreen baseline:

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests
```

Expected comparison point: `1220 passed, 13 skipped`. Investigate any difference before editing.

---

### Task 2: Extract explicit test support without moving scenarios

**Files:**

- Create: `tests/support/taskbuilder.py`
- Modify: `tests/test_task_extractor.py`
- Verify: `tests/support/__init__.py` remains unchanged unless the repository already exports helpers there

**Interfaces:**

- Consumes: payload dictionaries and `PublicAsset` objects
- Produces: `RecordingExtractionGateway`, budget/payload factories and explicit public-file/provided-mesh
  asset/context factories

- [x] Move the current gateway and three helpers from `tests/test_task_extractor.py` into
  `tests/support/taskbuilder.py`, preserving behavior. Use ordinary functions, not implicit pytest fixtures. The
  exported test-support surface is exactly `RecordingExtractionGateway`,
  `task_extraction_budget() -> ModelBudgetWindow`,
  `extraction_payload(*, facts: list[dict[str, object]] | None = None, assumptions: list[dict[str, object]] | None = None, unresolved_questions: list[dict[str, object]] | None = None, source: str = "user_text", confirmed: bool = True) -> dict[str, object]`,
  `file_ingress_context(*assets: PublicAsset) -> TaskIngressContext`,
  `provided_mesh_asset(*, path: str = "mesh/native", manifest_sha256: str = "c" * 64, install_path: str = "constant/polyMesh") -> PublicAsset`,
  `poly_mesh_topology_payload(*, manifest_sha256: str = "c" * 64, region: str | None = None, patches: list[dict[str, object]] | None = None, cell_zones: list[dict[str, object]] | None = None, bounds: dict[str, list[float]] | None = None) -> dict[str, object]`, and
  `provided_mesh_ingress_context(*topologies: dict[str, object]) -> TaskIngressContext`.

- [x] Replace the four local definitions and repeated provided-mesh asset/topology construction with these
  factories. Every test must pass its semantically relevant patch names, zone names, region, bounds and conflicts
  explicitly at the call site. Do not move, rename, merge or parameterize any test scenario in this task.

- [x] Run all 45 tests and recollect them:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests/test_task_extractor.py
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -m pytest \
  --collect-only -q -p no:cacheprovider tests/test_task_extractor.py
```

Expected: 45 scenarios, all passing. This task changes test construction only.

---

### Task 3: Extract the model transport protocol

**Files:**

- Create: `src/foampilot/taskbuilder/extraction_protocol.py`
- Modify: `src/foampilot/taskbuilder/extraction.py`
- Test unchanged: `tests/test_task_extractor.py`

**Interfaces:**

- Consumes: model response payload
- Produces: `_ExtractedTaskDraft` schema and `_SYSTEM_PROMPT` for `extraction.py`

- [x] Move without semantic edits: `ExtractableFactPath`, `_ExtractedFact`, `_ExtractedAssumption`,
  `_ExtractedQuestion`, `_ExtractedTaskDraft` and `_SYSTEM_PROMPT`.

- [x] Leave `INPUT_QUESTION_PATHS` out of this module. The protocol describes what the model may return; it does
  not decide which questions survive deterministic reconstruction.

- [x] Update only imports in `extraction.py` and the private schema import in `tests/test_task_extractor.py`.
  Do not export protocol names from `taskbuilder/__init__.py`.

- [x] Prove one owner and run the stationary black-box tests:

```bash
rg -n '^(ExtractableFactPath =|class _ExtractedFact|class _ExtractedAssumption|class _ExtractedQuestion|class _ExtractedTaskDraft|_SYSTEM_PROMPT =)' \
  src/foampilot/taskbuilder
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests/test_task_extractor.py
```

Expected: one definition per moved symbol and 45 passing scenarios.

---

### Task 4: Extract source and evidence authority

**Files:**

- Create: `src/foampilot/taskbuilder/authority.py`
- Modify: `src/foampilot/taskbuilder/extraction.py`
- Test unchanged: `tests/test_task_extractor.py`

**Interfaces:**

- Consumes: `list[_ExtractedFact]` plus normalized request text
- Produces: authority-reconciled `list[TaskFact]` and two evidence helpers used by geometry routes

- [x] Move duplicate normalization, aliases, exclusive groups, numeric matching, English/Chinese negation,
  scalar leaves, semantic keys, value support, balanced-quote evidence verification and source downgrade into
  `authority.py`.

- [x] Replace the fact-construction loop in `extract_task_draft()` with exactly one call:

```python
facts = reconcile_extracted_facts(response.facts, normalized)
```

- [x] Preserve the current rules: deterministic ingress alone mints `PUBLIC_ASSET`; UI/user alone mints
  `USER_CONFIRMATION`; compiler alone mints defaults; medium/high model inference remains unconfirmed; conflicting
  duplicates downgrade rather than select a winner.

- [x] Check the file boundary and run focused tests:

```bash
rg -n 'ModelGateway|ModelRequest|ModelBudget|ModelTrace|TaskDraft|PublicAsset|TaskIngressContext|Path|subprocess' \
  src/foampilot/taskbuilder/authority.py
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_task_extractor.py tests/test_taskbuilder_semantics.py tests/test_task_compiler.py
```

Expected: the `rg` command finds none of the forbidden owners and all selected tests pass.

---

### Task 5: Extract provided-polyMesh reconciliation

**Files:**

- Create: `src/foampilot/taskbuilder/provided_mesh.py`
- Modify: `src/foampilot/taskbuilder/extraction.py`
- Test unchanged: `tests/test_task_extractor.py`

**Interfaces:**

- Consumes: already reconciled facts/questions, declared assets, immutable ingress topology and request text
- Produces: geometry/mesh authority plus unresolved input conflicts; performs no I/O

- [x] Move `_provided_mesh_route` to `reconcile_provided_mesh()` with the fixed signature at the top of this
  plan. Import `verified_user_evidence()` and `geometry_component_supported()` from `authority.py`; do not copy
  their implementations.

- [x] Preserve all current outputs: `openfoam_mesh`, `provided`, separate user unit/dimensionality facts,
  empty-patch inference, exact patch/region-name matching, malformed-role blocking and one missing-unit question.

- [x] Replace the orchestrator call, delete the old definition immediately, and prove the new owner does not read
  or stage assets:

```bash
rg -n '^def (_provided_mesh_route|reconcile_provided_mesh)' src/foampilot/taskbuilder
rg -n 'open\(|read_text|read_bytes|write_text|write_bytes|shutil|subprocess|ModelGateway|ModelRequest' \
  src/foampilot/taskbuilder/provided_mesh.py
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_task_extractor.py tests/test_poly_mesh_inspector.py \
  tests/test_asset_contracts.py tests/test_task_compiler.py
```

Expected: one route definition, no forbidden I/O/model owner and all selected tests pass.

---

### Task 6: Extract public-file geometry reconciliation

**Files:**

- Create: `src/foampilot/taskbuilder/public_geometry.py`
- Modify: `src/foampilot/taskbuilder/extraction.py`
- Test unchanged: `tests/test_task_extractor.py`

**Interfaces:**

- Consumes: verified `TaskIngressContext.asset_bundles`, declared assets, facts/questions and request text
- Produces: deterministic STL/OBJ/GEO geometry authority and conflicts; performs no file inspection

- [x] Move `_PUBLIC_FILE_GEOMETRY` and `_public_file_geometry_route` to the new module and rename the callable
  `reconcile_public_geometry()`.

- [x] Preserve recognition and conflict behavior for STL, OBJ, GEO, auxiliary assets, mixed modes, generated
  Gmsh strategy, explicit compatible strategies, missing provided mesh and user roles.

- [x] Replace the orchestrator call, delete the old definition immediately, and run:

```bash
rg -n '^(_PUBLIC_FILE_GEOMETRY =|def _public_file_geometry_route|def reconcile_public_geometry)' \
  src/foampilot/taskbuilder
rg -n 'open\(|read_text|read_bytes|write_text|write_bytes|subprocess|ModelGateway|ModelRequest' \
  src/foampilot/taskbuilder/public_geometry.py
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_task_extractor.py tests/test_asset_contracts.py \
  tests/test_task_draft_validation.py tests/test_task_compiler.py
```

Expected: one route definition, no I/O/model ownership and all selected tests pass.

---

### Task 7: Establish one authority projection and one input-question policy

**Files:**

- Modify: `src/foampilot/taskbuilder/projection.py`
- Create: `src/foampilot/taskbuilder/questions.py`
- Modify: `src/foampilot/taskbuilder/extraction.py`
- Modify: `src/foampilot/taskbuilder/validation.py`
- Test unchanged: `tests/test_task_extractor.py`

**Interfaces:**

- Consumes: `Iterable[TaskFact]` or complete facts/questions/assets lists
- Produces: one compilable-source projection and one final question list

- [x] Add `compilable_fact_map_from_facts()` to `projection.py` and implement existing
  `compilable_fact_map(draft)` by delegating to it. Preserve the exact source set and `confirmed` requirement:

```python
def compilable_fact_map_from_facts(
    facts: Iterable[TaskFact],
) -> dict[str, TaskFact]:
    return {
        item.path: item
        for item in facts
        if item.confirmed and item.source in _COMPILABLE_SOURCES
    }
```

- [x] Move `_ensure_input_questions` to `rebuild_input_questions()` in `questions.py`. Replace its local source
  filter with `compilable_fact_map_from_facts(facts)`.

- [x] Define the only input-question policy constant in `questions.py`:

```python
INPUT_QUESTION_PATHS = frozenset(
    {
        "geometry",
        "geometry.dimensionality",
        "geometry.length_unit",
        "geometry.patch_roles",
        "geometry.region_roles",
        "mesh",
    }
)
```

- [x] Make both `extraction.py` and `validation.py` import this constant. Delete both old duplicated set
  definitions. Keep DraftReview construction in `validation.py`; questions must not import validation.

- [x] Prove uniqueness, absence of a cycle and behavior equivalence:

```bash
rg -n '^(_INPUT_QUESTION_PATHS|INPUT_QUESTION_PATHS)' src/foampilot/taskbuilder
rg -n '^def (_ensure_input_questions|rebuild_input_questions|compilable_fact_map_from_facts)' \
  src/foampilot/taskbuilder
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -c \
  'import foampilot.taskbuilder.extraction; import foampilot.taskbuilder.validation'
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_task_extractor.py tests/test_task_draft_validation.py \
  tests/test_taskbuilder_cli.py tests/test_desktop_workspace.py
```

Expected: one policy constant, one reconstruction function, imports succeed and all tests pass.

---

### Task 8: Finish the thin serial orchestrator and enforce file boundaries

**Files:**

- Modify: `src/foampilot/taskbuilder/extraction.py`
- Modify: `tests/test_import_boundary.py`
- Verify: `src/foampilot/taskbuilder/__init__.py`
- Test unchanged: `tests/test_task_extractor.py`

**Interfaces:**

- Consumes: public request/assets/gateway/budget/trace/protected paths/ingress context
- Produces: one TaskDraft through the unchanged public `extract_task_draft()` signature

- [x] Leave in `extraction.py` only request normalization, protected-path gates, one `ModelRequest`, one
  structured gateway call, protected-output gate, ordered stage calls, assumption conversion, status, draft ID
  and TaskDraft assembly.

- [x] Preserve this exact order:

```text
validate public request
-> one structured model call
-> validate protected model output
-> reconcile fact authority
-> reconcile provided polyMesh
-> reconcile public-file geometry
-> rebuild deterministic input questions
-> assemble TaskDraft
```

- [x] Add an AST-based import-boundary test to `tests/test_import_boundary.py` which asserts that among the new
  TaskBuilder modules only `extraction.py` imports `foampilot.models`. Also assert none imports the forbidden
  execution/orchestration layers named in the global constraints.

- [x] Verify public exports, line ownership and behavior:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -c \
  'import foampilot.taskbuilder as t; assert "extract_task_draft" in t.__all__'
wc -l src/foampilot/taskbuilder/extraction.py \
  src/foampilot/taskbuilder/extraction_protocol.py \
  src/foampilot/taskbuilder/authority.py \
  src/foampilot/taskbuilder/provided_mesh.py \
  src/foampilot/taskbuilder/public_geometry.py \
  src/foampilot/taskbuilder/questions.py \
  src/foampilot/taskbuilder/projection.py
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_task_extractor.py tests/test_import_boundary.py tests/test_repository_docs.py
```

Expected: tests pass. `extraction.py` should be near or below 300 lines, but any larger result is acceptable when
every remaining block belongs to orchestration; do not split it mechanically to satisfy the number.

---

### Task 9: Reorganize tests only after production responsibilities are stable

**Files:**

- Create: `tests/test_taskbuilder_extraction_protocol.py`
- Create: `tests/test_taskbuilder_authority.py`
- Create: `tests/test_taskbuilder_provided_mesh.py`
- Create: `tests/test_taskbuilder_public_geometry.py`
- Create: `tests/test_taskbuilder_questions.py`
- Create: `tests/test_taskbuilder_extraction.py`
- Delete after inventory equality: `tests/test_task_extractor.py`
- Reuse: `tests/support/taskbuilder.py`

**Interfaces:**

- Consumes: the unchanged 45 black-box scenarios and explicit support factories
- Produces: responsibility-aligned root-level test files with the same normalized scenario names and parameter IDs

- [x] Move tests without changing assertions according to this ownership map:

| Destination | Existing scenarios |
|---|---|
| `test_taskbuilder_extraction_protocol.py` | response schema JSON text; invalid domain path; outside vocabulary |
| `test_taskbuilder_authority.py` | three duplicate cases; invented/missing/verbatim/unrelated evidence; compressibility; five negations; scientific notation; semantic/nested/boolean binding; source forgeries; balanced quote |
| `test_taskbuilder_provided_mesh.py` | eight provided-mesh reconciliation cases |
| `test_taskbuilder_public_geometry.py` | two asset-authority parameters; auxiliary asset; strategy conflict; roles |
| `test_taskbuilder_questions.py` | design-owned question filtering; rebuilt model question ID; forged conflict ID |
| `test_taskbuilder_extraction.py` | structured request/prompt; declared metadata; compact/size-limited context; protected input/output |

- [x] Do not merge or parameterize scenarios during the move. Keep every existing test function name and every
  parameter ID unchanged.

- [x] Collect the six files and normalize away their file prefixes:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -m pytest \
  --collect-only -q -p no:cacheprovider \
  tests/test_taskbuilder_extraction_protocol.py \
  tests/test_taskbuilder_authority.py \
  tests/test_taskbuilder_provided_mesh.py \
  tests/test_taskbuilder_public_geometry.py \
  tests/test_taskbuilder_questions.py \
  tests/test_taskbuilder_extraction.py \
  > "$TASKBUILDER_AUDIT_DIR/final-collect.txt"
sed -n 's#^tests/test_taskbuilder_[^:]*::##p' \
  "$TASKBUILDER_AUDIT_DIR/final-collect.txt" \
  | sort > "$TASKBUILDER_AUDIT_DIR/final-scenarios.txt"
sort "$TASKBUILDER_AUDIT_DIR/original-scenarios.txt" \
  > "$TASKBUILDER_AUDIT_DIR/original-scenarios.sorted.txt"
diff -u "$TASKBUILDER_AUDIT_DIR/original-scenarios.sorted.txt" \
  "$TASKBUILDER_AUDIT_DIR/final-scenarios.txt"
wc -l "$TASKBUILDER_AUDIT_DIR/final-scenarios.txt"
```

Expected: empty diff and `45` scenarios. Delete `tests/test_task_extractor.py` only after this equality passes.

- [x] Run all reorganized tests and the neighboring TaskBuilder suite. Any later consolidation of identical
  scenarios requires a separate documented review and is not part of this refactor.

---

### Task 10: Add a deterministic real-asset extractor gate

**Files:**

- Create: `tests/test_real_taskbuilder_ingress_gate.py`
- Reuse: `tests/support/taskbuilder.py`

**Interfaces:**

- Consumes: `FOAMPILOT_REAL_POLYMESH_CASE_ROOT`, the real `mesh/openfoam/constant/polyMesh`, a frozen extraction
  payload and the public TaskBuilder API
- Produces: a new TaskDraft that passes through `build_task_ingress_context()` and the refactored
  `extract_task_draft()` before validation

- [x] Add one opt-in test, skipped only when `FOAMPILOT_REAL_POLYMESH_CASE_ROOT` is unset. It must:

  1. resolve the case root and reject paths that do not contain `mesh/openfoam/constant/polyMesh`;
  2. compute the declared directory manifest from the actual regular members using the existing
     `BundleMember` and `compute_bundle_manifest_sha256` contracts;
  3. call `build_task_ingress_context()` so the production adapter and topology inspector verify the asset;
  4. call `extract_task_draft()` with a frozen payload that does not claim a length unit;
  5. call `validate_task_draft()` and assert the only blocking tuple is
     `("TASK_UNIT_AMBIGUOUS", "geometry.length_unit")`;
  6. assert geometry is `openfoam_mesh`, mesh strategy is `provided`, raw member contents are absent from the
     recorded model request and the gateway observed exactly one request.

- [x] Keep this file limited to the real ingress/extractor/validation boundary. It must not instantiate
  `NativeAgent`, call OpenFOAM, perform a solve or read qualification data.

- [x] Run it against the known local case:

```bash
FOAMPILOT_REAL_POLYMESH_CASE_ROOT=/home/edwin/workplace/openfoam-v2512-selected-100-results-from-server-20260807/case-incompressible-pisofoam-laminar-porousblockage-205447969d3f \
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests/test_real_taskbuilder_ingress_gate.py
```

Expected: `1 passed`. This is a new extractor execution, not a new CFD solve.

- [x] If the historical `/tmp/foampilot-porous-ingress-rerun2-20260813/task-draft.yaml` still exists, its
  `validate-draft` result may be checked as
  supplemental evidence only; it cannot replace this gate because it does not exercise the refactored extractor.

---

### Task 11: Audit responsibilities, duplicates and documentation

**Files:**

- Modify: `docs/architecture.md`
- Modify: `docs/current-state.md`
- Verify: all new production and test files

**Interfaces:**

- Consumes: final source tree and fresh test evidence
- Produces: accurate file-level responsibility catalog and completion record

- [x] Produce a temporary moved-symbol table with old owner, new owner, fixed interface, forbidden dependencies
  and equivalent test scenarios. Every moved symbol must map to exactly one new owner.

- [x] Search for duplicate definitions and copied policy:

```bash
rg -n '^(ExtractableFactPath =|class _Extracted|_SYSTEM_PROMPT =|_VALUE_ALIASES =|def reconcile_extracted_facts|def reconcile_provided_mesh|_PUBLIC_FILE_GEOMETRY =|def reconcile_public_geometry|INPUT_QUESTION_PATHS|def rebuild_input_questions)' \
  src/foampilot/taskbuilder
rg -n 'ModelGateway|ModelRequest|ModelBudgetWindow|ModelTraceSink' \
  src/foampilot/taskbuilder
```

Expected: one definition for each policy/implementation; model transport imports only in `extraction.py`.

- [x] Reread every new production file fully. For each file, check its imports and each function against the fixed
  responsibility table. If a file crosses its boundary, move the logic to the correct owner or stop and report;
  do not edit architecture documentation to legitimize an accidental crossing.

- [x] Update the architecture file-level catalog with the new modules and thin orchestrator. Update
  `current-state.md` with final line counts, test organization, fresh results and the new real-asset extractor
  evidence. Preserve the existing capability matrix and explicitly state that no new CFD solve occurred.

- [x] Run repository-doc and import-boundary tests after the documentation update.

---

### Task 12: Run final deterministic, distribution and clean-install gates

**Files:**

- Verify: full repository and `dist/`
- Do not add: build artifacts to git

**Interfaces:**

- Consumes: final source, tests and documentation
- Produces: fresh focused/full/distribution/clean-import evidence

- [x] Review the entire change before testing:

```bash
git status --short
git diff --stat
git diff --check
git diff -- src/foampilot/taskbuilder tests/support/taskbuilder.py \
  tests/test_taskbuilder_*.py tests/test_real_taskbuilder_ingress_gate.py \
  docs/architecture.md docs/current-state.md \
  docs/superpowers/specs/2026-08-13-codebase-consolidation-design.md \
  docs/superpowers/plans/2026-08-13-code-and-test-consolidation.md
```

- [x] Compile and run the final focused set:

```bash
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m compileall -q src tests
PYTHONPATH=src /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider \
  tests/test_taskbuilder_extraction_protocol.py \
  tests/test_taskbuilder_authority.py \
  tests/test_taskbuilder_provided_mesh.py \
  tests/test_taskbuilder_public_geometry.py \
  tests/test_taskbuilder_questions.py \
  tests/test_taskbuilder_extraction.py \
  tests/test_task_draft.py tests/test_task_draft_validation.py \
  tests/test_task_compiler.py tests/test_taskbuilder_cli.py \
  tests/test_taskbuilder_semantics.py tests/test_asset_contracts.py \
  tests/test_poly_mesh_inspector.py tests/test_desktop_workspace.py \
  tests/test_repository_docs.py tests/test_import_boundary.py
```

- [x] Run the complete deterministic/Qt-offscreen suite. The new opt-in real-asset test may add one documented
  skip when its environment variable is absent:

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests
```

- [x] Inspect `dist/`, move only the existing explicit `foampilot-0.2.0*` artifacts to a `mktemp -d` backup,
  build an sdist, build a wheel from that sdist, and verify distribution contents:

```bash
/home/edwin/feal-venv-py312/bin/python -c \
  'from setuptools import build_meta; print(build_meta.build_sdist("dist"))'
/home/edwin/feal-venv-py312/bin/python -m pip wheel \
  dist/foampilot-0.2.0.tar.gz --no-deps --no-build-isolation \
  --wheel-dir /tmp/foampilot-consolidation-wheel
cp /tmp/foampilot-consolidation-wheel/foampilot-0.2.0-py3-none-any.whl dist/
FOAMPILOT_VERIFY_DISTRIBUTION=1 PYTHONPATH=src \
  /home/edwin/feal-venv-py312/bin/python -B -m pytest \
  -q -p no:cacheprovider tests/test_distribution_contents.py
```

- [x] Install the wheel into a fresh `mktemp -d` target and, from `/tmp`, import `foampilot`, public
  `extract_task_draft`, and all five new responsibility modules. Assert every module path
  resolves inside the clean target rather than the repository.

- [x] Record sdist/wheel SHA256 and all fresh gate results in `docs/current-state.md`; rerun `git diff --check`.

---

### Task 13: Final review and commit on `main`

**Files:**

- Commit: only the scoped refactor, tests and current documentation
- Exclude: `dist/`, `.foampilot/`, `/tmp`, caches and unrelated files

**Interfaces:**

- Consumes: all passing gates and final responsibility audit
- Produces: one scoped local commit on `main` and a clean worktree

- [x] Reread every changed production file and the final test split. Re-run the normalized 45-scenario diff,
  real-asset extractor gate, `git diff --check` and `git status --short`.

- [x] Stage only the reviewed paths and inspect `git diff --cached --stat` plus `git diff --cached` before
  committing.

- [x] Commit with:

```bash
git commit -m "refactor: consolidate taskbuilder extraction"
```

- [x] Verify `git status --short` is empty. Report commit hash, production/test line-count changes, retained 45
  scenarios, focused/full/distribution/clean-install results, real-asset extractor result, artifact hashes and any
  discovered issue deliberately deferred because fixing it would change behavior.

## Completion Definition

The work is complete only when all conditions hold simultaneously:

1. `extract_task_draft()` remains the only model-backed TaskBuilder entrypoint and makes one serial logical call;
2. every production file matches the fixed responsibility map and forbidden dependency audit;
3. each moved implementation, policy constant and authority projection has one owner;
4. public imports, schemas, prompt meaning, status/error behavior and authority boundaries are unchanged;
5. all 45 original extractor scenario names and parameter IDs remain present and passing;
6. focused, full, real-asset extractor, distribution and clean-wheel import gates pass with fresh evidence;
7. the real provided polyMesh is reprocessed through the refactored extractor and stops only for unknown length
   unit;
8. architecture/current-state documentation matches the final tree and makes no new CFD/qualification claim;
9. the local `main` commit contains no new product capability and the worktree is clean.
