from __future__ import annotations

from pathlib import Path

import pytest

from foampilot.models import (
    load_codex_access_token,
)


def test_codex_auth_reads_nested_tokens_without_logging_token(
    tmp_path: Path,
) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(
        '{"tokens":{"access_token":"secret-value"}}',
        encoding="utf-8",
    )
    assert load_codex_access_token(auth) == "secret-value"


def test_codex_auth_error_does_not_echo_file_contents(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(
        '{"unrelated":"do-not-echo-this"}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as captured:
        load_codex_access_token(auth)
    assert "do-not-echo-this" not in str(captured.value)
