from __future__ import annotations

from pathlib import Path

from foampilot.environment import CommandFact, EnvironmentSnapshot
from foampilot.knowledge import (
    KnowledgeQuery,
    load_knowledge_corpus,
    select_knowledge,
    verify_knowledge_manifest,
)
from foampilot.agent.context import load_agent_context
from foampilot.routing import route_capability
from foampilot.tasks import load_task_spec


PROJECT = Path(__file__).parents[1]
CORPUS = PROJECT / "src/foampilot/knowledge/openfoam10"
MANIFEST = PROJECT / "src/foampilot/knowledge/knowledge-manifest.json"


def _context_for_task(task):
    corpus = load_knowledge_corpus(CORPUS)
    solvers = sorted(
        {
            solver
            for entry in corpus
            for solver in entry.solvers
        }
    )
    environment = EnvironmentSnapshot(
        schema_version=1,
        distribution="foundation",
        version="10",
        openfoam_root=Path("/opt/openfoam10"),
        tutorial_root=Path("/private/tutorials"),
        workspace_root=Path("/runs"),
        workspace_writable=True,
        commands=[
            CommandFact(
                name=solver,
                path=Path("/opt/openfoam10/bin") / solver,
            )
            for solver in solvers
        ],
        mpi_launcher=Path("/usr/bin/mpirun"),
        gmsh=None,
        max_mpi_ranks=16,
    )
    capability = route_capability(task, environment, corpus)
    return load_agent_context(
        task,
        capability,
        package_root=PROJECT / "src/foampilot",
    )


def test_reviewed_corpus_is_complete_frozen_and_has_no_target_solution() -> None:
    entries = load_knowledge_corpus(CORPUS)
    assert len(entries) == 36
    assert verify_knowledge_manifest(CORPUS, MANIFEST) == []
    boundedness = next(
        entry
        for entry in entries
        if entry.id == "of10.numerics.interfoam-alpha-boundedness"
    )
    assert boundedness.leakage.visibility == "public"
    assert boundedness.leakage.contains_target_case_solution is False
    assert {entry.knowledge_type for entry in entries} == {
        "solver_guide",
        "mesh_pattern",
        "boundary_condition",
        "physics_model",
        "numerics",
        "error_playbook",
        "parallel_execution",
        "validation_pattern",
    }
    serialized = "\n".join(entry.model_dump_json() for entry in entries)
    assert "/tutorials/" not in serialized
    assert "contains_target_case_solution\":true" not in serialized


def test_interfoam_knowledge_covers_v10_scheme_and_stability_margin() -> None:
    entries = load_knowledge_corpus(CORPUS)
    solver = next(
        entry
        for entry in entries
        if entry.id == "of10.solver.interfoam-vof-contract"
    )
    boundedness = next(
        entry
        for entry in entries
        if entry.id == "of10.numerics.interfoam-alpha-boundedness"
    )

    assert (
        "div(((rho*nuEff)*dev2(T(grad(U)))))"
        in solver.model_dump_json()
    )
    assert "严格低于公开上限" in boundedness.model_dump_json()


def test_solver_contracts_cover_failures_observed_in_native_baseline() -> None:
    entries = load_knowledge_corpus(CORPUS)
    by_id = {entry.id: entry.model_dump_json() for entry in entries}

    solid = by_id["of10.solver.solidequilibriumdisplacementfoam-contract"]
    assert "top-level Cp dictionary" in solid
    assert "mixture/thermodynamics/Cp" in solid

    rho_central = by_id["of10.solver.rhocentralfoam-contract"]
    assert "div(tauMC)" in rho_central
    assert "fieldInf must be a scalar literal" in rho_central
    assert "gamma scalar" in rho_central
    assert "(U|e)" in rho_central

    buoyant = by_id["of10.solver.buoyantfoam-contract"]
    assert "rhoFinal" in buoyant

    cht = by_id["of10.solver.chtmultiregionfoam-contract"]
    assert "maxDi <= 1" in cht


def test_retrieval_prefers_exact_solver_and_topic_deterministically() -> None:
    entries = load_knowledge_corpus(CORPUS)
    query = KnowledgeQuery(
        text="icoFoam PISO closed pressure reference",
        solver="icoFoam",
        knowledge_types=("numerics", "error_playbook"),
        limit=3,
    )
    first = select_knowledge(entries, query)
    second = select_knowledge(entries, query)
    assert first == second
    assert first
    assert first[0].entry_id == "of10.numerics.piso-closed-pressure-reference"
    assert len(first) <= 3


