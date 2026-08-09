"""Workspace-scoped runtime supervision.

This layer owns process lifecycle and recovery policy only. It never owns
business state and it never retries a business command.
"""

from __future__ import annotations

import time
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from . import ComponentProcessApplication, SYSTEM_COMPONENTS


# Launch owns Account, Risk, Execution, and instance-local Market runtimes.
# The detached workspace supervisor may only reconcile services whose identity
# is workspace-scoped and whose configuration is safe to persist globally.
SUPERVISED_COMPONENTS = ("reference", "market")


@dataclass(frozen=True, slots=True)
class RestartPolicy:
    auto_restart: bool
    max_attempts: int = 3
    backoff_seconds: float = 2.0
    reconcile_before_restart: bool = False


DEFAULT_RESTART_POLICIES: Mapping[str, RestartPolicy] = {
    "reference": RestartPolicy(True),
    "market": RestartPolicy(True),
}


@dataclass(slots=True)
class SystemRuntimeSupervisor:
    """Reconcile configured long-lived components in one workspace."""

    processes: ComponentProcessApplication
    desired: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    policies: Mapping[str, RestartPolicy] = field(default_factory=lambda: DEFAULT_RESTART_POLICIES)
    _attempts: dict[str, int] = field(default_factory=dict, init=False)
    _last_attempt: dict[str, float] = field(default_factory=dict, init=False)

    @property
    def desired_path(self) -> Path:
        return self.processes.workspace.paths.run / "supervisor" / "desired.json"

    def register(self, component: str, options: Mapping[str, Any] | None = None) -> None:
        if component not in SUPERVISED_COMPONENTS:
            raise ValueError(
                f"{component} is launch-owned; only {', '.join(SUPERVISED_COMPONENTS)} "
                "can be registered with the workspace supervisor"
            )
        path = self.desired_path
        path.parent.mkdir(parents=True, exist_ok=True)
        current: dict[str, Any] = {}
        if path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    current = value
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        current[component] = dict(options or {})
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(current, sort_keys=True), encoding="utf-8")
        temporary.replace(path)

    def unregister(self, component: str) -> None:
        if component not in SUPERVISED_COMPONENTS:
            return
        path = self.desired_path
        if not path.is_file():
            return
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            current = {}
        if isinstance(current, dict):
            current.pop(component, None)
            path.write_text(json.dumps(current, sort_keys=True), encoding="utf-8")

    def start_background(self) -> None:
        """Start one detached supervisor for this workspace if absent."""
        lock = self.processes.workspace.paths.process_lock("system-supervisor")
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock.exists():
            # The child owns the advisory lock. A live PID in the lock file is
            # enough to avoid spawning duplicate supervisors.
            try:
                pid = int(lock.read_text(encoding="utf-8").strip())
                os.kill(pid, 0)
                return
            except (OSError, ValueError):
                pass
        log_dir = self.processes.workspace.paths.logs / "processes"
        log_dir.mkdir(parents=True, exist_ok=True)
        log = (log_dir / "system-supervisor.log").open("ab")
        try:
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "kairospy.bin.system_supervisor",
                    "--workspace",
                    str(self.processes.workspace.paths.root),
                ],
                cwd=str(self.processes.workspace.paths.root),
                env={**os.environ, "KAIROS_SUPERVISOR_CHILD": "1"},
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            log.close()

    def reconcile_once(self) -> dict[str, dict[str, Any]]:
        statuses = self.processes.list_status()
        for component in SYSTEM_COMPONENTS:
            status = statuses[component].get("status")
            if status in {"ready", "ok", "running", "degraded"}:
                self._attempts.pop(component, None)
                continue
            if component not in SUPERVISED_COMPONENTS or component not in self.desired:
                continue
            policy = self.policies.get(component, RestartPolicy(False))
            if not policy.auto_restart:
                statuses[component] = self._manual_reconcile_required(component, statuses[component])
                continue
            attempt = self._attempts.get(component, 0)
            if attempt >= policy.max_attempts:
                statuses[component] = {
                    **statuses[component],
                    "status": "failed",
                    "error": "automatic restart circuit breaker is open",
                    "restart_attempts": attempt,
                }
                continue
            now = time.monotonic()
            if now - self._last_attempt.get(component, 0.0) < policy.backoff_seconds:
                statuses[component] = {
                    **statuses[component],
                    "status": "recovering",
                    "restart_attempts": attempt,
                }
                continue
            self._last_attempt[component] = now
            self._attempts[component] = attempt + 1
            try:
                control = self.processes.ensure_running(
                    component, **dict(self.desired[component])
                )
                statuses[component] = {
                    **control.status(),
                    "status": "ready",
                    "restart_attempts": attempt + 1,
                }
            except Exception as error:
                statuses[component] = {
                    **statuses[component],
                    "status": "recovering",
                    "error": str(error),
                    "restart_attempts": attempt + 1,
                }
        return statuses

    def _manual_reconcile_required(
        self, component: str, status: Mapping[str, Any]
    ) -> dict[str, Any]:
        result = dict(status)
        result["status"] = "manual_reconcile_required"
        result["error"] = (
            f"{component} is not safe to restart automatically; "
            "reconcile its external state before restarting"
        )
        return result

    def run_forever(self, *, interval: float = 1.0) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        while True:
            self.reconcile_once()
            time.sleep(interval)
