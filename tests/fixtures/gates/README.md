# Real continuation gate fixtures

`non-tutorial-side-driven-plan.json` is a frozen historical replay fixture:
it was model-authored as ExecutionPlan v3 and upgraded with a reviewed CaseManifest and command stages
from a previously manifest-verified run of
`examples/tasks/non-tutorial-side-driven-box.yaml`.

- Source run native status: `PUBLIC_VALIDATION_PASS`
- Plan SHA256:
  `30e84b2e6fa9e50735b83968961fef1d13cd2cae39a505256875e3dc25295dc8`
- No OpenFOAM tutorial, golden field, private evaluator output, credential, or
  source-machine path is included.

This fixture is accepted only through the read-only legacy replay boundary; it
is not a current authoring, resume, or execution fallback. The opt-in gate injects one public dictionary defect, verifies that the target
solver starts and fails, defers on a fake backend overload, then resumes from
the frozen failure evidence with a minimal repair.
