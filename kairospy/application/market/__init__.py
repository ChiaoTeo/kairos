"""Workspace-owned market dataset and component read-side application."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class MarketDataApplication:
    root: Path

    @property
    def catalog_path(self) -> Path:
        return self.root / "datasets.json"

    @property
    def dataset_root(self) -> Path:
        return self.root / "datasets"

    def list(self) -> list[dict[str, Any]]:
        return list(self._read_catalog().get("datasets", []))

    def inspect(self, name: str) -> dict[str, Any]:
        for item in self.list():
            if item.get("name") == name:
                return item
        raise FileNotFoundError(f"market dataset does not exist: {name}")

    def ingest(self, name: str, source: Path) -> dict[str, Any]:
        if not name or "/" in name or "\\" in name:
            raise ValueError("dataset name must be path-safe")
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = self.dataset_root / name / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        entries = [item for item in self.list() if item.get("name") != name]
        entry = {"name": name, "path": str(destination), "size": destination.stat().st_size, "format": destination.suffix.lstrip(".")}
        entries.append(entry)
        self._write_catalog({"datasets": sorted(entries, key=lambda item: item["name"])})
        return entry

    def alias(self, name: str, alias: str) -> dict[str, Any]:
        entry = self.inspect(name)
        aliases = self._read_catalog().setdefault("aliases", {})
        aliases[alias] = name
        value = self._read_catalog()
        value["aliases"] = aliases
        self._write_catalog(value)
        return {"alias": alias, "dataset": entry}

    def prune(self, name: str) -> dict[str, Any]:
        entry = self.inspect(name)
        path = Path(entry["path"])
        path.unlink(missing_ok=True)
        self._write_catalog({"datasets": [item for item in self.list() if item.get("name") != name]})
        return {"name": name, "status": "pruned"}

    def read(self, name: str) -> str:
        path = Path(self.inspect(name)["path"])
        return path.read_text(encoding="utf-8")

    def _read_catalog(self) -> dict[str, Any]:
        if not self.catalog_path.exists():
            return {"datasets": [], "aliases": {}}
        value = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"datasets": [], "aliases": {}}

    def _write_catalog(self, value: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog_path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


from .cli import MarketCliApplication


__all__ = ["MarketCliApplication", "MarketDataApplication"]
