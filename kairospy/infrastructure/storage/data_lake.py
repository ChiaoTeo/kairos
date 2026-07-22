from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Iterable


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def utc_midnight(day: date) -> str:
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def write_daily_dataset(
    root: Path,
    rows: list[dict[str, object]],
    *,
    dataset_id: str,
    schema: dict[str, object],
    lineage: dict[str, object],
    period_start_field: str = "period_start",
    capabilities: dict[str, object] | None = None,
) -> dict[str, object]:
    """Write a canonical/feature daily dataset with monthly event-time partitions."""
    if not rows:
        raise ValueError("dataset cannot be empty")
    root.mkdir(parents=True, exist_ok=True)
    partitions: dict[tuple[int, int], list[dict[str, object]]] = {}
    observed: set[date] = set()
    for row in rows:
        value = str(row[period_start_field]).replace("Z", "+00:00")
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            raise ValueError(f"{period_start_field} must be UTC: {value}")
        observed.add(timestamp.date())
        partitions.setdefault((timestamp.year, timestamp.month), []).append(row)

    files = []
    for (year, month), values in sorted(partitions.items()):
        path = _write_partition(root / f"event_year={year:04d}" / f"event_month={month:02d}" / "part-00000", values)
        content = path.read_bytes()
        files.append({"path": path.relative_to(root).as_posix(), "rows": len(values), "bytes": len(content), "sha256": sha256_bytes(content)})

    first, last = min(observed), max(observed)
    expected = {first + timedelta(days=i) for i in range((last - first).days + 1)}
    missing = sorted(expected - observed)
    coverage = {
        "dataset_id": dataset_id,
        "time_basis": period_start_field,
        "timezone": "UTC",
        "interval": "P1D",
        "calendar": "24x7",
        "boundary": "[start,end)",
        "coverage": {
            "start": utc_midnight(first),
            "end": utc_midnight(last + timedelta(days=1)),
            "expected_periods": len(expected),
            "observed_periods": len(observed),
            "coverage_ratio": len(observed) / len(expected),
            "latest_complete_period_end": utc_midnight(last + timedelta(days=1)),
        },
        "missing_ranges": _ranges(missing),
        "duplicate_periods": len(rows) - len(observed),
        "incomplete_partitions": [],
    }
    manifest = {
        "manifest_version": 1,
        "dataset_id": dataset_id,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "schema_id": schema["schema_id"],
        "partitioning": ["event_year", "event_month"],
        "files": files,
        "rows": len(rows),
        "dataset_sha256": sha256_bytes("".join(item["sha256"] for item in files).encode()),
    }
    write_json(root / "schema.json", schema)
    write_json(root / "lineage.json", lineage)
    write_json(root / "coverage.json", coverage)
    write_json(root / "manifest.json", manifest)
    write_json(root / "capabilities.json", capabilities or _default_capabilities(dataset_id))
    _write_quality(root, dataset_id, len(rows), {
        "duplicate_primary_keys": _duplicate_primary_keys(rows, schema), "missing_ranges": len(missing),
    })
    return manifest


def write_intraday_dataset(root: Path, rows: list[dict[str, object]], *, dataset_id: str,
                           schema: dict[str, object], lineage: dict[str, object], interval: timedelta,
                           capabilities: dict[str, object] | None = None) -> dict[str, object]:
    """Write rows with many instruments per snapshot and report snapshot-time coverage."""
    if not rows:
        raise ValueError("dataset cannot be empty")
    root.mkdir(parents=True, exist_ok=True)
    partitions, snapshot_times = {}, set()
    for row in rows:
        timestamp = datetime.fromisoformat(str(row["period_start"]).replace("Z", "+00:00"))
        if timestamp.utcoffset() != timedelta(0):
            raise ValueError("period_start must be UTC")
        snapshot_times.add(timestamp)
        partitions.setdefault((timestamp.year, timestamp.month), []).append(row)
    files = []
    for (year, month), values in sorted(partitions.items()):
        values.sort(key=lambda row: (str(row.get("period_start", "")), str(row.get("instrument_id", ""))))
        path = _write_partition(root / f"event_year={year:04d}" / f"event_month={month:02d}" / "part-00000", values)
        content = path.read_bytes()
        files.append({"path": path.relative_to(root).as_posix(), "rows": len(values), "bytes": len(content), "sha256": sha256_bytes(content)})
    first, last = min(snapshot_times), max(snapshot_times)
    expected, cursor = set(), first
    while cursor <= last:
        expected.add(cursor); cursor += interval
    missing = sorted(expected - snapshot_times)
    coverage = {"dataset_id": dataset_id, "time_basis": "period_start", "timezone": "UTC",
                "interval_seconds": int(interval.total_seconds()), "calendar": "24x7", "boundary": "[start,end)",
                "coverage": {"start": first.isoformat().replace("+00:00", "Z"),
                    "end": (last + interval).isoformat().replace("+00:00", "Z"), "expected_snapshots": len(expected),
                    "observed_snapshots": len(snapshot_times), "rows": len(rows), "coverage_ratio": len(snapshot_times) / len(expected),
                    "latest_complete_period_end": (last + interval).isoformat().replace("+00:00", "Z")},
                "missing_ranges": _datetime_ranges(missing, interval), "incomplete_partitions": []}
    manifest = {"manifest_version": 1, "dataset_id": dataset_id,
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "schema_id": schema["schema_id"],
                "partitioning": ["event_year", "event_month"], "files": files, "rows": len(rows),
                "dataset_sha256": sha256_bytes("".join(item["sha256"] for item in files).encode())}
    write_json(root / "schema.json", schema); write_json(root / "lineage.json", lineage)
    write_json(root / "coverage.json", coverage); write_json(root / "manifest.json", manifest)
    write_json(root / "capabilities.json", capabilities or _default_capabilities(dataset_id))
    _write_quality(root, dataset_id, len(rows), {"duplicate_primary_keys": _duplicate_primary_keys(rows, schema),
                                                  "missing_ranges": len(missing)})
    return manifest


