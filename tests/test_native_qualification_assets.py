from __future__ import annotations

import json
from pathlib import Path

from foampilot.agent import load_agent_context
from foampilot.environment import CommandFact, EnvironmentSnapshot
from foampilot.knowledge import load_knowledge_corpus
from foampilot.qualification.runner import qualification_data_path
from foampilot.routing import route_capability
from foampilot.tasks import load_task_spec


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
TASK_ROOT = PACKAGE_ROOT / "examples" / "qualification"
EXPECTED_TASK_IDS = {
    "buoyant-cavity",
    "compressible-shock-tube",
    "laminar-cavity",
    "multiphase-dam-break",
    "potential-cylinder",
    "rans-pitzdaily",
}
EXPECTED_SOLVER_GUIDES = {
    "buoyant-cavity": "of10.solver.buoyantfoam-contract",
    "compressible-shock-tube": "of10.solver.rhocentralfoam-contract",
    "laminar-cavity": "of10.solver.icofoam-contract",
    "multiphase-dam-break": "of10.solver.interfoam-vof-contract",
    "potential-cylinder": "of10.solver.potentialfoam-contract",
    "rans-pitzdaily": "of10.solver.simplefoam-rans-contract",
}


def _context(task):
    corpus = load_knowledge_corpus(
        PACKAGE_ROOT / "src/foampilot/knowledge/openfoam10"
    )
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
    return load_agent_context(task, capability)


def test_native_qualification_has_exactly_six_safe_task_specs() -> None:
    paths = sorted(TASK_ROOT.glob("*.yaml"))
    tasks = [load_task_spec(path) for path in paths]

    assert {task.task_id for task in tasks} == EXPECTED_TASK_IDS
    assert len(tasks) == len(EXPECTED_TASK_IDS)
    for task in tasks:
        assert task.openfoam_target.distribution == "foundation"
        assert task.openfoam_target.version == "10"
        assert task.public_checks
        assert any(
            path == Path("/home/edwin/workplace/OpenFOAM-10/tutorials")
            for path in map(Path, task.protected_paths)
        )
        visible = json.dumps(
            task.agent_payload(),
            ensure_ascii=False,
        ).casefold()
        assert "golden" not in visible
        assert "private validator" not in visible
        assert "/home/edwin/workplace/openfoam-10/tutorials" not in visible


def test_each_qualification_task_retrieves_its_public_solver_contract() -> None:
    for path in sorted(TASK_ROOT.glob("*.yaml")):
        task = load_task_spec(path)
        context = _context(task)

        assert EXPECTED_SOLVER_GUIDES[task.task_id] in (
            context.selected_knowledge_ids
        )
        assert context.selected_knowledge_ids


def test_scalar_transport_task_retrieves_foundation_v10_solver_contract() -> None:
    task = load_task_spec(
        qualification_data_path("tasks", "scalar-transport-pitzdaily")
    )
    context = _context(task)

    assert "of10.solver.scalartransportfoam-contract" in (
        context.selected_knowledge_ids
    )


