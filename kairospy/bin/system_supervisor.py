from __future__ import annotations

import argparse
import json
from pathlib import Path

from kairospy.application.system import ComponentProcessApplication, SystemRuntimeSupervisor
from kairospy.application.workspace import WorkspaceApplication


def main() -> int:
    parser = argparse.ArgumentParser(prog="kairos-system-supervisor")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    workspace = WorkspaceApplication().open(Path(args.workspace))
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
        return 0
    try:
        while True:
            desired_path = workspace.paths.run / "supervisor" / "desired.json"
            desired = {}
            if desired_path.is_file():
                try:
                    value = json.loads(desired_path.read_text(encoding="utf-8"))
                    desired = value if isinstance(value, dict) else {}
                except (OSError, ValueError, json.JSONDecodeError):
                    desired = {}
            supervisor = SystemRuntimeSupervisor(processes, desired=desired)
            supervisor.reconcile_once()
            import time
            time.sleep(args.interval)
    finally:
        stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
