from __future__ import annotations

import json
from pathlib import Path

from trading.storage.codec import from_primitive, to_primitive

from .models import SurfaceSnapshot


class SurfaceRepository:
    def __init__(self, root: str | Path = "data/surfaces") -> None:
        self.root = Path(root)

    def save(self, surface: SurfaceSnapshot) -> Path:
        directory = self.root / surface.underlying_id.value.replace(":", "_")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{surface.surface_id}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(to_primitive(surface), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(target)
        return target

    def load(self, underlying_id: str, surface_id: str) -> SurfaceSnapshot:
        path = self.root / underlying_id.replace(":", "_") / f"{surface_id}.json"
        return from_primitive(json.loads(path.read_text(encoding="utf-8")), SurfaceSnapshot)

    def list(self, underlying_id: str) -> tuple[str, ...]:
        directory = self.root / underlying_id.replace(":", "_")
        return tuple(sorted(path.stem for path in directory.glob("*.json"))) if directory.exists() else ()

