# Task Ingress Reconciliation Design

## 1. Goal

Make the canonical natural-language entry path accept an immutable OpenFOAM
`polyMesh` directory plus a physics intent, determine all mesh facts that do not
depend on an unknown length unit before the first model request, and defer
engineering design choices to the existing `IntentInterpreter -> CaseDesigner ->
RiskGate` chain.

This is a correction to the approved contract-first architecture. It does not
weaken the no-generalized-override rule and it does not add a porous-blockage
special case.

## 2. Confirmed defect

The current implementation has both the intended components and the wrong
entry ordering:

```text
request + asset metadata
-> legacy TaskDraft completeness gate
-> TaskSpec
-> polyMesh inspection
-> Intent / Design / Risk
```

Consequently, the legacy gate asks the user for patch names, zone names,
solver, material values, time controls, output paths, and resource budgets
before the authoritative mesh inspector or the engineering designer can act.
It also treats text wrapped in Chinese quotation marks as unverifiable even
when the inner quotation is present verbatim in the request.

## 3. Corrected ordering

```text
request + declared assets
-> immutable asset manifest validation
-> unit-independent polyMesh topology inspection
-> TaskDraft extraction and deterministic authority reconciliation
-> TaskSpec request envelope
-> unit-aware InputMeshFacts and controlled checkMesh
-> IntentInterpreter
-> CaseDesigner
-> RiskGate
-> CaseAuthor
```

The TaskBuilder decides whether the input envelope can enter simulation design.
It does not decide whether the final case design can enter authoring.

## 4. New unit-independent mesh contract

Add `PolyMeshTopologyFacts`, produced by the existing first-party parser. It
contains only facts that are invariant under the user's length-unit choice:

- bundle manifest identity;
- point, face, internal-face and cell counts;
- raw coordinate bounds, explicitly labelled as unscaled coordinates;
- patch name, OpenFOAM patch type and face count;
- cell/face/point zone name and element count;
- empty-patch dimensionality observations;
- static topology observations;
- `raw_content_included=false`.

It must not contain `bounding_box_m` or claim a length unit. The existing
`InputMeshFacts` remains the unit-aware solve contract. `inspect_poly_mesh()`
must reuse the unit-independent parse and only add declared-unit scaling.

This split prevents the pre-draft inspector from silently treating OpenFOAM
coordinates as metres.

## 5. Authority reconciliation

The extraction model receives the public request, asset manifests and compact
`PolyMeshTopologyFacts`. It never receives raw `points`, `faces`, `owner`, or
`neighbour` content.

After extraction, deterministic reconciliation applies these rules:

1. A surrounding pair of ASCII or Chinese quotation marks is presentation,
   not evidence. Strip only balanced outer quotation marks and whitespace
   before verifying that evidence occurs verbatim in the request.
2. A declared atomic polyMesh asset deterministically selects
   `geometry.mode=openfoam_mesh`, `mesh.strategy=provided`, and its exact asset
   path/install path. Stale `.msh` wording cannot override the actual asset
   type.
3. Empty-patch observations may deterministically establish `two_d`; otherwise
   dimensionality remains unresolved unless the user states it.
4. Patch/zone names and counts come only from topology facts. Semantic roles
   still come from user intent or a later design candidate.
5. The product target is Foundation OpenFOAM 10. TaskDraft does not ask the
   user to repeat the target already fixed by the product contract.
6. Explicit user statements remain authoritative `user_text` facts after
   evidence verification.
7. Unconfirmed model interpretations are retained for audit but are not
   compiled as authoritative TaskSpec facts.

## 6. Gate ownership

TaskBuilder blocks only input-authority gaps that later engineering design
cannot safely invent. For a provided polyMesh this includes:

- missing or invalid atomic bundle members;
- unknown mesh length unit;
- unresolved dimensionality when neither mesh topology nor user text supplies
  it;
- contradictory authoritative input facts;
- undeclared or unsafe assets.

TaskBuilder must not block solely because the user omitted:

- solver selection;
- material values for which the request explicitly permits an engineering
  choice;
- inlet magnitude, porous coefficients, timestep, end time or write interval;
- output paths, acceptance tolerances or resource budgets.

Those values are proposed by `CaseDesigner`. The existing `RiskGate` then
classifies them:

- a concrete medium/high-impact candidate becomes
  `CONFIRMATION_REQUIRED`;
- a fact with no defensible candidate becomes `INFORMATION_REQUIRED`;
- a capability conflict becomes `CAPABILITY_UNAVAILABLE`;
- only authoritative and complete designs become `READY_TO_AUTHOR`.

There remains no accept-all, continue-anyway, or model-confidence override.

## 7. TaskSpec compilation

For provided polyMesh, the compiler deterministically emits:

- `geometry.input.mode=openfoam_mesh`;
- the declared unit and topology-derived dimensionality;
- one `GeometryAssetRef(format=openfoam_mesh)` for each declared bundle;
- `mesh.intent.strategy=provided`;
- Foundation OpenFOAM 10 target;
- visible bounded resource defaults.

Only confirmed `user_text`, `user_confirmation`, `public_asset`, and valid
low-impact system-default facts are copied into `explicit_facts`.
Unconfirmed model inference remains in TaskDraft audit data and is interpreted
again in the bounded Intent stage with mesh facts available.

## 8. Error behavior

- Asset or topology failure terminates `task draft` with its stable asset or
  polyMesh error code before any model call.
- Missing mesh unit produces one concrete TaskDraft input question and no case
  generation.
- Model/backend interruption remains a task-extraction deferred result.
- Design uncertainty after TaskSpec compilation is represented only by the
  canonical run's `questions.json` and RiskDecision.

## 9. Generality constraints

- No checks for `porousBlockage`, `inlet`, `outlet`, or any case-specific name.
- Multiple regions and arbitrary safe patch/zone names remain data, not code.
- Surface, Gmsh and parametric routes retain their existing contracts; only
  the gate-ownership rule is shared.
- Runtime, execution security, observation, acceptance, repair and evidence
  contracts are unchanged.

## 10. Acceptance gates

1. Unit-independent inspection reports a real polyMesh's topology without
   asserting metres.
2. TaskDraft model input contains compact patch/zone facts and no raw mesh
   content.
3. Balanced Chinese quotation marks do not downgrade a true user quotation.
4. A provided polyMesh deterministically compiles to `openfoam_mesh/provided`
   after its unit is supplied.
5. Omitted solver/material/time/numerical choices do not block TaskSpec
   compilation.
6. Missing length unit still blocks with a specific question.
7. Existing TaskBuilder, intent, risk, asset-integrity, runtime-security and
   real provided-mesh gates remain green.
8. The original broad Chinese request and original polyMesh proceed beyond the
   legacy TaskBuilder gate; any later stop must be a truthful Intent/Design/Risk
   decision with persisted evidence.
