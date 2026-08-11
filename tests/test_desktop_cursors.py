from __future__ import annotations

from pathlib import Path

from foampilot.desktop.cursors import IncrementalLineCursor, ResidualLogCursor


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


def test_residual_cursor_is_incremental_and_bounded(tmp_path: Path) -> None:
    path = tmp_path / "solve.stdout.log"
    path.write_text(
        "Time = 1\n"
        "Solving for Ux, Initial residual = 0.1, Final residual = 0.01, "
        "No Iterations 1\n",
        encoding="utf-8",
    )
    cursor = ResidualLogCursor(
        path,
        attempt=1,
        source_log="attempt-01/solve.stdout.log",
        sample_limit=2,
    )
    first = cursor.read()
    first_offset = cursor.offset
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            "Time = 2\n"
            "Solving for Uy, Initial residual = 0.2, Final residual = 0.02, "
            "No Iterations 2\n"
            "Solving for p, Initial residual = 0.3, Final residual = 0.03, "
            "No Iterations 3\n"
        )
    second = cursor.read()

    assert len(first) == 1
    assert cursor.offset > first_offset
    assert [sample.field for sample in second] == ["Uy", "p"]
    assert [sample.simulation_time for sample in second] == [2.0, 2.0]