def write_event_dataset(root: Path, rows: list[dict[str, object]], *, dataset_id: str,
                        schema: dict[str, object], lineage: dict[str, object], event_time_field: str = "event_time",
                        capabilities: dict[str, object] | None = None) -> dict[str, object]:
    """Write irregular market events without inventing an expected sampling interval."""
    if not rows:
        raise ValueError("dataset cannot be empty")
    root.mkdir(parents=True, exist_ok=True)
    partitions, times = {}, []
    for row in rows:
        timestamp = datetime.fromisoformat(str(row[event_time_field]).replace("Z", "+00:00"))
        times.append(timestamp); partitions.setdefault((timestamp.year, timestamp.month), []).append(row)
    files = []
    for (year, month), values in sorted(partitions.items()):
        values.sort(key=lambda row: (str(row[event_time_field]), str(row.get("trade_id", ""))))
        path = _write_partition(root / f"event_year={year:04d}" / f"event_month={month:02d}" / "part-00000", values)
        content = path.read_bytes()
        files.append({"path": path.relative_to(root).as_posix(), "rows": len(values), "bytes": len(content), "sha256": sha256_bytes(content)})
    observed_days = {value.date() for value in times}; first, last = min(times), max(times)
    coverage = {"dataset_id": dataset_id, "time_basis": event_time_field, "timezone": "UTC", "sampling": "irregular_events",
                "observed_window": {"minimum_event_time": first.isoformat().replace("+00:00", "Z"),
                    "maximum_event_time": last.isoformat().replace("+00:00", "Z"), "event_count": len(rows),
                    "active_days": len(observed_days)}, "missing_ranges": [],
                "note": "Absence of trades is not treated as a missing market-data interval."}
    manifest = {"manifest_version": 1, "dataset_id": dataset_id,
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "schema_id": schema["schema_id"],
                "partitioning": ["event_year", "event_month"], "files": files, "rows": len(rows),
                "dataset_sha256": sha256_bytes("".join(item["sha256"] for item in files).encode())}
    write_json(root / "schema.json", schema); write_json(root / "lineage.json", lineage)
    write_json(root / "coverage.json", coverage); write_json(root / "manifest.json", manifest)
    write_json(root / "capabilities.json", capabilities or _default_capabilities(dataset_id))
    _write_quality(root, dataset_id, len(rows), {"duplicate_primary_keys": _duplicate_primary_keys(rows, schema)})
    return manifest


