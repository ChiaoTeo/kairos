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

    def ingest(
        self, name: str, source: Path, *, format: str | None = None
    ) -> dict[str, Any]:
        if not name or "/" in name or "\\" in name:
            raise ValueError("dataset name must be path-safe")
        if not source.is_file():
            raise FileNotFoundError(source)
        storage_format = (format or source.suffix.lstrip(".") or "jsonl").lower()
        if storage_format not in {"jsonl", "parquet"}:
            raise ValueError("market dataset format must be jsonl or parquet")
        destination_name = source.name
        if storage_format == "parquet":
            destination_name = f"{source.stem}.parquet"
        destination = self.dataset_root / name / destination_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if storage_format == "parquet":
            _write_parquet(_read_jsonl(source), destination)
        else:
            shutil.copy2(source, destination)
        events = _read_events(destination)
        entries = [item for item in self.list() if item.get("name") != name]
        entry = {
            "name": name,
            "path": str(destination),
            "size": destination.stat().st_size,
            "format": storage_format,
            "event_count": len(events),
            "start_time_unix_nanos": _event_time(events[0]) if events else None,
            "end_time_unix_nanos": _event_time(events[-1]) if events else None,
        }
        manifest_path = destination.with_suffix(".manifest.json")
        manifest_path.write_text(
            json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        entry["manifest_path"] = str(manifest_path)
        entries.append(entry)
        self._write_catalog(
            {"datasets": sorted(entries, key=lambda item: item["name"])}
        )
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
        self._write_catalog(
            {"datasets": [item for item in self.list() if item.get("name") != name]}
        )
        return {"name": name, "status": "pruned"}

    def read(self, name: str) -> str:
        path = Path(self.inspect(name)["path"])
        if path.suffix.lower() == ".parquet":
            return "\n".join(
                json.dumps(event, sort_keys=True) for event in _read_events(path)
            )
        return path.read_text(encoding="utf-8")

    def read_events(self, name: str) -> list[dict[str, Any]]:
        """Read normalized observations from either supported dataset format."""
        return _read_events(Path(self.inspect(name)["path"]))

    def _read_catalog(self) -> dict[str, Any]:
        if not self.catalog_path.exists():
            return {"datasets": [], "aliases": {}}
        value = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"datasets": [], "aliases": {}}

    def _write_catalog(self, value: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog_path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


from .cli import MarketCliApplication  # noqa: E402


def materialize_replay_file(
    source: Path, target: Path, *, catalog_root: Path | None = None
) -> Path:
    """Materialize a columnar dataset for the current Rust replay reader."""
    source = source.resolve()
    if not source.is_file() and catalog_root is not None:
        catalog = MarketDataApplication(catalog_root)
        name = source.name.removeprefix("dataset:")
        try:
            source = Path(catalog.inspect(name)["path"]).resolve()
        except FileNotFoundError:
            pass
    if source.name.endswith(".manifest.json"):
        manifest = json.loads(source.read_text(encoding="utf-8"))
        source_value = Path(str(manifest["path"]))
        source = (
            source_value if source_value.is_absolute() else source.parent / source_value
        )
    if source.suffix.lower() != ".parquet":
        return source
    events = _read_events(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events),
        encoding="utf-8",
    )
    return target


__all__ = ["MarketCliApplication", "MarketDataApplication", "materialize_replay_file"]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("market dataset event must be a JSON object")
            events.append(value)
    return events


def _write_parquet(events: list[dict[str, Any]], path: Path) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "Parquet support requires the 'data' extra: uv sync --extra data"
        ) from error

    rows: list[dict[str, Any]] = []
    for event in events:
        kind, payload = next(iter(event.items()), ("", {}))
        if not isinstance(payload, dict):
            raise ValueError("market observation payload must be an object")
        rows.append(
            {
                "kind": kind,
                "market_id": payload.get("market_id"),
                "instrument_id": payload.get("instrument_id"),
                "source_id": payload.get("source_id"),
                "observed_at_unix_nanos": _event_time(event),
                "payload_json": json.dumps(payload, separators=(",", ":")),
            }
        )
    table = pa.Table.from_pylist(
        rows,
        schema=pa.schema(
            [
                ("kind", pa.string()),
                ("market_id", pa.string()),
                ("instrument_id", pa.string()),
                ("source_id", pa.string()),
                ("observed_at_unix_nanos", pa.uint64()),
                ("payload_json", pa.string()),
            ]
        ),
    )
    pq.write_table(table, path, compression="zstd", version="2.6")


def _read_events(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() != ".parquet":
        return _read_jsonl(path)
    try:
        import pyarrow.parquet as pq
    except ImportError as error:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "Parquet support requires the 'data' extra: uv sync --extra data"
        ) from error
    rows = pq.read_table(path).to_pylist()
    events: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        events.append({row["kind"]: payload})
    return events


def _event_time(event: dict[str, Any]) -> int | None:
    payload = next(iter(event.values()), {})
    if not isinstance(payload, dict):
        return None
    value = payload.get("observed_at_unix_nanos")
    return int(value) if value is not None else None