def test_detailed_rules_do_not_change_retrieval_relevance() -> None:
    entries = list(load_knowledge_corpus(CORPUS))
    query = KnowledgeQuery(
        text="incompressible transient icoFoam laminar sentinelword",
        limit=20,
    )
    baseline = {
        match.entry_id: match.score
        for match in select_knowledge(entries, query)
    }
    interfoam_index = next(
        index
        for index, entry in enumerate(entries)
        if entry.id == "of10.solver.interfoam-vof-contract"
    )
    entry = entries[interfoam_index]
    entries[interfoam_index] = entry.model_copy(
        update={
            "content": entry.content.model_copy(
                update={
                    "rules": [
                        *entry.content.rules,
                        "icoFoam laminar sentinelword",
                    ]
                }
            )
        }
    )

    modified = {
        match.entry_id: match.score
        for match in select_knowledge(entries, query)
    }
    assert modified[entry.id] == baseline[entry.id]


def test_shipped_corpus_is_public_and_formal_queries_fail_closed() -> None:
    entries = load_knowledge_corpus(CORPUS)
    formal = KnowledgeQuery(
        text="pilot physics golden validation gate",
        evaluation_family="new-holdout-family",
        formal=True,
    )
    assert all(
        match.visibility == "public"
        for match in select_knowledge(entries, formal)
    )

    unapproved_development = formal.model_copy(
        update={
            "formal": False,
        }
    )
    assert all(
        match.visibility == "public"
        for match in select_knowledge(entries, unapproved_development)
    )

    assert all(entry.leakage.visibility == "public" for entry in entries)


def test_type_and_solver_filters_are_applied_before_scoring() -> None:
    entries = load_knowledge_corpus(CORPUS)
    matches = select_knowledge(
        entries,
        KnowledgeQuery(
            text="pressure reference",
            solver="rhoCentralFoam",
            knowledge_types=("numerics",),
        ),
    )
    assert matches == ()


def test_parallel_knowledge_uses_method_specific_decomposition_coefficients() -> None:
    entries = load_knowledge_corpus(CORPUS)
    parallel = next(
        entry
        for entry in entries
        if entry.id == "of10.parallel.serial-first-explicit-mpi"
    )
    rules = "\n".join(parallel.content.rules)

    assert "hierarchicalCoeffs" in rules
    assert "simpleCoeffs" in rules
    assert "通用 coeffs dictionary" in rules


def test_maxwell_pimple_task_retrieves_its_solver_family_contract() -> None:
    task = load_task_spec(
        PROJECT
        / "src/foampilot/qualification/data/tasks"
        / "laminar-planar-poiseuille.yaml"
    )

    context = _context_for_task(task)

    assert (
        "of10.solver.pimplefoam-maxwell-contract"
        in context.selected_knowledge_ids
    )


def test_blocked_channel_retrieves_volume_fraction_source_contract() -> None:
    task = load_task_spec(
        PROJECT
        / "src/foampilot/qualification/data/tasks"
        / "compressible-blocked-channel.yaml"
    )

    context = _context_for_task(task)

    assert (
        "of10.physics.volume-fraction-source"
        in context.selected_knowledge_ids
    )
    assert (
        "of10.solver.rhopimplefoam-compressible-laminar-contract"
        in context.selected_knowledge_ids
    )


def test_srf_and_mhd_tasks_retrieve_exact_solver_contracts() -> None:
    for case_id, entry_id in (
        ("srf-rotor", "of10.solver.srfpimplefoam-contract"),
        ("mhd-hartmann", "of10.solver.mhdfoam-contract"),
    ):
        task = load_task_spec(
            PROJECT
            / "src/foampilot/qualification/data/tasks"
            / f"{case_id}.yaml"
        )

        context = _context_for_task(task)

        assert entry_id in context.selected_knowledge_ids


def test_solid_task_retrieves_foundation_v10_solver_contract() -> None:
    task = load_task_spec(
        PROJECT
        / "src/foampilot/qualification/data/tasks"
        / "solid-plate-hole.yaml"
    )

    context = _context_for_task(task)

    assert (
        "of10.solver.soliddisplacementfoam-contract"
        in context.selected_knowledge_ids
    )


def test_porous_task_retrieves_foundation_v10_solver_contract() -> None:
    task = load_task_spec(
        PROJECT
        / "src/foampilot/qualification/data/tasks"
        / "porous-angled-duct.yaml"
    )

    context = _context_for_task(task)

    assert (
        "of10.solver.poroussimplefoam-contract"
        in context.selected_knowledge_ids
    )


