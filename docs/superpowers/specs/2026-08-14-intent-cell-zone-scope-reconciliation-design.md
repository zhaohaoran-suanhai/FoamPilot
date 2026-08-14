# Intent Cell-Zone Scope Reconciliation Design

## Problem

The real provided-polyMesh porous case contains one authoritative cell zone named
`porousBlockage` and no named OpenFOAM mesh region. The model emitted region-average
observations with `scope.kind = region`, `scope.names = [porousBlockage]`, and
`scope.region = porousBlockage`. `ObservationPlanner` correctly rejected that scope
as `MESH_REGION_UNKNOWN` because cell zones and OpenFOAM mesh regions are different
objects.

## Responsibility Boundary

`ObservationPlanner` remains a strict validator and evidence-strategy selector. It
must not reinterpret an invalid scope. `SimulationIntent` reconciliation already
combines model interpretation with first-party task, asset, and executed-mesh
authority, so it owns the deterministic correction of an unambiguous model scope
classification.

## Reconciliation Rule

For every intent observation request and acceptance request observation:

1. Leave every non-`region` scope unchanged.
2. Leave a `region` scope unchanged when its name matches an authoritative mesh
   region.
3. Otherwise find authoritative cell-zone occurrences with the same name.
4. Convert the scope to `cell_zone` only when exactly one mesh contains that zone.
   Preserve the one-item `names` tuple and bind `region` to that mesh's region name,
   or to `null` for a single-region mesh.
5. Leave zero matches and multiple matches unchanged so the existing planner rejects
   the invalid or ambiguous scope.
6. Add a stable audit warning identifying the reconciled observation or acceptance
   condition.

The model system prompt must also state that `region` means a named OpenFOAM mesh
region and that a cellZone must use `cell_zone`. Prompt guidance reduces errors; the
deterministic rule is the authority-bearing safeguard.

## Verification

Tests must first reproduce the real failure shape. They then prove that a unique
cellZone is reconciled, a valid mesh region remains a region, and duplicate cell-zone
names across mesh regions remain unresolved. Existing ObservationPlanner tests must
continue to prove strict rejection of unknown regions. After the focused tests pass,
run the complete deterministic suite before resuming the real plan-only case.

