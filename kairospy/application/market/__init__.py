"""Workspace-owned market dataset and component read-side application."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from .application import (
    MarketApplication,
    Subscription,
    SubscriptionReleaseResult,
    SubscriptionStatus,
)
from .events import (
    BarEvent,
    EventStreamGap,
    GreeksEvent,
    MarketEvent,
    QuoteEvent,
    TradeEvent,
)
from .models import (
    AggressorSide,
    Bar,
    MarketSnapshot,
    ObservationScope,
    ObservationScopeKind,
    OptionGreeks,
    Quote,
    Trade,
)
from .requests import SubscriptionRequest


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
        self,
        name: str,
        source: Path,
        *,
        format: str | None = None,
        metadata: Mapping[str, Any] | None = None,
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
        _validate_events(events)
        times = [_event_time(event) for event in events]
        valid_times = [time for time in times if time is not None]
        entries = [item for item in self.list() if item.get("name") != name]
        entry = {
            "name": name,
            "path": str(destination),
            "size": destination.stat().st_size,
            "format": storage_format,
            "event_count": len(events),
            "start_time_unix_nanos": min(valid_times) if valid_times else None,
            "end_time_unix_nanos": max(valid_times) if valid_times else None,
            "observation_types": sorted(
                {next(iter(event)) for event in events if event}
            ),
            "source_ids": sorted(
                {
                    str(payload.get("source_id"))
                    for event in events
                    for payload in event.values()
                    if isinstance(payload, dict) and payload.get("source_id")
                }
            ),
        }
        if metadata:
            entry.update(
                {key: value for key, value in metadata.items() if value is not None}
            )
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
        events = _read_events(Path(self.inspect(name)["path"]))
        _validate_events(events)
        return events

    def validate(self, name: str) -> dict[str, Any]:
        """Validate a registered dataset and its manifest before replay."""
        entry = self.inspect(name)
        events = self.read_events(name)
        expected = entry.get("event_count")
        if expected is not None and expected != len(events):
            raise ValueError(
                f"dataset event count mismatch: manifest={expected}, file={len(events)}"
            )
        return {
            "name": name,
            "path": entry["path"],
            "event_count": len(events),
            "start_time_unix_nanos": min(
                time
                for time in (_event_time(event) for event in events)
                if time is not None
            )
            if events
            else None,
            "end_time_unix_nanos": max(
                time
                for time in (_event_time(event) for event in events)
                if time is not None
            )
            if events
            else None,
        }

    def derive_synthetic_quotes(
        self, source_name: str, target_name: str, *, spread_bps: str | Decimal = "0"
    ) -> dict[str, Any]:
        """Derive explicit synthetic Quotes from normalized Bar data.

        This is a test/research adapter for providers whose historical API
        exposes OHLCV but not historical bid/ask.  The result is deliberately
        marked with ``derivation = synthetic_quote`` and must not be presented
        as historical order-book data.
        """
        spread = Decimal(str(spread_bps))
        if spread < 0:
            raise ValueError("synthetic quote spread_bps cannot be negative")
        events = self.read_events(source_name)
        derived: list[dict[str, Any]] = []
        half_spread = spread / Decimal("20000")
        for index, event in enumerate(events):
            if set(event) != {"Bar"}:
                raise ValueError(
                    f"synthetic quote derivation requires Bar data; event {index} is not Bar"
                )
            bar = event["Bar"]
            close = _decimal_value(bar["close"])
            bid = close * (Decimal("1") - half_spread)
            ask = close * (Decimal("1") + half_spread)
            derived.append(
                {
                    "Quote": {
                        "scope": dict(bar["scope"]),
                        "instrument_id": bar["instrument_id"],
                        "bid_price": _decimal_text(bid),
                        "bid_quantity": bar.get("volume"),
                        "ask_price": _decimal_text(ask),
                        "ask_quantity": bar.get("volume"),
                        "observed_at_unix_nanos": bar["observed_at_unix_nanos"],
                        "source_id": f"{bar['source_id']}:synthetic-quote",
                        "derivation": "synthetic_quote",
                    }
                }
            )
        raw = self.root / "derived" / f"{target_name}.jsonl"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_text(
            "".join(
                json.dumps(event, separators=(",", ":")) + "\n" for event in derived
            ),
            encoding="utf-8",
        )
        return self.ingest(target_name, raw, format="jsonl")

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
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() != ".parquet":
        manifest_path = source.with_suffix(".manifest.json")
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            events = _read_events(source)
            _validate_manifest(manifest, events)
        if source != target.resolve():
            shutil.copyfile(source, target)
        return target
    events = _read_events(source)
    manifest_path = source.with_suffix(".manifest.json")
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _validate_manifest(manifest, events)
    target.write_text(
        "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events),
        encoding="utf-8",
    )
    return target


def validate_replay_window(
    path: Path,
    *,
    start_time_unix_nanos: int | None = None,
    end_time_unix_nanos: int | None = None,
) -> dict[str, int | None]:
    """Validate replay identity and prove that the launch window is covered."""
    events = _read_events(path)
    _validate_events(events)
    times = [
        time for time in (_event_time(event) for event in events) if time is not None
    ]
    if not times:
        raise ValueError("market replay dataset has no event times")
    first = min(times)
    last = max(times)
    if start_time_unix_nanos is not None and first < start_time_unix_nanos:
        raise ValueError(
            f"market replay dataset starts before launch window: first={first}, start={start_time_unix_nanos}"
        )
    if end_time_unix_nanos is not None and last > end_time_unix_nanos:
        raise ValueError(
            f"market replay dataset ends after launch window: last={last}, end={end_time_unix_nanos}"
        )
    return {
        "event_count": len(events),
        "first_time_unix_nanos": first,
        "last_time_unix_nanos": last,
    }


def read_replay_events(
    path: str | Path, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Read and validate market observations from a replay artifact."""
    if limit is not None and limit < 0:
        raise ValueError("limit must not be negative")
    events = _read_events(Path(path))
    _validate_events(events)
    if limit is not None:
        return events[-limit:] if limit else []
    return events


