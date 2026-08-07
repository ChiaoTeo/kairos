"""Workspace-owned launch and instance registry queries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.identity import LaunchIdentity
from ...workspace import OperationJournal, Workspace


@dataclass(frozen=True, slots=True)
class LaunchRegistryApplication:
    workspace: Workspace

    @property
    def path(self) -> Path:
        return self.workspace.paths.launch_index()

    def list(self) -> list[dict[str, Any]]:
        return list(self._read().get("launches", []))

    def add(
        self,
        launch_id: str,
        *,
        mode: str = "paper",
        instance_id: str = "default",
        strategy_ref: str | None = None,
        config_path: str | Path | None = None,
    ) -> dict[str, Any]:
        LaunchIdentity(launch_id, mode)
        if not instance_id.strip():
            raise ValueError("instance_id is required")
        value = self._read()
        existing = next((entry for entry in value.get("launches", []) if
                         entry.get("launch_id") == launch_id and
                         entry.get("mode") == mode and
                         entry.get("instance_id") == instance_id), None)
        if existing and strategy_ref is not None and existing.get("strategy") not in {None, strategy_ref}:
            raise RuntimeError("launch instance is already bound to another strategy")
        entries = [entry for entry in value.get("launches", []) if not (entry.get("launch_id") == launch_id and entry.get("mode") == mode and entry.get("instance_id") == instance_id)]
        entry = {"launch_id": launch_id, "mode": mode, "instance_id": instance_id,
                 "socket": str(self.workspace.paths.launch_socket(mode, launch_id, instance_id)), "state": "created"}
        if strategy_ref is not None:
            entry["strategy"] = strategy_ref
        if config_path is not None:
            entry["config"] = str(Path(config_path).expanduser().resolve())
        entries.append(entry)
        self._write({"launches": sorted(entries, key=lambda item: (item["mode"], item["launch_id"], item["instance_id"]))})
        launch_root = self.workspace.paths.launch_root(mode, launch_id)
        instance_root = self.workspace.paths.launch_instance_root(mode, launch_id, instance_id)
        instance_root.mkdir(parents=True, exist_ok=True)
        (launch_root / "current.json").write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for name, value in (("state.json", {"state": "created"}), ("command.json", {"command": ""})):
            (instance_root / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (instance_root / "run.sqlite").touch()
        (instance_root / "launch.log").touch()
        OperationJournal(self.workspace).append("launch.register", subject=f"{mode}/{launch_id}/{instance_id}")
        return entry

    def remove(self, launch_id: str, *, mode: str = "paper", instance_id: str = "default") -> dict[str, Any]:
        current = self.list()
        remaining = [entry for entry in current if not (entry.get("launch_id") == launch_id and entry.get("mode") == mode and entry.get("instance_id") == instance_id)]
        if len(remaining) == len(current):
            raise FileNotFoundError(f"launch instance does not exist: {launch_id}/{instance_id}")
        self._write({"launches": remaining})
        OperationJournal(self.workspace).append("launch.remove", subject=f"{mode}/{launch_id}/{instance_id}")
        return {"launch_id": launch_id, "mode": mode, "instance_id": instance_id, "status": "removed"}

    def diagnose(self, launch_id: str, *, mode: str = "paper", instance_id: str = "default") -> dict[str, Any]:
        entry = next((item for item in self.list() if item.get("launch_id") == launch_id and item.get("mode") == mode and item.get("instance_id") == instance_id), None)
        if entry is None:
            return {"ok": False, "issues": ["launch instance is not registered"]}
        socket = Path(entry["socket"])
        return {"ok": True, "issues": [], "registered": entry, "socket_exists": socket.exists()}

    def instances(self, launch_id: str | None = None) -> list[dict[str, Any]]:
        return [entry for entry in self.list() if launch_id is None or entry.get("launch_id") == launch_id]

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"launches": []}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"launches": []}

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


__all__ = ["LaunchRegistryApplication"]
