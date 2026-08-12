from __future__ import annotations

from pathlib import Path

from foampilot.desktop.cursors import IncrementalLineCursor


def test_line_cursor_holds_partial_tail_and_reads_only_new_bytes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"sequence":1}\n{"sequence":')
    cursor = IncrementalLineCursor(path)

    first = cursor.read()
    path.write_bytes(path.read_bytes() + b"2}\n")
    second = cursor.read()

    assert first.lines == ((1, '{"sequence":1}'),)
    assert second.lines == ((2, '{"sequence":2}'),)
    assert first.bytes_read + second.bytes_read == path.stat().st_size
    assert second.reset is False


def test_line_cursor_resets_on_truncate_or_rotation(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("old-1\nold-2\n", encoding="utf-8")
    cursor = IncrementalLineCursor(path)
    cursor.read()

    path.write_text("new-1\n", encoding="utf-8")
    chunk = cursor.read()

    assert chunk.reset is True
    assert chunk.lines == ((1, "new-1"),)
