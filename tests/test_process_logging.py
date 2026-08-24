from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from kairospy.system.apps.components.application.process_logging import (
    current_run_id,
    filter_log_lines,
    parse_since,
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
