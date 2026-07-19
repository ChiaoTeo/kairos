from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import shutil
from uuid import uuid4

from trading.storage.data_lake import sha256_bytes, write_json

from .models import DatasetRelease, QualityLevel
from .products import ManagedDataset
from .publishing import publish_release, release_path
from .quality import DatasetQualityService


@dataclass(frozen=True, slots=True)
class IntradayColumnarRelease:
    release: DatasetRelease
    manifest: dict[str, object]


def publish_intraday_staging_parquet(
    lake: str | Path,
    product: ManagedDataset,
    staging_root: str | Path,
    *,
    schema: dict[str, object],
    lineage: dict[str, object],
    interval: timedelta,
    capabilities: dict[str, object],
    provider: str,
    venue: str | None,
    transform_id: str,
    transform_version: str,
    quality_level: QualityLevel,
    primary_key: tuple[str, ...],
    order_by: tuple[str, ...],
) -> IntradayColumnarRelease:
    """Publish a partitioned intraday release from staging Parquet without materializing all rows."""
    try:
        import duckdb
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("columnar dataset publishing requires the query optional dependency") from error

    root = Path(lake)
    staging = Path(staging_root)
    staged_files = sorted(staging.glob("**/*.parquet"))
    if not staged_files:
        raise ValueError(f"no staging parquet files under {staging}")

    build_id = f"columnar-{uuid4().hex}"
    output = root / product.relative_path / f"release={build_id}.tmp"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    escaped_files = ",".join(_sql_string(path) for path in staged_files)
    source_sql = f"[{escaped_files}]"
    temp_directory = root / "tmp" / "duckdb"
    temp_directory.mkdir(parents=True, exist_ok=True)
    columns = _columns_sql(schema)
    partition_clause = ", ".join(_quote(name) for name in primary_key)
    order_clause = ", ".join(_quote(name) for name in order_by)

    connection = duckdb.connect()
    try:
        connection.execute(f"PRAGMA temp_directory={_sql_string(temp_directory)}")
        connection.execute("PRAGMA threads=4")
        connection.execute(f"CREATE TEMP VIEW staging_rows AS SELECT * FROM read_parquet({source_sql}, union_by_name=true)")
        partitions = connection.execute(
            "SELECT DISTINCT year(period_start), month(period_start) FROM staging_rows ORDER BY 1, 2"
        ).fetchall()
        for year, month in partitions:
            partition = output / f"event_year={int(year):04d}" / f"event_month={int(month):02d}"
            partition.mkdir(parents=True, exist_ok=True)
            target = _sql_string(partition / "part-00000.parquet")
            connection.execute(f"""
                COPY (
                    SELECT {columns}
                    FROM (
                        SELECT {columns},
                               row_number() OVER (
                                   PARTITION BY {partition_clause}
                                   ORDER BY {order_clause}
                               ) AS __row_number
                        FROM staging_rows
                        WHERE year(period_start) = {int(year)}
                          AND month(period_start) = {int(month)}
                    )
                    WHERE __row_number = 1
                    ORDER BY {order_clause}
                ) TO {target} (
                    FORMAT PARQUET,
                    COMPRESSION ZSTD,
                    ROW_GROUP_SIZE 100000,
                    PRESERVE_ORDER true
                )
            """)
        snapshot_values = [
            _utc(value) for (value,) in connection.execute(
                "SELECT DISTINCT period_start FROM staging_rows ORDER BY period_start"
            ).fetchall()
        ]
    finally:
        connection.close()

    files: list[dict[str, object]] = []
    total_rows = 0
    for path in sorted(output.glob("**/*.parquet")):
        content = path.read_bytes()
        rows = pq.read_metadata(path).num_rows
        total_rows += rows
        files.append({
            "path": path.relative_to(output).as_posix(),
            "rows": rows,
            "bytes": len(content),
            "sha256": sha256_bytes(content),
        })
    if total_rows <= 0 or not snapshot_values:
        shutil.rmtree(output, ignore_errors=True)
        raise ValueError("columnar publisher produced an empty release")

    dataset_sha256 = sha256_bytes("".join(str(item["sha256"]) for item in files).encode())
    release_id = _content_hash_release_id(product, dataset_sha256)
    final = root / release_path(product, release_id)
    if final.exists():
        shutil.rmtree(output, ignore_errors=True)
    else:
        output.rename(final)

    first, last = min(snapshot_values), max(snapshot_values)
    expected = []
    cursor = first
    while cursor <= last:
        expected.append(cursor)
        cursor += interval
    missing = sorted(set(expected) - set(snapshot_values))
    coverage = {
        "dataset_id": release_id,
        "time_basis": "period_start",
        "timezone": "UTC",
        "interval_seconds": int(interval.total_seconds()),
        "calendar": "24x7",
        "boundary": "[start,end)",
        "coverage": {
            "start": first.isoformat().replace("+00:00", "Z"),
            "end": (last + interval).isoformat().replace("+00:00", "Z"),
            "expected_snapshots": len(expected),
            "observed_snapshots": len(snapshot_values),
            "rows": total_rows,
            "coverage_ratio": len(snapshot_values) / len(expected),
            "latest_complete_period_end": (last + interval).isoformat().replace("+00:00", "Z"),
        },
        "missing_ranges": _datetime_ranges(missing, interval),
        "incomplete_partitions": [],
    }
    manifest = {
        "manifest_version": 1,
        "dataset_id": release_id,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "schema_id": schema["schema_id"],
        "partitioning": ["event_year", "event_month"],
        "files": files,
        "rows": total_rows,
        "dataset_sha256": dataset_sha256,
    }
    resolved_lineage = {**lineage, "dataset_id": release_id}
    resolved_capabilities = {**capabilities, "dataset_id": release_id}
    write_json(final / "schema.json", schema)
    write_json(final / "lineage.json", resolved_lineage)
    write_json(final / "coverage.json", coverage)
    write_json(final / "manifest.json", manifest)
    write_json(final / "capabilities.json", resolved_capabilities)
    write_json(final / "quality.json", {
        "quality_schema_version": 1,
        "dataset_id": release_id,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "passed": True,
        "checks": [{"name": "non_empty", "passed": True, "value": total_rows, "minimum": 1}],
        "metrics": {"rows": total_rows},
    })
    release = publish_release(
        root, product, release_id, manifest, provider=provider, venue=venue,
        transform_id=transform_id, transform_version=transform_version,
        quality_level=quality_level,
    )
    DatasetQualityService(root).assess(release.release_id)
    return IntradayColumnarRelease(release, manifest)


def _columns_sql(schema: dict[str, object]) -> str:
    columns = schema.get("columns")
    if not isinstance(columns, dict) or not columns:
        raise ValueError("columnar publishing requires schema columns")
    return ", ".join(_quote(str(name)) for name in columns)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _content_hash_release_id(product: ManagedDataset, content_hash: str) -> str:
    digest = sha256(str(product.key).encode() + b"\0" + content_hash.encode()).hexdigest()[:24]
    return f"ds_{digest}"


def _utc(value: object) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _datetime_ranges(values: list[datetime], interval: timedelta) -> list[dict[str, object]]:
    if not values:
        return []
    result, start, previous = [], values[0], values[0]
    for current in values[1:]:
        if current != previous + interval:
            result.append({
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": (previous + interval).isoformat().replace("+00:00", "Z"),
            })
            start = current
        previous = current
    result.append({
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": (previous + interval).isoformat().replace("+00:00", "Z"),
    })
    return result
