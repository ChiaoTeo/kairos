from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path

from kairospy.system.apps.components.application.process_logging import (
    current_run_id,
    filter_log_lines,
    parse_since,
    start_logged_process,
)
from kairospy.bin.log_sink import run


def test_log_sink_rotates_previous_run_and_emits_strict_jsonl(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "reference.log"
    path.write_text("legacy plain text\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            '{"event":"process_spawned","pid":42,"level":"INFO"}\n'
            '{"event":"reference_refresh_completed","level":"INFO"}\n'
            "thread main panicked at test.rs:1\n"
        ),
    )

    assert run(path, component="reference", run_id="run-1", max_bytes=4096) == 0

    assert (tmp_path / "reference.log.1").read_text(encoding="utf-8") == (
        "legacy plain text\n"
    )
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [event["event"] for event in events] == [
        "process_spawned",
        "reference_refresh_completed",
        "process_output",
    ]
    assert all(event["component"] == "reference" for event in events)
    assert all(event["run_id"] == "run-1" for event in events)
    assert all(event["pid"] == 42 for event in events)
    assert events[-1]["level"] == "ERROR"


def test_process_log_filters_use_structured_fields_and_current_run() -> None:
    lines = [
        json.dumps(
            {
                "timestamp": "2026-08-17T07:00:00Z",
                "level": "WARN",
                "event": "reference_provider_degraded",
                "provider": "massive-equity",
                "run_id": "old",
            }
        ),
        json.dumps(
            {
                "timestamp": "2026-08-17T08:00:00Z",
                "level": "WARN",
                "event": "reference_provider_unavailable",
                "provider": "massive-equity",
                "run_id": "new",
            }
        ),
    ]

    run_id = current_run_id(lines)
    assert run_id == "new"
    assert filter_log_lines(
        lines,
        level="warn",
        provider="massive-equity",
        run_id=run_id,
        since=parse_since("2026-08-17T07:30:00Z"),
    ) == [lines[-1]]


def test_logged_process_termination_reaps_child_and_sink(tmp_path: Path) -> None:
    process = start_logged_process(
        [sys.executable, "-c", "import time; print('started'); time.sleep(30)"],
        component="test-component",
        log_path=tmp_path / "process.log",
        cwd=str(Path.cwd()),
        environment=os.environ.copy(),
    )
    child_pid = process.child.pid
    sink_pid = process.sink.pid

    process.terminate(timeout=2)

    assert process.child.poll() is not None
    assert process.sink.poll() is not None
    for pid in (child_pid, sink_pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        for _ in range(20):
            time.sleep(0.01)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        else:  # pragma: no cover - diagnostic guard for an OS-level leak
            raise AssertionError(f"process {pid} remained alive")