def append_snapshot_dataset(root: Path, rows: list[dict[str, object]], *, dataset_id: str,
                            schema: dict[str, object], lineage: dict[str, object],
                            capabilities: dict[str, object] | None = None) -> dict[str, object]:
    """Append an immutable point-in-time chain snapshot and refresh its manifest."""
    if not rows:
        raise ValueError("snapshot cannot be empty")
    timestamp = datetime.fromisoformat(str(rows[0]["period_start"]).replace("Z", "+00:00"))
    directory = root / f"event_year={timestamp.year:04d}" / f"event_month={timestamp.month:02d}" / f"event_day={timestamp.day:02d}"
    stem = directory / f"part-{timestamp:%H%M%S%f}"
    directory.mkdir(parents=True, exist_ok=True)
    path = stem.with_suffix(".parquet") if _has_pyarrow() else stem.with_suffix(".csv")
    if not path.exists():
        path = _write_partition(stem, rows)
    files, snapshots, total_rows = [], [], 0
    paths = sorted(root.glob("event_year=*/event_month=*/event_day=*/part-*.parquet"))
    paths.extend(sorted(root.glob("event_year=*/event_month=*/event_day=*/part-*.csv")))
    for item in paths:
        content = item.read_bytes()
        if item.suffix == ".parquet":
            import pyarrow.parquet as pq
            table = pq.read_table(item); count = table.num_rows; first = table.slice(0, 1).to_pylist()[0] if count else None
        else:
            with item.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle); first = next(reader, None); count = 1 + sum(1 for _ in reader) if first else 0
        if first: snapshots.append(first["period_start"])
        total_rows += count; files.append({"path": item.relative_to(root).as_posix(), "rows": count, "bytes": len(content), "sha256": sha256_bytes(content)})
    manifest = {"manifest_version": 1, "dataset_id": dataset_id,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "schema_id": schema["schema_id"],
        "partitioning": ["event_year", "event_month", "event_day"], "files": files, "rows": total_rows,
        "snapshot_count": len(snapshots), "dataset_sha256": sha256_bytes("".join(item["sha256"] for item in files).encode())}
    coverage = {"dataset_id": dataset_id, "sampling": "append_only_snapshots", "timezone": "UTC",
        "observed_window": {"start": min(snapshots), "end": max(snapshots), "snapshots": len(snapshots), "rows": total_rows}}
    write_json(root/"schema.json", schema); write_json(root/"lineage.json", lineage)
    write_json(root/"coverage.json", coverage); write_json(root/"manifest.json", manifest)
    write_json(root/"capabilities.json", capabilities or _default_capabilities(dataset_id))
    _write_quality(root, dataset_id, total_rows, {"duplicate_primary_keys_in_batch": _duplicate_primary_keys(rows, schema)})
    return manifest


def _default_capabilities(dataset_id: str) -> dict[str, object]:
    return {"capability_schema_version": 1, "dataset_id": dataset_id,
            "maximum_validation_level": 1,
            "note": "conservative default; register explicit capabilities before advanced validation"}


def _write_quality(root: Path, dataset_id: str, rows: int, metrics: dict[str, int]) -> None:
    checks = [{"name": "non_empty", "passed": rows > 0, "value": rows, "minimum": 1}]
    checks.extend({"name": name, "passed": value == 0, "value": value, "maximum": 0}
                  for name, value in metrics.items() if name.startswith("duplicate"))
    write_json(root / "quality.json", {
        "quality_schema_version": 1, "dataset_id": dataset_id,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "passed": all(item["passed"] for item in checks), "checks": checks, "metrics": metrics,
    })


def _duplicate_primary_keys(rows: list[dict[str, object]], schema: dict[str, object]) -> int:
    keys = tuple(str(item) for item in schema.get("primary_key", ()))
    if not keys:
        return 0
    identities = [tuple(str(row.get(key, "")) for key in keys) for row in rows]
    return len(identities) - len(set(identities))


def _has_pyarrow() -> bool:
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        return False
    return True


def _write_partition(stem: Path, rows: list[dict[str, object]]) -> Path:
    stem.parent.mkdir(parents=True, exist_ok=True)
    if _has_pyarrow():
        import pyarrow as pa
        import pyarrow.parquet as pq
        path = stem.with_suffix(".parquet")
        temporary = path.with_suffix(".parquet.tmp")
        pq.write_table(pa.Table.from_pylist(rows), temporary, compression="zstd")
        temporary.replace(path)
        return path
    path = stem.with_suffix(".csv")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    return path


def _ranges(days: Iterable[date]) -> list[dict[str, object]]:
    ordered = sorted(days)
    if not ordered:
        return []
    result, start, previous = [], ordered[0], ordered[0]
    for current in ordered[1:]:
        if current != previous + timedelta(days=1):
            result.append(_range(start, previous))
            start = current
        previous = current
    result.append(_range(start, previous))
    return result


def _range(start: date, end: date) -> dict[str, object]:
    return {"start": utc_midnight(start), "end": utc_midnight(end + timedelta(days=1)), "expected_periods": (end - start).days + 1}


def _datetime_ranges(values: list[datetime], interval: timedelta) -> list[dict[str, object]]:
    if not values:
        return []
    result, start, previous = [], values[0], values[0]
    for current in values[1:]:
        if current != previous + interval:
            result.append({"start": start.isoformat().replace("+00:00", "Z"),
                           "end": (previous + interval).isoformat().replace("+00:00", "Z")})
            start = current
        previous = current
    result.append({"start": start.isoformat().replace("+00:00", "Z"),
                   "end": (previous + interval).isoformat().replace("+00:00", "Z")})
    return result