__all__ = [
    "AggressorSide",
    "Bar",
    "BarEvent",
    "EventStreamGap",
    "GreeksEvent",
    "MarketApplication",
    "MarketEvent",
    "MarketAnalyticalApplication",
    "MarketCliApplication",
    "MarketDataApplication",
    "OptionGreeksProjectionRequest",
    "OptionGreeksProjectionResult",
    "OptionGreeks",
    "Quote",
    "QuoteEvent",
    "Subscription",
    "SubscriptionReleaseResult",
    "SubscriptionRequest",
    "SubscriptionStatus",
    "Trade",
    "TradeEvent",
    "materialize_replay_file",
    "read_replay_events",
    "validate_replay_window",
]

from .analytics import (  # noqa: E402
    MarketAnalyticalApplication,
    OptionGreeksProjectionRequest,
    OptionGreeksProjectionResult,
)


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
                "scope_key": _scope_key(payload.get("scope")),
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
                ("scope_key", pa.string()),
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


def _validate_events(events: list[dict[str, Any]]) -> None:
    allowed = {"Quote", "Bar", "Trade", "TradeBar", "QuoteBar", "OrderBook", "Greeks"}
    for index, event in enumerate(events):
        if not isinstance(event, dict) or len(event) != 1:
            raise ValueError(
                f"market dataset event {index} must contain one observation"
            )
        kind, payload = next(iter(event.items()))
        if kind not in allowed or not isinstance(payload, dict):
            raise ValueError(
                f"market dataset event {index} has invalid observation type"
            )
        if not isinstance(payload.get("observed_at_unix_nanos"), int):
            raise ValueError(
                f"market dataset event {index} is missing integer event time"
            )
        if payload["observed_at_unix_nanos"] < 0:
            raise ValueError(f"market dataset event {index} has negative event time")
        for field in ("instrument_id", "source_id"):
            if not isinstance(payload.get(field), str) or not payload[field].strip():
                raise ValueError(f"market dataset event {index} is missing {field}")
        scope = payload.get("scope")
        if not isinstance(scope, dict):
            raise ValueError(f"market dataset event {index} is missing scope")
        scope_kind = scope.get("kind")
        if scope_kind == "market":
            if (
                not isinstance(scope.get("market_id"), str)
                or not scope["market_id"].strip()
            ):
                raise ValueError(
                    f"market dataset event {index} has invalid market scope"
                )
        elif scope_kind == "consolidated":
            if scope.get("instrument_id") != payload["instrument_id"]:
                raise ValueError(
                    f"market dataset event {index} has mismatched consolidated scope"
                )
        else:
            raise ValueError(f"market dataset event {index} has invalid scope kind")
        if kind in {"Bar", "TradeBar", "QuoteBar"} and not payload.get("timeframe"):
            # Composite bar observations carry their timeframe in the nested
            # bar payload; plain bars must declare it directly.
            nested = payload.get("bar")
            if not isinstance(nested, dict) or not nested.get("timeframe"):
                raise ValueError(f"market dataset event {index} is missing timeframe")


def _scope_key(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    if value.get("kind") == "market":
        market_id = value.get("market_id")
        return market_id if isinstance(market_id, str) else None
    if value.get("kind") == "consolidated":
        instrument_id = value.get("instrument_id")
        if not isinstance(instrument_id, str):
            return None
        network_id = value.get("network_id")
        return f"consolidated:{instrument_id}:{network_id or '*'}"
    return None


def _validate_manifest(manifest: dict[str, Any], events: list[dict[str, Any]]) -> None:
    expected = manifest.get("event_count")
    if expected is not None and expected != len(events):
        raise ValueError(
            f"market dataset event count mismatch: manifest={expected}, file={len(events)}"
        )
    _validate_events(events)


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _decimal_value(value: object) -> Decimal:
    return Decimal(str(value))


def _event_time(event: dict[str, Any]) -> int | None:
    payload = next(iter(event.values()), {})
    if not isinstance(payload, dict):
        return None
    value = payload.get("observed_at_unix_nanos")
    return int(value) if value is not None else None
