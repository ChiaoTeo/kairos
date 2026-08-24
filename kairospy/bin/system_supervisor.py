from __future__ import annotations

import argparse
import json
from pathlib import Path

from kairospy.system.apps.components.application.observability import (
    configure_from_environment,
    record_counter,
    record_gauge,
    start_span,
)
from kairospy.system.apps.components.application import (
    ComponentProcessApplication,
    SystemRuntimeSupervisor,
)
from kairospy.system.apps.workspace.application import WorkspaceApplication


def main() -> int:
    parser = argparse.ArgumentParser(prog="kairos-system-supervisor")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    workspace = WorkspaceApplication().open(Path(args.workspace))
    telemetry = configure_from_environment(
        "system-supervisor", workspace_id=workspace.identity.workspace_id
    )
    processes = ComponentProcessApplication(workspace)
    lock = workspace.paths.process_lock("system-supervisor")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl

        stream = lock.open("a+")
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        stream.seek(0)
        stream.truncate()
        stream.write(str(__import__("os").getpid()))
        stream.flush()
    except (BlockingIOError, OSError):
        if telemetry is not None:
            telemetry.shutdown()
        return 0
    try:
        record_gauge("kairos.process.ready", 1)
        while True:
            desired_path = workspace.paths.run / "supervisor" / "desired.json"
            desired = {}
            if desired_path.is_file():
                try:
                    value = json.loads(desired_path.read_text(encoding="utf-8"))
                    desired = value if isinstance(value, dict) else {}
                except (OSError, ValueError, json.JSONDecodeError):
                    desired = {}
            with start_span(
                "system.reconcile",
                attributes={"component": "system-supervisor"},
            ):
                supervisor = SystemRuntimeSupervisor(processes, desired=desired)
                supervisor.reconcile_once()
                record_counter("kairos.system.reconcile.total")
            import time

            time.sleep(args.interval)
    finally:
        stream.close()
        if telemetry is not None:
            telemetry.force_flush()
            telemetry.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
