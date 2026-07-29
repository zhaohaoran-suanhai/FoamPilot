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

## Official-six qualification

FoamPilot ships six public TaskSpecs, evaluator rules, and compact derived
numeric references. It does not ship the official tutorial cases or their
large solver results.

```bash
foampilot qualify official-six \
  --run-root /tmp/foampilot-official-six \
  --workers 2 \
  --model-name gpt-5.6-sol \
  --json
```

The latest preserved pre-extraction run showed that all six tasks eventually
entered and completed their OpenFOAM solver after the bounded repair policy.
The stricter physics qualification still contained failures. Solver
completion and physics qualification are therefore reported separately.
A fresh post-extraction six-case run is not yet claimed.

See [qualification methodology](docs/qualification.md).

The fresh standalone non-tutorial gate is 2/2
`PUBLIC_VALIDATION_PASS`: the laminar enclosure passed on its first attempt,
and the two-phase column collapse passed after one evidence-scoped
time-step-cap repair. This verifies the installed-wheel solve path, not the
full official-six physics qualification. See the
[standalone real-case gate report](docs/reports/2026-07-29-standalone-real-gate.md).

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
- [Standalone real-case gate](docs/reports/2026-07-29-standalone-real-gate.md)
- [License](LICENSE)
- [Provenance and notices](NOTICE.md)

FoamPilot is not affiliated with or endorsed by the OpenFOAM Foundation.
