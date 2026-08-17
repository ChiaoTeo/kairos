"""Workspace-owned process log transport and lifecycle.

Business processes continue to write structured events to stderr. This module
owns the OS pipe, run identity, and the detached rotating JSONL sink so logging
survives the short-lived CLI process that launched the component.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


def utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def parse_since(value: str | None) -> datetime | None:
    if value is None:
        return None
    match = re.fullmatch(r"(\d+)(s|m|h|d)", value.strip().lower())
    if match:
        seconds = (
            int(match.group(1))
            * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
        )
        return datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - seconds, timezone.utc
        )
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError as error:
        raise ValueError(
            "--since must be a duration such as 10m or an RFC3339 timestamp"
        ) from error


def decode_log_event(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def current_run_id(lines: Sequence[str]) -> str | None:
    for line in reversed(lines):
        event = decode_log_event(line)
        if event is not None and isinstance(event.get("run_id"), str):
            return event["run_id"]
    return None


def filter_log_lines(
    lines: Sequence[str],
    *,
    level: str | None = None,
    event_name: str | None = None,
    provider: str | None = None,
    since: datetime | None = None,
    run_id: str | None = None,
) -> list[str]:
    selected: list[str] = []
    expected_level = level.upper() if level else None
    for line in lines:
        value = decode_log_event(line)
        if not any((expected_level, event_name, provider, since, run_id)):
            selected.append(line)
            continue
        if value is None:
            continue
        if expected_level and str(value.get("level", "")).upper() != expected_level:
            continue
        if event_name and value.get("event") != event_name:
            continue
        if provider and value.get("provider") != provider:
            continue
        if run_id and value.get("run_id") != run_id:
            continue
        if since is not None:
            timestamp = value.get("timestamp")
            if not isinstance(timestamp, str):
                continue
            try:
                observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            if observed.astimezone(timezone.utc) < since:
                continue
        selected.append(line)
    return selected


def start_logged_process(
    command: Sequence[str],
    *,
    component: str,
    log_path: Path,
    cwd: str,
    environment: Mapping[str, str],
) -> subprocess.Popen[bytes]:
    """Start a detached child whose combined output becomes rotating JSONL."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid.uuid4())
    sink = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "kairospy.bin.log_sink",
            "--component",
            component,
            "--run-id",
            run_id,
            "--path",
            str(log_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    if sink.stdin is None:  # pragma: no cover - subprocess contract guard
        raise RuntimeError("process log sink did not expose stdin")
    child_environment = {
        **environment,
        "KAIROS_RUN_ID": run_id,
        # Keep SQLx's unbounded statement field out of normal operational logs.
        # An explicit user RUST_LOG remains authoritative for diagnostics.
        **({} if "RUST_LOG" in environment else {"RUST_LOG": "info,sqlx::query=error"}),
    }
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=child_environment,
            stdout=sink.stdin,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        metadata: dict[str, Any] = {
            "timestamp": utc_timestamp(),
            "level": "INFO",
            "event": "process_spawned",
            "message": "component process spawned",
            "component": component,
            "run_id": run_id,
            "pid": process.pid,
        }
        sink.stdin.write(
            (json.dumps(metadata, separators=(",", ":")) + "\n").encode("utf-8")
        )
        sink.stdin.flush()
    except BaseException:
        sink.stdin.close()
        sink.terminate()
        raise
    sink.stdin.close()
    # Retain the sink handle for the lifetime of the child Popen object. The
    # sink exits naturally when the child closes its inherited pipe.
    process._kairos_log_sink = sink  # type: ignore[attr-defined]
    return process
