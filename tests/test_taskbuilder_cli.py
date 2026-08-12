from __future__ import annotations

import json
from pathlib import Path

import yaml

from foampilot.cli.main import build_parser, main
from foampilot.models import (
    BackendError,
    BackendFailureKind,
    GatewayRequestError,
)
from tests.test_task_draft_validation import _complete_draft, _without


def _write_draft(path: Path, draft) -> None:
    path.write_text(
        yaml.safe_dump(
            draft.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def test_task_cli_subcommands_parse_explicit_paths() -> None:
    parser = build_parser()
    draft = parser.parse_args(
        [
            "task",
            "draft",
            "--request-file",
            "/tmp/request.md",
            "--asset",
            "geometry/body.stl",
            "--asset-dir",
            "mesh/native",
            "--asset-install-path",
            "constant/polyMesh",
            "--output",
            "/tmp/draft.yaml",
        ]
    )
    validate = parser.parse_args(
        ["task", "validate-draft", "/tmp/draft.yaml", "--json"]
    )
    compile_args = parser.parse_args(
        [
            "task",
            "compile",
            "/tmp/draft.yaml",
            "--output",
            "/tmp/task.yaml",
            "--json",
        ]
    )

    assert draft.task_command == "draft"
    assert draft.asset == [Path("geometry/body.stl")]
    assert draft.asset_dir == [Path("mesh/native")]
    assert draft.asset_install_path == [Path("constant/polyMesh")]
    assert validate.task_command == "validate-draft"
    assert compile_args.task_command == "compile"


def test_task_draft_writes_model_output_without_calling_solver(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    request = tmp_path / "request.md"
    request.write_text("Solve a complete channel.", encoding="utf-8")
    output = tmp_path / "draft.yaml"
    expected = _complete_draft()
    calls = []

    monkeypatch.setattr(
        "foampilot.cli.main._native_gateway",
        lambda arguments, **kwargs: object(),
    )

    def fake_extract(request_text, assets, gateway, **kwargs):
        calls.append((request_text, assets, gateway, kwargs))
        return expected

    monkeypatch.setattr("foampilot.cli.main.extract_task_draft", fake_extract)

    assert main(
        [
            "task",
            "draft",
            "--request-file",
            str(request),
            "--output",
            str(output),
            "--json",
        ]
    ) == 0

    assert output.is_file()
    performance_path = output.with_suffix(
        output.suffix + ".performance.json"
    )
    assert performance_path.is_file()
    performance = json.loads(performance_path.read_text(encoding="utf-8"))
    assert performance["schema_version"] == 1
    assert performance["draft_id"] == expected.draft_id
    assert performance["total_seconds"] >= 0
    assert performance["logical_requests"] == 0
    assert performance["transport_attempts"] == 0
    assert calls and calls[0][0] == "Solve a complete channel."
    budget = calls[0][3]["budget"]
    assert budget.request_timeout_seconds == 180
    assert budget.max_transport_attempts == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["draft_status"] == "confirmed"


def test_task_draft_declares_poly_mesh_directory_as_one_asset(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    request = tmp_path / "request.md"
    request.write_text("Use the supplied native mesh.", encoding="utf-8")
    mesh = tmp_path / "mesh/native"
    mesh.mkdir(parents=True)
    for name, content in {
        "points": b"points\n",
        "faces": b"faces\n",
        "owner": b"owner\n",
        "neighbour": b"neighbour\n",
        "boundary": b"boundary\n",
        "cellZones": b"zones\n",
    }.items():
        (mesh / name).write_bytes(content)
    output = tmp_path / "draft.yaml"
    expected = _complete_draft()
    captured = []

    monkeypatch.setattr(
        "foampilot.cli.main._native_gateway",
        lambda arguments, **kwargs: object(),
    )

    def fake_extract(request_text, assets, gateway, **kwargs):
        del request_text, gateway, kwargs
        captured.extend(assets)
        return expected.model_copy(update={"assets": assets})

    monkeypatch.setattr("foampilot.cli.main.extract_task_draft", fake_extract)

    assert main(
        [
            "task",
            "draft",
            "--request-file",
            str(request),
            "--asset-dir",
            "mesh/native",
            "--asset-install-path",
            "constant/polyMesh",
            "--output",
            str(output),
            "--json",
        ]
    ) == 0

    assert len(captured) == 1
    asset = captured[0]
    assert asset.kind == "directory"
    assert asset.path == "mesh/native"
    assert asset.install_path == "constant/polyMesh"
    assert asset.sha256 == asset.bundle_manifest_sha256
    payload = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert len(payload["assets"]) == 1
    assert "members" not in payload["assets"][0]


def test_task_draft_rejects_unpaired_directory_arguments(
    tmp_path: Path,
    capsys,
) -> None:
    request = tmp_path / "request.md"
    request.write_text("Use the supplied native mesh.", encoding="utf-8")

    assert main(
        [
            "task",
            "draft",
            "--request-file",
            str(request),
            "--asset-dir",
            "mesh/native",
            "--output",
            str(tmp_path / "draft.yaml"),
            "--json",
        ]
    ) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "INVALID_INPUT"
    assert "paired" in payload["error"]


def test_validate_draft_returns_four_for_blocking_review(
    tmp_path: Path,
    capsys,
) -> None:
    path = tmp_path / "draft.yaml"
    _write_draft(path, _without(_complete_draft(), "materials.fluid"))

    assert main(["task", "validate-draft", str(path), "--json"]) == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "TASK_REQUEST_INCOMPLETE"
    assert any(item["severity"] == "blocking" for item in payload["issues"])


def test_compile_writes_canonical_task_and_visible_assumptions(
    tmp_path: Path,
    capsys,
) -> None:
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "task.yaml"
    _write_draft(draft, _complete_draft())

    assert main(
        [
            "task",
            "compile",
            str(draft),
            "--output",
            str(output),
            "--json",
        ]
    ) == 0

    task_payload = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert task_payload["schema_version"] == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["assumptions"]
    assert len(payload["task_sha256"]) == 64


def test_task_outputs_are_created_exclusively(tmp_path: Path, capsys) -> None:
    draft = tmp_path / "draft.yaml"
    output = tmp_path / "task.yaml"
    _write_draft(draft, _complete_draft())
    output.write_text("user-owned\n", encoding="utf-8")

    assert main(
        [
            "task",
            "compile",
            str(draft),
            "--output",
            str(output),
            "--json",
        ]
    ) == 2
    assert output.read_text(encoding="utf-8") == "user-owned\n"
    assert json.loads(capsys.readouterr().out)["status"] == "INVALID_INPUT"


def test_task_draft_rejects_existing_output_before_model_call(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    request = tmp_path / "request.md"
    request.write_text("Solve a complete public task.", encoding="utf-8")
    output = tmp_path / "draft.yaml"
    output.write_text("user-owned\n", encoding="utf-8")
    monkeypatch.setattr(
        "foampilot.cli.main._native_gateway",
        lambda arguments, **kwargs: (_ for _ in ()).throw(
            AssertionError("model gateway must not be created")
        ),
    )

    assert main(
        [
            "task",
            "draft",
            "--request-file",
            str(request),
            "--output",
            str(output),
            "--json",
        ]
    ) == 2

    assert output.read_text(encoding="utf-8") == "user-owned\n"
    assert json.loads(capsys.readouterr().out)["status"] == "INVALID_INPUT"


def test_task_draft_rejects_oversized_asset_before_model_call(
    tmp_path: Path,
    capsys,
) -> None:
    request = tmp_path / "request.md"
    request.write_text("Use the declared geometry.", encoding="utf-8")
    asset = tmp_path / "body.stl"
    with asset.open("wb") as stream:
        stream.truncate(256 * 1024 * 1024 + 1)

    assert main(
        [
            "task",
            "draft",
            "--request-file",
            str(request),
            "--asset",
            asset.name,
            "--output",
            str(tmp_path / "draft.yaml"),
            "--json",
        ]
    ) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "INVALID_INPUT"
    assert "size limit" in payload["error"]


def test_task_draft_reports_gateway_failure_in_chinese(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    request = tmp_path / "request.md"
    request.write_text("求解一个完整的公开 CFD 问题。", encoding="utf-8")
    failure = BackendError(
        kind=BackendFailureKind.PROCESS_INTERRUPTED,
        backend_id="codex-cli",
        model="gpt-test",
        purpose="extract-cfd-task-draft",
        detail="external command failed",
        retryable=True,
    )

    def fail_extract(*args, **kwargs):
        del args, kwargs
        raise GatewayRequestError(
            failure=failure,
            logical_request_id="draft-request-1",
            transport_attempts=2,
            backend_switches=0,
            deadline_reason=None,
        )

    monkeypatch.setattr("foampilot.cli.main.extract_task_draft", fail_extract)
    monkeypatch.setattr(
        "foampilot.cli.main._native_gateway",
        lambda arguments, **kwargs: object(),
    )

    assert main(
        [
            "task",
            "draft",
            "--request-file",
            str(request),
            "--output",
            str(tmp_path / "draft.yaml"),
            "--json",
        ]
    ) == 3

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "TASK_EXTRACTION_DEFERRED"
    assert payload["code"] == "PROCESS_INTERRUPTED"
    assert payload["message"] == "外部模型进程异常中断。"
    assert payload["retryable"] is True
    assert payload["transport_attempts"] == 2
    assert "external command failed" not in payload["message"]
