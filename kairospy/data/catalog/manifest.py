from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping
import tomllib


DEFAULT_DATA_MANIFEST = "kairos.data.toml"


class DataManifestError(ValueError):
    """Raised when a Data manifest cannot be applied."""


@dataclass(frozen=True, slots=True)
class DataManifestDataset:
    name: str
    kind: str
    source: str
    dataset: str | None
    time: str | None = None
    account: str | None = None
    instruments: tuple[str, ...] = ()
    channel: str | None = None
    market: str | None = None
    levels: int | None = None
    interval: str | None = None
    start: str | None = None
    end: str | None = None
    target_use: str | None = None
    freshness_seconds: float | None = None
    protocol: str | None = None

    def to_args(self, root: str | Path) -> SimpleNamespace:
        source_path = Path(self.source)
        source_value: Path | str = (
            source_path if source_path.exists() or self.kind in {"file", "historical"} else self.source
        )
        custom_dataset_allowed = self.kind in {"file", "historical"} or (
            self.kind == "live" and source_path.exists()
        )
        dataset_name = self.dataset if custom_dataset_allowed else None
        return SimpleNamespace(
            lake_root=Path(root),
            source=source_value,
            file=source_value if self.kind == "file" else None,
            product=self.source,
            key=self.source,
            name=dataset_name,
            as_dataset=dataset_name,
            time=self.time,
            protocol=self.protocol or ("live" if self.kind == "live" else None),
            start=self.start,
            end=self.end,
            instrument=list(self.instruments),
            account=self.account,
            channel=self.channel,
            market=self.market or "spot",
            levels=self.levels,
            interval=self.interval,
            for_use=self.target_use or ("shadow" if self.kind == "live" else "workspace"),
            freshness_seconds=self.freshness_seconds or 5.0,
            provider=None,
            venue=None,
            refresh=False,
            dry_run=False,
            list_products=False,
        )

    def plan_payload(self) -> dict[str, object]:
        return {
            "operation": "plan",
            "name": self.name,
            "kind": self.kind,
            "source": self.source,
            "dataset": self.dataset,
            "target_use": self.target_use or ("shadow" if self.kind == "live" else "workspace"),
        }


class DataManifest:
    """User-facing Data manifest.

    The manifest is a control-plane layer: it turns named dataset declarations
    into the existing Data services instead of creating a separate pipeline.
    """

    def __init__(self, path: str | Path, datasets: tuple[DataManifestDataset, ...]) -> None:
        self.path = Path(path)
        self.datasets = datasets

    @classmethod
    def load(cls, path: str | Path | None = None) -> "DataManifest":
        manifest_path = Path(path or DEFAULT_DATA_MANIFEST).expanduser().resolve()
        if not manifest_path.exists():
            raise DataManifestError(f"data manifest does not exist: {manifest_path}")
        data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise DataManifestError(f"data manifest root must be a TOML table: {manifest_path}")
        raw_datasets = data.get("datasets")
        if not isinstance(raw_datasets, dict) or not raw_datasets:
            raise DataManifestError("data manifest requires at least one [datasets.<name>] table")
        datasets = tuple(
            cls._dataset_from_table(name, table, base=manifest_path.parent)
            for name, table in raw_datasets.items()
        )
        return cls(manifest_path, datasets)

    @staticmethod
    def _dataset_from_table(name: str, table: object, *, base: Path) -> DataManifestDataset:
        if not isinstance(table, dict):
            raise DataManifestError(f"datasets.{name} must be a TOML table")
        kind = _required_string(table, "kind", name)
        source = _required_string(table, "source", name)
        resolved_source = _resolve_source(source, base)
        raw_dataset = table.get("dataset") or table.get("name")
        if raw_dataset is not None and (not isinstance(raw_dataset, str) or not raw_dataset.strip()):
            raise DataManifestError(f"datasets.{name}.dataset cannot be empty")
        dataset = str(raw_dataset).strip() if isinstance(raw_dataset, str) else None
        source_path = Path(resolved_source)
        custom_dataset_allowed = kind in {"file", "historical"} or (kind == "live" and source_path.exists())
        if not custom_dataset_allowed and dataset is not None:
            raise DataManifestError(
                f"datasets.{name}.dataset is not allowed for built-in Data products; use an alias after apply"
            )
        if custom_dataset_allowed and dataset is None:
            dataset = name
        return DataManifestDataset(
            name=name,
            kind=kind,
            source=resolved_source,
            dataset=dataset,
            time=_optional_string(table, "time"),
            account=_optional_string(table, "account"),
            instruments=_string_tuple(table.get("instrument") or table.get("instruments")),
            channel=_optional_string(table, "channel"),
            market=_optional_string(table, "market"),
            levels=_optional_int(table, "levels"),
            interval=_optional_string(table, "interval"),
            start=_optional_string(table, "start"),
            end=_optional_string(table, "end"),
            target_use=_optional_string(table, "for") or _optional_string(table, "target_use"),
            freshness_seconds=_optional_float(table, "freshness_seconds"),
            protocol=_optional_string(table, "protocol"),
        )

    def apply(self, root: str | Path, *, only: str | None = None, dry_run: bool = False) -> dict[str, object]:
        from kairospy.data import HistoricalDataService

        selected = [item for item in self.datasets if only is None or item.name == only or item.dataset == only]
        if only is not None and not selected:
            raise DataManifestError(f"data manifest has no dataset named {only!r}")
        results: list[dict[str, object]] = []
        for item in selected:
            args = item.to_args(root)
            if dry_run:
                results.append(item.plan_payload())
                continue
            if item.kind in {"file", "historical"}:
                results.append(HistoricalDataService(root).add(args))
            elif item.kind in {"product", "built_in", "live"}:
                raise DataManifestError("Data Product and live manifest execution is owned by surface/integrations")
            else:
                raise DataManifestError(
                    f"datasets.{item.name}.kind must be file, historical, product, built_in, or live"
                )
        status = "ready" if all(str(item.get("status") or "ready") != "error" for item in results) else "needs_fix"
        return {
            "product": "data",
            "operation": "apply",
            "status": status,
            "manifest": str(self.path),
            "datasets": results,
            "count": len(results),
            "will_run": not dry_run,
        }

    def resolve_dataset(self, value: str) -> DataManifestDataset:
        for item in self.datasets:
            if item.name == value or item.dataset == value:
                return item
        raise DataManifestError(f"data manifest has no dataset named {value!r}")


def _required_string(table: Mapping[str, object], key: str, dataset_name: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DataManifestError(f"datasets.{dataset_name}.{key} is required")
    return value.strip()


def _optional_string(table: Mapping[str, object], key: str) -> str | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise DataManifestError(f"{key} must be a string")
    return value.strip() or None


def _optional_int(table: Mapping[str, object], key: str) -> int | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        raise DataManifestError(f"{key} must be an integer")
    return value


def _optional_float(table: Mapping[str, object], key: str) -> float | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise DataManifestError(f"{key} must be a number")
    return float(value)


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise DataManifestError("instrument/instruments must be a string or string list")


def _resolve_source(source: str, base: Path) -> str:
    if source.startswith(("./", "../")):
        return str((base / source).resolve())
    candidate = base / source
    if candidate.exists():
        return str(candidate.resolve())
    return source
