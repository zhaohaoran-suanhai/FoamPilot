# FoamPilot

FoamPilot turns a public CFD requirement into a native Foundation OpenFOAM
v10 case, executes it in a networkless sandbox, evaluates the written result,
and permits one evidence-scoped repair. It is an independently installable
Python package and CLI; it does not require Foam-Agent, LangGraph, FAISS, MCP,
or a pre-existing tutorial case.

FoamPilot is an Agent workflow around OpenFOAM, not a CFD solver. OpenFOAM
provides the mesh utilities and numerical solvers; FoamPilot authors,
orchestrates, checks, and records the case.

## Capability boundary

The verified runtime target is Foundation OpenFOAM v10. ESI OpenFOAM
distributions and other Foundation releases are not currently qualified.
Model-authored case generation is non-deterministic, so one successful run is
evidence for that run rather than a guarantee for every future prompt.

The canonical workflow is:

```text
public TaskSpec
-> dynamic public knowledge and Skills
-> one complete model-authored case bundle
-> typed command and resource checks
-> networkless native OpenFOAM execution
-> evaluator-owned checks
-> at most one evidence-scoped repair
-> immutable artifacts and SHA256 manifest
```

The Agent starts from an empty case directory. It may use public OpenFOAM
documentation and general knowledge, but it may not read the current target
tutorial, evaluator rules, or derived reference values.

## Requirements

- Python 3.12 or newer;
- Foundation OpenFOAM v10;
- bubblewrap (`bwrap`);
- NumPy, Pydantic, PyYAML, and PyVista;
- `requests` plus a supported local Codex OAuth credential for the default
  live model provider.

The current workstation profile expects:

```text
/home/edwin/workplace/OpenFOAM-10
/home/edwin/feal-venv-py312/bin/python
/usr/local/bin/bwrap
```

These paths are explicit runtime configuration, not a dependency on the
source repository from which FoamPilot was extracted.

## Install

```bash
python -m pip install -e ".[codex,test]"
foampilot preflight --json
```

`preflight` must be executed with permission to create the bubblewrap
namespace. A nested development sandbox may block that operation even when
the host is correctly configured.

## Solve a task

Validate a public TaskSpec:

```bash
foampilot validate examples/tasks/non-tutorial-side-driven-box.yaml --json
```

Run the complete Agent loop:

```bash
foampilot solve \
  examples/tasks/non-tutorial-side-driven-box.yaml \
  --run-root /tmp/foampilot-runs \
  --model-name gpt-5.6-sol \
  --json
```

Verify a frozen result:

```bash
foampilot report /tmp/foampilot-runs/RUN_DIR --json
```

The default authentication path is `~/.codex/auth.json`. A task may allow
serial or bounded MPI execution. The model declares `mpi_ranks`; the Runner,
not the model, owns the MPI launcher.

## Public knowledge and Skills

Knowledge and Skills are package data and remain available from an installed
wheel:

```bash
foampilot knowledge validate src/foampilot/knowledge/openfoam10 --json
foampilot knowledge search src/foampilot/knowledge/openfoam10 \
  "incompressible immiscible free surface" --formal --limit 8 --json

foampilot skill validate \
  src/foampilot/skills/openfoam-author-native-case --json
```

The package contains one general native authoring Skill plus benchmark,
buoyant-flow, and `rhoCentralFoam` solver-family Skills. These are public
guidance, not deterministic case templates.

## Controlled qualification

FoamPilot ships a 15-case Foundation OpenFOAM v10 suite spanning regression,
development, and holdout roles. Each case has a public TaskSpec plus
evaluator-only rules and compact derived numeric references. The repository
does not ship official tutorial directories or their large solver results.

```bash
foampilot qualify suite \
  --suite-file \
    src/foampilot/qualification/data/suites/controlled-learning-15-v1.yaml \
  --run-root /tmp/foampilot-controlled-learning-15 \
  --workers 2 \
  --model-name gpt-5.6-sol \
  --json
```

The smaller `foampilot qualify official-six` command remains available as a
six-case regression wrapper.

On 2026-07-30, the frozen 15-case baseline reached 11/15 strict qualification
passes. All 15 cases entered their requested solver and 14 reached public
validation; one CHT case failed during its solver run. Four evidence-scoped
failures were then corrected and passed targeted reruns, but these separate
runs are not presented as a fresh 15/15 stochastic suite result. See the
[qualification methodology](docs/qualification.md) and
[controlled-learning report](docs/reports/2026-07-30-controlled-learning-15.md).

The standalone non-tutorial gate is 2/2
`PUBLIC_VALIDATION_PASS`: the laminar enclosure passed on its first attempt,
and the two-phase column collapse passed after one evidence-scoped
time-step-cap repair. This verifies the installed-wheel solve path, not the
15-case physics qualification. See the
[standalone real-case gate report](docs/reports/2026-07-29-standalone-real-gate.md).

## Offline controlled improvement

FoamPilot can turn a frozen failed run into a reviewable learning candidate
and compare qualification reports after a developer applies one small change:

```text
frozen solve/qualification
-> foampilot improve analyze
-> developer applies one candidate change
-> rerun qualification
-> foampilot improve compare
-> explicit promotion decision
```

For example:

```bash
foampilot improve analyze RUN_DIR \
  --qualification-report BASELINE.json \
  --candidate-id of10-solver-family-rule \
  --lesson "General solver-family lesson" \
  --target knowledge \
  --development-case SOURCE_CASE \
  --output IMPROVEMENTS/candidate.yaml

foampilot improve compare BASELINE.json CURRENT.json \
  --candidate IMPROVEMENTS/candidate.yaml \
  --output IMPROVEMENTS/promotion.json \
  --json
```

This workflow is offline and has no automatic promotion. Candidate and
comparison files live beside run roots, never inside immutable runs or package
data. Official examples are unavailable during blind authoring and repair.
Only after artifact verification and frozen qualification may a developer use
one as a teacher reference; the candidate records its directory hash,
generalized principles, and leakage family instead of copying the case.

## Failure layers

FoamPilot reports:

- `BLOCKED_ENVIRONMENT`;
- `CASE_GENERATION_FAILED`;
- `PLAN_INVALID`;
- `STATIC_INSPECTION_FAILED`;
- `SOLVER_FAILED`;
- `PUBLIC_VALIDATION_FAILED`;
- `PUBLIC_VALIDATION_PASS`.

`PUBLIC_VALIDATION_PASS` covers the checks declared by the public task. A
separate qualification layer may still reject a completed solve against
physics metrics. Reports must preserve that distinction.

## Development verification

```bash
PYTHONPATH=src python -B -m pytest -q -p no:cacheprovider tests
python -m pip wheel . --no-deps --wheel-dir dist
```

Real OpenFOAM tests require the host runtime and are intentionally separate
from deterministic unit tests.

## Documentation

- [Architecture](docs/architecture.md)
- [Independent quickstart](docs/independent-agent-quickstart.md)
- [Agent integration](docs/agent-integration.md)
- [Knowledge governance](docs/knowledge-governance.md)
- [Qualification](docs/qualification.md)
- [Controlled-learning 15-case report](docs/reports/2026-07-30-controlled-learning-15.md)
- [Standalone real-case gate](docs/reports/2026-07-29-standalone-real-gate.md)
- [License](LICENSE)
- [Provenance and notices](NOTICE.md)

FoamPilot is not affiliated with or endorsed by the OpenFOAM Foundation.