def test_cht_task_retrieves_foundation_v10_multiregion_contract() -> None:
    task = load_task_spec(
        PROJECT
        / "src/foampilot/qualification/data/tasks"
        / "cht-cooling-cylinder.yaml"
    )

    context = _context_for_task(task)

    assert (
        "of10.solver.chtmultiregionfoam-contract"
        in context.selected_knowledge_ids
    )


def test_shallow_water_contract_distinguishes_static_bed_from_time_outputs() -> None:
    entries = load_knowledge_corpus(CORPUS)
    solver = next(
        entry
        for entry in entries
        if entry.id == "of10.solver.shallowwaterfoam-contract"
    )
    rules = "\n".join(solver.content.rules)

    assert "静态输入字段" in rules
    assert "不会自动写入" in rules
    assert "h、hU" in rules
    assert "hTotal" in rules
    assert "g g [0 1 -2 0 0 0 0]" in rules
    assert "Omega Omega [0 0 -1 0 0 0 0]" in rules


def test_solver_contracts_capture_complete_observed_v10_dictionary_sets() -> None:
    entries = {
        entry.id: entry
        for entry in load_knowledge_corpus(CORPUS)
    }
    expected_fragments = {
        "of10.solver.mhdfoam-contract": (
            "pFinal",
            "pBFinal",
            "div(phiB,U)",
            "div(phi,B)",
            "div(phiB,((2*DBU)*B))",
        ),
        "of10.solver.pimplefoam-maxwell-contract": (
            "momentumPredictor off",
            "nOuterCorrectors",
            "vanAlbada",
            "(U|sigma)Final",
        ),
        "of10.solver.srfpimplefoam-contract": (
            "value uniform (0 0 0)",
            "Urel.*",
            "k.*",
            "epsilon.*",
        ),
        "of10.solver.interfoam-vof-contract": (
            "constantAlphaContactAngle",
            "inletOutlet",
            "液体 reservoir",
            "momentumPredictor no",
            "nCorrectors 3",
            "fixedValue p_rgh",
            "Gauss interfaceCompression vanLeer 1",
            "nAlphaCorr 1",
            "nAlphaSubCycles 2",
        ),
        "of10.solver.simplefoam-rans-contract": (
            "consistent yes",
            "limitedLinear 1",
            "0.9",
        ),
        "of10.solver.poroussimplefoam-contract": (
            "constant/porosityProperties",
            "No porosity models present",
            "完整局部截面",
            "共享入口界面",
            "公共平面",
            "turbulentBL",
            "turbulentIntensityKineticEnergyInlet",
            "turbulentMixingLengthDissipationRateInlet",
            "slip wall 仍使用 turbulence wall function",
            "internal k 与 epsilon 初始化为严格正值",
            "nUCorrectors 2",
            "不要启用 consistent SIMPLE",
            "inletOutlet",
            "0.7",
            "0.9",
        ),
        "of10.solver.chtmultiregionfoam-contract": (
            "0/<fluid>/nut",
            "0/<fluid>/alphat",
            "turbulence model",
            "nMoles 1",
        ),
        "of10.solver.shallowwaterfoam-contract": (
            "constant/gravitationalProperties",
            "rotating true",
            "Omega",
            "h0",
            "hTotal",
            "div(phiv,hU)",
            "Gauss LUST un",
            "nOuterCorrectors",
        ),
    }

    for entry_id, fragments in expected_fragments.items():
        serialized = entries[entry_id].model_dump_json()
        for fragment in fragments:
            assert fragment in serialized


def test_buoyant_pressure_contract_covers_operating_pressure_gauge_start() -> None:
    entries = {
        entry.id: entry
        for entry in load_knowledge_corpus(CORPUS)
    }
    serialized = entries[
        "of10.boundary.buoyant-pressure-semantics"
    ].model_dump_json()

    assert "均匀零缩减压力" in serialized
    assert "不要在 p 和 p_rgh 中重复写入工作压力" in serialized
    assert "pRefValue 0" in serialized
    assert "constant/pRef" in serialized


