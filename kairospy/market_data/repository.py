from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable

from kairospy.trading.identity import InstrumentId
from kairospy.storage.data_lake import write_json

from .events import MarketEventEnvelope, MarketEventType
from .quality_gate import EventQualityReport, require_publishable, validate_events


class ParquetMarketEventRepository:
    def __init__(self, root: str | Path = "data/canonical/market") -> None:
        self.root = Path(root)

    def write_batch(self, dataset_id: str, events: Iterable[MarketEventEnvelope], *, lineage: dict[str, object],
                    reconciliation: dict[str, object] | None = None, known_exchange_codes: set[str] | None = None,
                    known_condition_codes: set[str] | None = None) -> dict[str, object]:
        pa, pq, _ = _pyarrow()
        values = tuple(sorted(events, key=lambda item: item.event_key))
        if not values:
            raise ValueError("event batch cannot be empty")
        report = validate_events(values, known_exchange_codes=known_exchange_codes, known_condition_codes=known_condition_codes)
        require_publishable(report)
        batch_hash = sha256(json.dumps([_row(item) for item in values], default=_json_default, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        dataset_root = self.root / f"dataset={dataset_id}"
        existing_manifest_path = dataset_root / "manifest.json"
        if existing_manifest_path.exists():
            existing_manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
            if existing_manifest.get("batch_sha256") == batch_hash:
                return existing_manifest
            raise ValueError(f"dataset ID {dataset_id!r} already refers to different immutable content; use a new version")
        files = []
        groups: dict[tuple[str, str, str], list[MarketEventEnvelope]] = {}
        for event in values:
            groups.setdefault((event.event_time.date().isoformat(), event.record_type.value, event.source), []).append(event)
        for (event_date, event_type, provider), group in sorted(groups.items()):
            directory = dataset_root / f"event_date={event_date}" / f"event_type={event_type}" / f"provider={provider}"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"part-{batch_hash[:24]}.parquet"
            if not path.exists():
                temporary = path.with_suffix(".parquet.tmp")
                table = pa.Table.from_pylist([_row(item) for item in group], schema=_schema(pa))
                pq.write_table(table, temporary, compression="zstd")
                temporary.replace(path)
            content = path.read_bytes()
            files.append({"path": path.relative_to(dataset_root).as_posix(), "rows": len(group), "bytes": len(content), "sha256": sha256(content).hexdigest()})
        manifest = {
            "manifest_version": 2, "dataset_id": dataset_id, "format": "parquet", "compression": "zstd",
            "generated_at": datetime.now(timezone.utc).isoformat(), "rows": len(values), "files": files, "batch_sha256": batch_hash,
            "dataset_sha256": sha256("".join(item["sha256"] for item in files).encode()).hexdigest(),
            "partitioning": ["event_date", "event_type", "provider"],
        }
        quality = {"event_count": report.event_count, "publishable": report.publishable,
                   "issues": [{"code": item.code, "severity": item.severity.value, "message": item.message} for item in report.issues],
                   "reconciliation": reconciliation or {"canonical_event_records": len(values)}}
        schema = {"schema_id": "market.event_envelope.v1", "schema_version": 1,
                  "primary_key": ["source", "source_namespace", "source_instrument_id", "record_type", "event_time", "source_order"],
                  "time_semantics": {"window": "[start,end)", "replay_clock": "available_time"}}
        coverage = {"dataset_id": dataset_id, "timezone": "UTC", "calendar": "US_SECURITIES", "sampling": "irregular_events", "boundary": "[start,end)",
                    "requested_window": lineage.get("request_window"),
                    "observed_window": {"minimum_event_time": min(item.event_time for item in values).isoformat(),
                                        "maximum_event_time": max(item.event_time for item in values).isoformat(), "event_count": len(values)},
                    "missing_ranges": [], "note": "Absence of irregular trades or quotes is not automatically classified as a gap."}
        write_json(dataset_root / "schema.json", schema); write_json(dataset_root / "lineage.json", lineage)
        write_json(dataset_root / "coverage.json", coverage); write_json(dataset_root / "quality.json", quality)
        write_json(dataset_root / "manifest.json", manifest)
        return manifest

    def compact(self, dataset_id: str) -> dict[str, object]:
        _, pq, ds = _pyarrow()
        dataset_root = self.root / f"dataset={dataset_id}"
        if not dataset_root.exists():
            raise FileNotFoundError(dataset_id)
        compacted = []
        for directory in sorted(path for path in dataset_root.glob("event_date=*/event_type=*/provider=*") if path.is_dir()):
            paths = sorted(directory.glob("part-*.parquet"))
            if len(paths) <= 1:
                continue
            table = ds.dataset(paths, format="parquet").to_table()
            digest = sha256(table.to_pydict().__repr__().encode()).hexdigest()
            target = directory / f"compact-{digest[:24]}.parquet"
            temporary = target.with_suffix(".parquet.tmp")
            pq.write_table(table, temporary, compression="zstd"); temporary.replace(target)
            for path in paths:
                path.unlink()
            compacted.append({"partition": directory.relative_to(dataset_root).as_posix(), "input_files": len(paths), "output": target.name})
        manifest_path = dataset_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files, rows = [], 0
        for path in sorted(dataset_root.glob("event_date=*/event_type=*/provider=*/*.parquet")):
            content = path.read_bytes(); count = pq.read_metadata(path).num_rows; rows += count
            files.append({"path": path.relative_to(dataset_root).as_posix(), "rows": count, "bytes": len(content), "sha256": sha256(content).hexdigest()})
        manifest.update({"generated_at": datetime.now(timezone.utc).isoformat(), "rows": rows, "files": files,
                         "dataset_sha256": sha256("".join(item["sha256"] for item in files).encode()).hexdigest()})
        write_json(manifest_path, manifest)
        return {"dataset_id": dataset_id, "compacted_partitions": compacted, "manifest": manifest}

    def scan(self, dataset_id: str, start: datetime, end: datetime, *, instruments: Iterable[InstrumentId] | None = None,
             event_types: Iterable[MarketEventType] | None = None, view: str = "raw-as-received"):
        _, _, ds = _pyarrow()
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("scan requires timezone-aware [start,end) with end after start")
        root = self.root / f"dataset={dataset_id}"
        dataset = ds.dataset(root, format="parquet", partitioning="hive", exclude_invalid_files=True)
        base_expression = (ds.field("available_time") >= start) & (ds.field("available_time") < end)
        ids = [str(item) for item in instruments or ()]
        types = [item.value for item in event_types or ()]
        if ids:
            base_expression &= ds.field("instrument_id").isin(ids)
        if types:
            base_expression &= ds.field("record_type").isin(types)
        if view not in {"raw-as-received", "corrected-final"}:
            raise ValueError("view must be raw-as-received or corrected-final")
        corrected = []
        day = start.date()
        while day <= (end - timedelta(microseconds=1)).date():
            expression = base_expression & (ds.field("event_date") == day.isoformat())
            events = [_event(row) for row in dataset.to_table(filter=expression).to_pylist()]
            ordered = sorted(events, key=lambda item: item.event_key)
            if view == "raw-as-received":
                yield from ordered
            else:
                corrected.extend(ordered)
            day += timedelta(days=1)
        if view == "corrected-final":
            yield from _corrected_final(corrected)

    def metadata(self, dataset_id: str) -> dict[str, object]:
        root = self.root / f"dataset={dataset_id}"
        if not root.exists():
            raise FileNotFoundError(dataset_id)
        return {name: json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
                for name in ("schema", "lineage", "coverage", "quality", "manifest")}

    def distinct_instruments(self, dataset_id: str, start: datetime, end: datetime, *, event_types: Iterable[MarketEventType]) -> tuple[InstrumentId, ...]:
        _, _, ds = _pyarrow()
        root = self.root / f"dataset={dataset_id}"
        dataset = ds.dataset(root, format="parquet", partitioning="hive", exclude_invalid_files=True)
        types = [item.value for item in event_types]
        expression = ((ds.field("available_time") >= start) & (ds.field("available_time") < end)
                      & ds.field("record_type").isin(types))
        values = {str(value) for value in dataset.to_table(columns=["instrument_id"], filter=expression).column("instrument_id").to_pylist()}
        return tuple(InstrumentId(value) for value in sorted(values))


def _row(event: MarketEventEnvelope) -> dict[str, object]:
    payload = event.payload
    return {"instrument_id": str(event.instrument_id), "event_time": event.event_time, "receive_time": event.receive_time,
            "available_time": event.available_time, "ingested_at": event.ingested_at, "source": event.source,
            "source_namespace": event.source_namespace, "source_instrument_id": event.source_instrument_id,
            "record_type": event.record_type.value, "source_order": event.source_order, "publisher_id": event.publisher_id,
            "flags_json": json.dumps(event.flags), "payload_json": json.dumps(payload, default=_json_default, sort_keys=True, separators=(",", ":")),
            # Common query fields are physical columns so Arrow/DuckDB can prune and filter them.
            "bid": _decimal_value(payload.get("bid")), "ask": _decimal_value(payload.get("ask")),
            "bid_size": _decimal_value(payload.get("bid_size")), "ask_size": _decimal_value(payload.get("ask_size")),
            "price": _decimal_value(payload.get("price")), "size": _decimal_value(payload.get("size")),
            "open": _decimal_value(payload.get("open")), "high": _decimal_value(payload.get("high")),
            "low": _decimal_value(payload.get("low")), "close": _decimal_value(payload.get("close")),
            "volume": _decimal_value(payload.get("volume")), "vwap": _decimal_value(payload.get("vwap")),
            "period_start": payload.get("period_start"), "period_end": payload.get("period_end"),
            "trade_id": str(payload["trade_id"]) if payload.get("trade_id") is not None else None,
            "sequence_number": int(payload["sequence_number"]) if payload.get("sequence_number") is not None else None,
            "conditions_json": json.dumps(payload.get("conditions", ())),
            "exchange": str(payload.get("exchange") or "") or None,
            "bid_exchange": str(payload.get("bid_exchange") or "") or None,
            "ask_exchange": str(payload.get("ask_exchange") or "") or None}


def _event(row: dict[str, object]) -> MarketEventEnvelope:
    return MarketEventEnvelope(InstrumentId(str(row["instrument_id"])), row["event_time"], row["available_time"], row["ingested_at"],
        str(row["source"]), str(row["source_namespace"]), str(row["source_instrument_id"]), MarketEventType(str(row["record_type"])),
        int(row["source_order"]), json.loads(str(row["payload_json"])), row.get("receive_time"),
        str(row["publisher_id"]) if row.get("publisher_id") else None, tuple(json.loads(str(row["flags_json"]))))


def _schema(pa):
    return pa.schema([("instrument_id", pa.string()), ("event_time", pa.timestamp("ns", tz="UTC")),
        ("receive_time", pa.timestamp("ns", tz="UTC")), ("available_time", pa.timestamp("ns", tz="UTC")),
        ("ingested_at", pa.timestamp("ns", tz="UTC")), ("source", pa.string()), ("source_namespace", pa.string()),
        ("source_instrument_id", pa.string()), ("record_type", pa.string()), ("source_order", pa.int64()),
        ("publisher_id", pa.string()), ("flags_json", pa.string()), ("payload_json", pa.string()),
        ("bid", pa.decimal128(38, 12)), ("ask", pa.decimal128(38, 12)),
        ("bid_size", pa.decimal128(38, 12)), ("ask_size", pa.decimal128(38, 12)),
        ("price", pa.decimal128(38, 12)), ("size", pa.decimal128(38, 12)),
        ("open", pa.decimal128(38, 12)), ("high", pa.decimal128(38, 12)),
        ("low", pa.decimal128(38, 12)), ("close", pa.decimal128(38, 12)),
        ("volume", pa.decimal128(38, 12)), ("vwap", pa.decimal128(38, 12)),
        ("period_start", pa.timestamp("ns", tz="UTC")), ("period_end", pa.timestamp("ns", tz="UTC")),
        ("trade_id", pa.string()), ("sequence_number", pa.int64()), ("conditions_json", pa.string()),
        ("exchange", pa.string()), ("bid_exchange", pa.string()), ("ask_exchange", pa.string())])


def _decimal_value(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _json_default(value: object):
    if isinstance(value, (datetime, Decimal)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _corrected_final(events: list[MarketEventEnvelope]):
    result: list[MarketEventEnvelope] = []
    trades: dict[str, int] = {}
    for event in events:
        if event.record_type is not MarketEventType.TRADE:
            result.append(event); continue
        trade_id = event.payload.get("trade_id")
        if trade_id is None:
            result.append(event); continue
        identity = str(event.payload.get("original_id") or trade_id)
        previous_index = trades.get(identity)
        if "cancel" in event.flags:
            if previous_index is not None:
                result[previous_index] = None  # type: ignore[assignment]
            continue
        if previous_index is not None and "correction" in event.flags:
            result[previous_index] = None  # type: ignore[assignment]
        trades[identity] = len(result)
        result.append(event)
    return (item for item in result if item is not None)


def _pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.dataset as ds
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("Parquet market data requires the 'data' optional dependency") from error
    return pa, pq, ds
