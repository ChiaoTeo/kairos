#!/usr/bin/env python3
"""Plan or apply the canonical Kairos resource-scope directory migration."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess


@dataclass(frozen=True, slots=True)
class Move:
    source: str
    destination: str


class LayoutMigration:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.moves: list[Move] = []
        self.conflicts: list[str] = []

    def file(self, source: Path, destination: Path) -> None:
        if not source.is_file() and not source.is_symlink():
            return
        if destination.exists():
            self.conflicts.append(
                f"destination exists: {destination.relative_to(self.root)} "
                f"(source {source.relative_to(self.root)})"
            )
            return
        self.moves.append(
            Move(
                str(source.relative_to(self.root)),
                str(destination.relative_to(self.root)),
            )
        )

    def tree(self, source: Path, destination: Path) -> None:
        if not source.is_dir():
            return
        for path in sorted(source.rglob("*")):
            if path.is_file() or path.is_symlink():
                self.file(path, destination / path.relative_to(source))

    def plan(self) -> None:
        self.tree(self.root / "accounts", self.root / "config" / "accounts")
        self.tree(self.root / "credentials", self.root / "config" / "credentials")
        self.tree(self.root / "reference", self.root / "state" / "reference")
        self.tree(
            self.root / "orders",
            self.root / "state" / "execution" / "orders",
        )
        self.tree(
            self.root / "backups",
            self.root / "state" / "workspace" / "archives",
        )
        self.tree(
            self.root / "market" / "connections",
            self.root / "config" / "market" / "connections",
        )

        self._workspace_runtime()
        self._component_logs(self.root / "logs" / "processes", self.root / "logs")
        self._launch_logs()
        self.tree(
            self.root / "snapshots" / "v2" / "market" / "market-shared",
            self.root / "snapshots" / "market" / "market-shared",
        )
        self.tree(
            self.root
            / "snapshots"
            / "market"
            / "snapshots"
            / "v2"
            / "market"
            / "market-shared",
            self.root / "snapshots" / "market" / "market-shared",
        )

        launches = self.root / "launches"
        for instances in sorted(launches.glob("*/*/instances")):
            for instance in sorted(
                path for path in instances.iterdir() if path.is_dir()
            ):
                self._instance(instance)

    def _launch_logs(self) -> None:
        legacy = self.root / "logs" / "launches"
        if not legacy.is_dir():
            return
        for source in sorted(legacy.glob("*/*/*/strategy.log*")):
            mode, launch_id, instance_id = source.relative_to(legacy).parts[:3]
            suffix = source.name[len("strategy.log") :]
            destination = (
                self.root
                / "launches"
                / mode
                / launch_id
                / "instances"
                / instance_id
                / "logs"
                / "strategy"
                / f"process.log{suffix}"
            )
            self.file(source, destination)

    def _workspace_runtime(self) -> None:
        runtime = self.root / "run"
        if not runtime.is_dir():
            return
        for component in sorted(path for path in runtime.iterdir() if path.is_dir()):
            name = component.name
            self.file(component / f"{name}.sock", component / "control.sock")
            self.file(component / f"{name}.lock", component / "process.lock")

    def _component_logs(self, legacy: Path, canonical_root: Path) -> None:
        if not legacy.is_dir():
            return
        for source in sorted(path for path in legacy.iterdir() if path.is_file()):
            name = source.name
            marker = name.find(".log")
            if marker <= 0:
                continue
            component = name[:marker]
            suffix = name[marker + len(".log") :]
            self.file(
                source,
                canonical_root / component / f"process.log{suffix}",
            )

    def _instance(self, instance: Path) -> None:
        for source, destination in (
            ("normalized-config.json", "config/normalized.json"),
            ("lifecycle.jsonl", "state/launch/lifecycle.jsonl"),
            ("command.json", "state/launch/command.json"),
            ("state.json", "state/launch/status.json"),
            ("run.sqlite", "state/launch/run.sqlite"),
            ("launch.log", "logs/launch/legacy.log"),
            ("strategy.sock", "run/strategy/control.sock"),
            ("state/component-endpoints.json", "manifest.json"),
            ("checkpoints/market-replay.json", "state/market/checkpoints/replay.json"),
        ):
            self.file(instance / source, instance / destination)

        for legacy_name, canonical_name in (
            ("sockets", "control.sock"),
            ("health", "health.json"),
            ("locks", "process.lock"),
        ):
            legacy = instance / legacy_name
            if not legacy.is_dir():
                continue
            for source in sorted(path for path in legacy.iterdir() if path.is_file()):
                component = source.name.split(".", 1)[0]
                self.file(source, instance / "run" / component / canonical_name)

        self._component_logs(instance / "logs" / "processes", instance / "logs")
        for source in sorted((instance / "logs").glob("strategy.log*")):
            suffix = source.name[len("strategy.log") :]
            self.file(source, instance / "logs" / "strategy" / f"process.log{suffix}")
        self.tree(
            instance / "snapshots" / "v2" / "market" / "market-shared",
            instance / "snapshots" / "market" / "market-shared",
        )

    def apply(self) -> Path:
        if self.conflicts:
            raise RuntimeError("migration has path conflicts; no files were moved")
        for move in self.moves:
            source = self.root / move.source
            destination = self.root / move.destination
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
        self._remove_empty_legacy_directories()
        journal = (
            self.root
            / "state"
            / "workspace"
            / "layout-migrations"
            / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        )
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            json.dumps(
                {
                    "version": 2,
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                    "moves": [asdict(move) for move in self.moves],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return journal

    def _remove_empty_legacy_directories(self) -> None:
        candidates = [
            self.root / "accounts",
            self.root / "credentials",
            self.root / "reference",
            self.root / "orders",
            self.root / "backups",
            self.root / "market" / "connections",
            self.root / "logs" / "processes",
            self.root / "logs" / "launches",
            self.root / "snapshots" / "v2",
        ]
        candidates.extend(self.root.glob("launches/*/*/instances/*/sockets"))
        candidates.extend(self.root.glob("launches/*/*/instances/*/health"))
        candidates.extend(self.root.glob("launches/*/*/instances/*/locks"))
        candidates.extend(self.root.glob("launches/*/*/instances/*/checkpoints"))
        candidates.extend(self.root.glob("launches/*/*/instances/*/logs/processes"))
        for directory in sorted(
            candidates, key=lambda path: len(path.parts), reverse=True
        ):
            if directory.is_dir():
                for descendant in sorted(
                    (path for path in directory.rglob("*") if path.is_dir()),
                    key=lambda path: len(path.parts),
                    reverse=True,
                ):
                    try:
                        descendant.rmdir()
                    except OSError:
                        pass
            current = directory
            while current != self.root:
                try:
                    current.rmdir()
                except (FileNotFoundError, OSError):
                    break
                current = current.parent


def active_kairos_processes(root: Path) -> list[str]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        check=True,
    )
    root_text = str(root.resolve())
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if root_text in line
        and any(
            marker in line for marker in ("kairos-", "kairospy.bin", "kairospy/_bin")
        )
        and "migrate_kairos_layout.py" not in line
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    root = args.workspace.expanduser().resolve()
    if root.name != ".kairos" and (root / ".kairos" / "kairos.toml").is_file():
        root = root / ".kairos"
    if not (root / "kairos.toml").is_file() and not (root / "workspace.toml").is_file():
        parser.error(f"not a Kairos workspace: {root}")

    migration = LayoutMigration(root)
    migration.plan()
    processes = active_kairos_processes(root)
    report = {
        "workspace": str(root),
        "mode": "apply" if args.apply else "dry-run",
        "move_count": len(migration.moves),
        "conflict_count": len(migration.conflicts),
        "active_process_count": len(processes),
        "active_processes": processes,
        "conflicts": migration.conflicts,
    }
    if args.verbose:
        report["moves"] = [asdict(move) for move in migration.moves]
    if args.apply:
        if processes:
            report["error"] = "Kairos processes are using this workspace"
            print(json.dumps(report, indent=2, sort_keys=True))
            return 2
        if migration.conflicts:
            report["error"] = "destination conflicts must be resolved first"
            print(json.dumps(report, indent=2, sort_keys=True))
            return 3
        report["journal"] = str(migration.apply())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
