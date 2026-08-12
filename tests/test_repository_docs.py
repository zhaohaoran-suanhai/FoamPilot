from pathlib import Path


def test_root_documents_define_the_foampilot_entrypoint() -> None:
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "# FoamPilot" in readme
    assert "foampilot solve" in readme
    assert "Foundation OpenFOAM v10" in readme
    assert "foampilot solve" in agents
    assert "禁止读取或复制当前目标 tutorial" in agents
    assert "未经用户明确要求，不要 commit、push" in agents


def test_repository_ignores_runtime_artifacts() -> None:
    root = Path(__file__).resolve().parents[1]
    ignore = (root / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".foampilot/", ".pytest_cache/", "dist/", "build/"):
        assert pattern in ignore


def test_repository_documents_the_offline_improvement_boundary() -> None:
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    integration = (root / "docs/agent-integration.md").read_text(
        encoding="utf-8"
    )
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    combined = "\n".join((readme, integration, agents))

    assert "foampilot improve analyze" in combined
    assert "foampilot improve compare" in combined
    assert "完全离线" in combined
    assert "不会自动 promotion" in combined
    assert (
        "盲编写与 repair 期间无法访问官方 example"
        in combined
    )


def test_repository_documents_stage_one_coverage_and_skill_boundaries() -> None:
    root = Path(__file__).resolve().parents[1]
    governance = (root / "docs/knowledge-governance.md").read_text(
        encoding="utf-8"
    )
    overview = (root / "docs/system-overview.md").read_text(
        encoding="utf-8"
    )
    self_checks = (root / "docs/solver-family-self-checks.md").read_text(
        encoding="utf-8"
    )
    report = (
        root / "docs/reports/2026-08-04-stage-1-knowledge-skills.md"
    ).read_text(encoding="utf-8")
    combined = "\n".join((governance, overview, self_checks, report))

    assert "foampilot knowledge coverage" in combined
    assert "通用 Skill + 至多一个物理族 Skill" in combined
    assert "coverage 不等于求解能力已经通过验证" in combined
    assert "失败日志只进入 error-playbook 检索槽位" in combined
    assert "不可压缩真实 gate" in report
    assert "PUBLIC_VALIDATION_PASS" in report


def test_repository_documents_taskbuilder_without_a_second_solve_path() -> None:
    root = Path(__file__).resolve().parents[1]
    overview = (root / "docs/system-overview.md").read_text(encoding="utf-8")
    quickstart = (root / "docs/independent-agent-quickstart.md").read_text(
        encoding="utf-8"
    )
    integration = (root / "docs/agent-integration.md").read_text(
        encoding="utf-8"
    )
    combined = "\n".join((overview, quickstart, integration))

    assert "foampilot task draft" in combined
    assert "foampilot task validate-draft" in combined
    assert "foampilot task compile" in combined
    assert "不持有 Runner" in integration
    assert "同一个\n`NativeAgent.solve()`" in integration
    assert "不会被猜测" in overview


def test_repository_documents_portable_runtime_and_isolation() -> None:
    root = Path(__file__).resolve().parents[1]
    combined = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "README.md",
            "AGENTS.md",
            "docs/architecture.md",
            "docs/system-overview.md",
            "docs/independent-agent-quickstart.md",
            "docs/desktop-ide.md",
        )
    )
    for token in (
        "FOAMPILOT_OPENFOAM_ROOT",
        "sandbox_required",
        "sandbox_preferred",
        "trusted_host",
        "runtime-config.json",
        "execution-risk-report.json",
        "execution-policy.json",
    ):
        assert token in combined
    assert "audited host 与 bubblewrap 不具有相同安全性" in combined


def test_quickstart_documents_poly_mesh_as_one_directory_asset() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs/independent-agent-quickstart.md").read_text(
        encoding="utf-8"
    )

    assert "--asset-dir" in text
    assert "--asset-install-path" in text
    assert "asset-bundles.json" in text
    assert "input-mesh-facts.json" in text
    assert "pre-authoring-mesh-facts.json" in text
    assert "constant/polyMesh/points" not in text
