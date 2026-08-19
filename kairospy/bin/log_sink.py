"""Detached rotating JSONL sink for workspace-owned component processes."""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

from kairospy.application.system.process_logging import utc_timestamp


DEFAULT_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5


def _rotate_run_boundary(path: Path, backup_count: int) -> None:
    if not path.exists() or path.stat().st_size == 0:
        return
    oldest = path.with_name(f"{path.name}.{backup_count}")
    oldest.unlink(missing_ok=True)
    for index in range(backup_count - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{index + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def _normalize(
    line: str, *, component: str, run_id: str, child_pid: int | None
) -> tuple[dict[str, Any] | None, int | None]:
    line = line.rstrip("\r\n")
    if not line:
        return None, child_pid
    try:
        parsed = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    event: dict[str, Any]
    if isinstance(parsed, dict):
        event = dict(parsed)
        if event.get("event") == "process_spawned" and isinstance(
            event.get("pid"), int
        ):
            child_pid = event["pid"]
    else:
        lowered = line.lower()
        level = (
            "ERROR"
            if any(
                token in lowered for token in ("error", "failed", "panic", "unexpected")
            )
            else "INFO"
        )
        event = {
            "timestamp": utc_timestamp(),
            "level": level,
            "event": "process_output",
            "message": line,
            "stream": "combined",
        }
    event.setdefault("timestamp", utc_timestamp())
    event.setdefault("level", "INFO")
    event.setdefault("event", "process_output")
    event.setdefault("component", component)
    event.setdefault("run_id", run_id)
    if child_pid is not None:
        event.setdefault("pid", child_pid)
    return event, child_pid


def run(
    path: Path,
    *,
    component: str,
    run_id: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_run_boundary(path, backup_count)
    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(f"kairos.log_sink.{component}.{run_id}")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    child_pid: int | None = None
    try:
        for line in sys.stdin:
            event, child_pid = _normalize(
                line, component=component, run_id=run_id, child_pid=child_pid
            )
            if event is None:
                continue
            logger.info(json.dumps(event, separators=(",", ":"), ensure_ascii=False))
            handler.flush()
    finally:
        handler.flush()
        handler.close()
        logger.removeHandler(handler)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="kairos-process-log-sink")
    parser.add_argument("--component", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--backup-count", type=int, default=DEFAULT_BACKUP_COUNT)
    args = parser.parse_args()
    return run(
        args.path,
        component=args.component,
        run_id=args.run_id,
        max_bytes=args.max_bytes,
        backup_count=args.backup_count,
    )


if __name__ == "__main__":
    raise SystemExit(main())