def test_native_authoring_skill_uses_only_execution_plan_v3_fields() -> None:
    skill = (
        PACKAGE_ROOT
        / "src"
        / "foampilot"
        / "skills"
        / "openfoam-author-native-case"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "unresolved_inputs" not in skill


def test_native_authoring_skill_keeps_optional_diagnostics_external() -> None:
    skill = (
        PACKAGE_ROOT
        / "src"
        / "foampilot"
        / "skills"
        / "openfoam-author-native-case"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Runner 负责 MPI launcher" in skill
    assert "将可选诊断排除在必需求解计划之外" in skill
    assert (
        "configure native volume-field-value function objects" not in skill
    )


def test_native_authoring_skill_separates_all_time_log_evidence() -> None:
    skill = (
        PACKAGE_ROOT
        / "src"
        / "foampilot"
        / "skills"
        / "openfoam-author-native-case"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert (
        "公开任务明确要求全时段日志证据时"
        in skill
    )
    assert (
        "普通输出时刻的测量应由 evaluator 检查写出字段"
        in skill
    )


def test_interfoam_boundedness_knowledge_uses_foundation_v10_statistics() -> None:
    entries = {
        entry.id: entry
        for entry in load_knowledge_corpus(
            PACKAGE_ROOT
            / "src"
            / "foampilot"
            / "knowledge"
            / "openfoam10"
        )
    }
    rules = "\n".join(
        entries["of10.numerics.interfoam-alpha-boundedness"].content.rules
    )

    for fragment in (
        "type volFieldValue",
        "operation min",
        "operation max",
        "operation volIntegrate",
        "writeFields false",
        "fieldMinMax",
    ):
        assert fragment in rules


def test_volume_fraction_initializer_uses_control_dict_function_object() -> None:
    entries = {
        entry.id: entry
        for entry in load_knowledge_corpus(
            PACKAGE_ROOT
            / "src"
            / "foampilot"
            / "knowledge"
            / "openfoam10"
        )
    }
    rules = "\n".join(
        entries["of10.physics.volume-fraction-source"].content.rules
    )

    for fragment in (
        "system/controlDict",
        "不带 -func",
        "-time constant",
    ):
        assert fragment in rules


def test_solver_contracts_cover_observed_foundation_v10_failure_shields() -> None:
    entries = {
        entry.id: entry
        for entry in load_knowledge_corpus(
            PACKAGE_ROOT
            / "src"
            / "foampilot"
            / "knowledge"
            / "openfoam10"
        )
    }

    expected_rules = {
        "of10.solver.potentialfoam-contract": (
            "div(div(phi,U))",
            "-writep",
            "为 p 配置 linear solver",
        ),
        "of10.solver.simplefoam-rans-contract": (
            "executable simpleFoam",
            "mpi_ranks",
            "div((nuEff*dev2(T(grad(U)))))",
        ),
        "of10.solver.interfoam-vof-contract": (
            "[1 -1 -2 0 0 0 0]",
            "prghTotalPressure",
        ),
        "of10.solver.rhocentralfoam-contract": (
            "timeFormat general",
            "名为 0 的初始目录",
            "(rho|rhoU|rhoE)",
            "solver diagonal",
        ),
        "of10.solver.buoyantfoam-contract": (
            "div(phi,K)",
            "单个 scalar value",
            "momentumPredictor no",
            "bounded Gauss limitedLinear 0.2",
            "rho 1.0",
        ),
        "of10.solver.scalartransportfoam-contract": (
            "constant/physicalProperties",
            "DT DT [0 2 -1 0 0 0 0]",
            "SIMPLE",
            "laplacian(DT,T)",
            "非对称矩阵",
            "PBiCGStab",
            "DILU",
        ),
        "of10.solver.pimplefoam-maxwell-contract": (
            "selectionMode all",
            "U source 子字典",
            "显式 vector",
            "隐式 scalar",
        ),
        "of10.solver.rhopimplefoam-compressible-laminar-contract": (
            "model Stokes",
            "Newtonian",
            "div(((rho*nuEff)*dev2(T(grad(U)))))",
            "div(phi,(p|rho))",
            "rhoFinal",
        ),
        "of10.physics.volume-fraction-source": (
            "transport constIsoSolid",
            "-dict system/",
            "const fvMesh& m = mesh()",
            "m.time().constant()",
        ),
        "of10.solver.srfpimplefoam-contract": (
            "freestreamValue",
            "div((nuEff*dev2(T(grad(Urel)))))",
            "p zeroGradient",
            "inletOutlet",
            "Gauss limitedLinearV 1",
            "adjustTimeStep no",
            "UInf (1 0 0)",
            "而不是 UInf uniform",
        ),
        "of10.solver.mhdfoam-contract": (
            "BPISO",
            "mu mu [1 1 -2 0 0 -2 0]",
            "sigma sigma [-1 -3 3 0 0 2 0]",
            "div(phiB,((2*DBU)*B))",
            "BFinal",
        ),
        "of10.solver.soliddisplacementfoam-contract": (
            "type uniform",
            "d2dt2(rho,D)",
            "d2dt2Schemes",
            "laplacian(DD,D)",
            "stressAnalysis",
            "compactNormalStress",
        ),
        "of10.solver.chtmultiregionfoam-contract": (
            "-defaultRegionName",
            "system/fvSolution",
            "system/<region>/fvSolution",
            "PIMPLE",
            "constIsoSolid",
            "constant/<fluid>/g",
            "thermo eConst",
            "sensibleInternalEnergy",
            "selectionMode all",
            "compressible::turbulentTemperatureCoupledBaffleMixed",
            "checkMesh -region <name>",
            "没有 -allRegions",
            "rhoFinal",
            "div(phi,(p|rho))",
            "radiationModel none",
            "radiation off",
        ),
    }
    for entry_id, fragments in expected_rules.items():
        rules = "\n".join(entries[entry_id].content.rules)
        for fragment in fragments:
            assert fragment in rules


def test_native_authoring_skill_pairs_constraint_patch_types() -> None:
    skill = (
        PACKAGE_ROOT
        / "src"
        / "foampilot"
        / "skills"
        / "openfoam-author-native-case"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert (
        "mesh type 为 `symmetryPlane` 时，field type 也必须是"
        in skill
    )
    assert '#includeEtc "caseDicts/setConstraintTypes"' in skill


def test_native_authoring_skill_requires_multiblock_face_conformity() -> None:
    skill = (
        PACKAGE_ROOT
        / "src"
        / "foampilot"
        / "skills"
        / "openfoam-author-native-case"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "每个共享面在两个切向方向上的划分数相同" in normalized
    assert "检查完整邻接图" in normalized
    assert "定义具名变量" in normalized
    assert "点位与 grading 兼容" in normalized
    assert "在 x-y 平面按逆时针排列" in normalized
    assert "负坐标绝不能写成 `-$name`" in normalized
    assert "流固界面" in normalized
    assert "切向划分" in normalized


def test_native_authoring_skill_covers_topology_geometry_and_budget_shields() -> None:
    skill = (
        PACKAGE_ROOT
        / "src"
        / "foampilot"
        / "skills"
        / "openfoam-author-native-case"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "复用完全相同的 vertex label" in normalized
    assert "-merge-points" in normalized
    assert "defaultFaces" in normalized
    assert "命令 timeout 总和" in normalized
    assert "完整局部坐标系与截面" in normalized