def test_extended_solver_contracts_cover_observed_startup_failures() -> None:
    entries = {
        entry.id: entry
        for entry in load_knowledge_corpus(CORPUS)
    }
    expected_fragments = {
        "of10.solver.incompressible-transient-contract": (
            "pisoFoam",
            "pimpleFoam",
            "div((nuEff*dev2(T(grad(U)))))",
            "diffusion none",
        ),
        "of10.function.scalartransport-contract": (
            "scalarTransport",
            "diffusion",
        ),
        "of10.mesh.two-dimensional-empty-extrusion": (
            "boundary face",
            "block cell face",
            "塌缩 hex",
        ),
        "of10.physics.volume-fraction-source": (
            "setFields",
            "不带 -time",
        ),
        "of10.boundary.rotating-swirl-inlet-contract": (
            "origin",
            "axis",
            "value",
        ),
        "of10.solver.rhosimplefoam-contract": (
            "rhoSimpleFoam",
            "alphat",
            "[1 -1 -1 0 0 0 0]",
            "minFactor",
            "maxFactor",
            "rhoInlet",
            "profile turbulentBL",
            "transonic yes",
            "consistent yes",
            "bounded Gauss upwind",
            "0.9",
        ),
        "of10.solver.buoyantfoam-contract": (
            "div(((rho*nuEff)*dev2(T(grad(U)))))",
            "thermophysicalTransport",
            "laminar",
            "Fourier",
        ),
        "of10.solver.solidequilibriumdisplacementfoam-contract": (
            "solidEquilibriumDisplacementFoam",
            "div((sigmaExp+sigmaD))",
            "laplacian(DD,Dcorr)",
        ),
        "of10.solver.srfsimplefoam-contract": (
            "SRFSimpleFoam",
            "SRFVelocity",
            "inletValue",
        ),
        "of10.solver.twoliquidmixingfoam-contract": (
            "twoLiquidMixingFoam",
            "maxAlphaCo",
            "必须成对包含 maxCo 与 maxAlphaCo",
            "nAlphaSubCycles",
            "div(phi,alpha)",
        ),
        "of10.solver.electrostaticfoam-contract": (
            "electrostaticFoam",
            "epsilon0 epsilon0",
            "k k",
        ),
    }

    for entry_id, fragments in expected_fragments.items():
        serialized = entries[entry_id].model_dump_json()
        for fragment in fragments:
            assert fragment in serialized


def test_extended_tasks_retrieve_exact_public_contracts() -> None:
    entries = load_knowledge_corpus(CORPUS)
    queries = (
        (
            "pimpleFoam transient laminar blocked channel tracer",
            "of10.solver.incompressible-transient-contract",
        ),
        (
            "scalarTransport function object tracer diffusion",
            "of10.function.scalartransport-contract",
        ),
        (
            "simpleFoam cyclic pipe rigid body swirl inlet origin axis",
            "of10.boundary.rotating-swirl-inlet-contract",
        ),
        (
            "rhoSimpleFoam compressible RAS square bend alphat",
            "of10.solver.rhosimplefoam-contract",
        ),
        (
            "solidEquilibriumDisplacementFoam plane stress traction beam",
            "of10.solver.solidequilibriumdisplacementfoam-contract",
        ),
        (
            "SRFSimpleFoam steady annular rotating reference frame Urel",
            "of10.solver.srfsimplefoam-contract",
        ),
        (
            "twoLiquidMixingFoam miscible phase fraction maxAlphaCo",
            "of10.solver.twoliquidmixingfoam-contract",
        ),
        (
            "electrostaticFoam charged wire dielectric epsilon0",
            "of10.solver.electrostaticfoam-contract",
        ),
    )

    for text, expected_entry_id in queries:
        selected = select_knowledge(
            entries,
            KnowledgeQuery(text=text, limit=5),
        )
        assert expected_entry_id in {
            match.entry_id
            for match in selected
        }


def test_explicit_solver_name_outranks_incidental_topic_overlap() -> None:
    entries = load_knowledge_corpus(CORPUS)
    query = KnowledgeQuery(
        text=(
            "Foundation OpenFOAM v10 pisoFoam laminar wake behind a Darcy "
            "porous square blockage. Use topoSet, a cartesian coordinate "
            "system at the origin, an axis, a cell zone, symmetry planes, "
            "kinematic viscosity, pressure and velocity fields."
        ),
        limit=1,
    )

    selected = select_knowledge(entries, query)

    assert selected[0].entry_id == (
        "of10.solver.incompressible-transient-contract"
    )

    contract = next(
        entry
        for entry in entries
        if entry.id == "of10.solver.incompressible-transient-contract"
    )
    rules = "\n".join(contract.content.rules)
    assert "explicitPorositySourceCoeffs" in rules
    assert "constant/coordinateSystems" in rules
