"""Workspace-owned launch and instance registry queries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..domain.identity import LaunchIdentity
from kairospy.system.apps.operations.application.application import OperationJournal
from kairospy.system.apps.workspace.application import Workspace


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
        existing = next(
            (
                entry
                for entry in value.get("launches", [])
                if entry.get("launch_id") == launch_id
                and entry.get("mode") == mode
                and entry.get("instance_id") == instance_id
            ),
            None,
        )
        if (
            existing
            and strategy_ref is not None
            and existing.get("strategy") not in {None, strategy_ref}
        ):
            raise RuntimeError("launch instance is already bound to another strategy")
        entries = [
            entry
            for entry in value.get("launches", [])
            if not (
                entry.get("launch_id") == launch_id
                and entry.get("mode") == mode
                and entry.get("instance_id") == instance_id
            )
        ]
        now = datetime.now(timezone.utc).isoformat()
        entry = {
            "launch_id": launch_id,
            "mode": mode,
            "instance_id": instance_id,
            "socket": str(
                self.workspace.paths.launch_socket(mode, launch_id, instance_id)
            ),
            "state": "created",
            "created_at": (
                str(existing.get("created_at"))
                if existing and existing.get("created_at")
                else now
            ),
            "updated_at": now,
        }
        if strategy_ref is not None:
            entry["strategy"] = strategy_ref
        if config_path is not None:
            entry["config"] = str(Path(config_path).expanduser().resolve())
        entries.append(entry)
        self._write(
            {
                "launches": sorted(
                    entries,
                    key=lambda item: (
                        item["mode"],
                        item["launch_id"],
                        item["instance_id"],
                    ),
                )
            }
        )
        launch_root = self.workspace.paths.launch_root(mode, launch_id)
        instance = self.workspace.instance(mode, launch_id, instance_id)
        instance.prepare()
        (launch_root / "current.json").write_text(
            json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        instance.component_manifest().write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "launch_id": launch_id,
                    "instance_id": instance_id,
                    "mode": mode,
                    "accounts": {},
                    "components": {},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        OperationJournal(self.workspace).append(
            "launch.register", subject=f"{mode}/{launch_id}/{instance_id}"
        )
        return entry

    def remove(
        self, launch_id: str, *, mode: str = "paper", instance_id: str = "default"
    ) -> dict[str, Any]:
        current = self.list()
        remaining = [
            entry
            for entry in current
            if not (
                entry.get("launch_id") == launch_id
                and entry.get("mode") == mode
                and entry.get("instance_id") == instance_id
            )
        ]
        if len(remaining) == len(current):
            raise FileNotFoundError(
                f"launch instance does not exist: {launch_id}/{instance_id}"
            )
        self._write({"launches": remaining})
        OperationJournal(self.workspace).append(
            "launch.remove", subject=f"{mode}/{launch_id}/{instance_id}"
        )
        return {
            "launch_id": launch_id,
            "mode": mode,
            "instance_id": instance_id,
            "status": "removed",
        }

    def update_state(
        self, launch_id: str, *, mode: str = "paper", instance_id: str, state: str
    ) -> dict[str, Any]:
        value = self._read()
        updated = None
        for entry in value.get("launches", []):
            if (
                entry.get("launch_id") == launch_id
                and entry.get("mode") == mode
                and entry.get("instance_id") == instance_id
            ):
                entry["state"] = state
                entry["updated_at"] = datetime.now(timezone.utc).isoformat()
                updated = dict(entry)
                break
        if updated is None:
            raise FileNotFoundError(
                f"launch instance does not exist: {launch_id}/{instance_id}"
            )
        self._write(value)
        current = self.workspace.paths.launch_root(mode, launch_id) / "current.json"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_text(
            json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return updated

    def diagnose(
        self, launch_id: str, *, mode: str = "paper", instance_id: str = "default"
    ) -> dict[str, Any]:
        entry = next(
            (
                item
                for item in self.list()
                if item.get("launch_id") == launch_id
                and item.get("mode") == mode
                and item.get("instance_id") == instance_id
            ),
            None,
        )
        if entry is None:
            return {"ok": False, "issues": ["launch instance is not registered"]}
        socket = Path(entry["socket"])
        return {
            "ok": True,
            "issues": [],
            "registered": entry,
            "socket_exists": socket.exists(),
        }

    def instances(self, launch_id: str | None = None) -> list[dict[str, Any]]:
        return [
            entry
            for entry in self.list()
            if launch_id is None or entry.get("launch_id") == launch_id
        ]

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"launches": []}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"launches": []}

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


__all__ = ["LaunchRegistryApplication"]
