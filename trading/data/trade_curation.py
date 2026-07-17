from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from trading.storage.data_lake import write_json

from .catalog import DataCatalog
from .models import DatasetRelease, DatasetStatus, QualityLevel
from .quality import DatasetQualityService


def curate_sorted_trade_release(root: str | Path, source_release_id: str) -> DatasetRelease:
    """External-sort an immutable Trade Release into its declared event_time/trade_id order."""
    try:
        import duckdb
    except ImportError as error:
        raise RuntimeError("Trade Release curation requires DuckDB") from error
    lake = Path(root)
    catalog = DataCatalog(lake)
    source = catalog.release(source_release_id)
    spec = catalog.product_spec(source.product_key)
    if spec.quality_profile != "trade":
        raise ValueError("sorted Trade curation requires the trade ProductSpec profile")
    source_directory = lake / source.relative_path
    source_manifest = _read(source_directory / "manifest.json")
    declared_files = source_manifest.get("files", [])
    source_files = sorted(
        source_directory / str(item["path"])
        for item in declared_files
        if isinstance(item, dict) and str(item.get("path", "")).endswith(".parquet")
    )
    if not source_files:
        source_files = sorted(path for path in source_directory.glob("**/*.parquet") if "/release=" not in path.as_posix())
    if not source_files:
        raise ValueError("source Trade Release has no Parquet files")
    staging = lake / spec.relative_path / "release=sorting-v1.tmp"
    if staging.exists():
        import shutil
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    escaped = [str(path).replace("'", "''") for path in source_files]
    source_sql = "[" + ",".join(f"'{path}'" for path in escaped) + "]"
    connection = duckdb.connect()
    try:
        connection.execute(f"PRAGMA temp_directory='{str(lake / 'tmp' / 'duckdb').replace("'", "''")}'")
        connection.execute("PRAGMA threads=1")
        connection.execute(
            f"CREATE TEMP VIEW source_trades AS SELECT * FROM read_parquet({source_sql}, union_by_name=true)"
        )
        schema = _read(source_directory / "schema.json")
        raw_columns = schema.get("columns") or schema.get("fields") or {}
        if not isinstance(raw_columns, dict) or not raw_columns:
            raise ValueError("Trade Release schema does not declare columns")
        columns = ", ".join(f'"{str(name).replace(chr(34), chr(34) * 2)}"' for name in raw_columns)
        partitions = connection.execute(
            "SELECT DISTINCT year(event_time), month(event_time) FROM source_trades ORDER BY 1, 2"
        ).fetchall()
        for year, month in partitions:
            partition = staging / f"event_year={int(year):04d}" / f"event_month={int(month):02d}"
            partition.mkdir(parents=True, exist_ok=True)
            target_file = str(partition / "part-00000.parquet").replace("'", "''")
            connection.execute(f"""
                COPY (
                    SELECT {columns}
                    FROM source_trades
                    WHERE year(event_time) = {int(year)} AND month(event_time) = {int(month)}
                    ORDER BY event_time, cast(trade_id AS VARCHAR)
                ) TO '{target_file}' (
                    FORMAT PARQUET,
                    COMPRESSION ZSTD,
                    ROW_GROUP_SIZE 100000,
                    PRESERVE_ORDER true
                )
            """)
    finally:
        connection.close()
    files = []
    total_rows = 0
    import pyarrow.parquet as pq
    for path in sorted(staging.glob("**/*.parquet")):
        content = path.read_bytes()
        rows = pq.read_metadata(path).num_rows
        total_rows += rows
        files.append({
            "path": path.relative_to(staging).as_posix(),
            "rows": rows,
            "bytes": len(content),
            "sha256": sha256(content).hexdigest(),
        })
    content_hash = sha256("".join(item["sha256"] for item in files).encode()).hexdigest()
    release_id = f"ds_{content_hash[:24]}"
    target = lake / spec.relative_path / f"release={release_id}"
    if target.exists():
        import shutil
        shutil.rmtree(staging)
    else:
        staging.rename(target)
    schema = _read(source_directory / "schema.json")
    coverage = _read(source_directory / "coverage.json")
    coverage["dataset_id"] = release_id
    manifest = {
        "manifest_version": 1,
        "dataset_id": release_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_id": source.schema_id,
        "partitioning": ["event_year", "event_month"],
        "files": files,
        "rows": total_rows,
        "dataset_sha256": content_hash,
    }
    write_json(target / "schema.json", schema)
    write_json(target / "coverage.json", coverage)
    write_json(target / "manifest.json", manifest)
    write_json(target / "lineage.json", {
        "lineage_version": 2,
        "producer": {"name": "curate_sorted_trade_release", "version": 1},
        "inputs": [{
            "release_id": source.release_id,
            "dataset_id": str(source.product_key),
            "content_hash": source.content_hash,
        }],
        "transform": {"order_by": ["event_time", "trade_id"], "content_preserving": True},
        "point_in_time_safe": True,
    })
    write_json(target / "capabilities.json", _read(source_directory / "capabilities.json"))
    write_json(target / "usage.json", _read(source_directory / "usage.json"))
    write_json(target / "quality.json", {
        "quality_schema_version": 2,
        "release_id": release_id,
        "profile": "trade",
        "passed": True,
        "level": QualityLevel.RESEARCH.value,
        "checks": [{"name": "external_sort_completed", "passed": True, "value": total_rows}],
    })
    release = DatasetRelease(
        release_id,
        source.product_key,
        f"content.{content_hash[:16]}",
        source.schema_id,
        source.schema_version,
        "curate_sorted_trade_release",
        "1",
        target.relative_to(lake).as_posix(),
        "parquet",
        content_hash,
        source.provider,
        source.venue,
        (),
        DatasetStatus.APPROVED_FOR_RESEARCH,
        QualityLevel.RESEARCH,
        datetime.now(timezone.utc).isoformat(),
        source.storage_kind,
        source.layout_version,
    )
    write_json(target / "release.json", {
        "release_schema_version": 1,
        "release_id": release.release_id,
        "logical_key": str(release.product_key),
        "release_version": release.release_version,
        "schema_id": release.schema_id,
        "schema_version": release.schema_version,
        "transform_id": release.transform_id,
        "transform_version": release.transform_version,
        "content_hash": release.content_hash,
        "provider": release.provider,
        "venue": release.venue,
        "status": release.status.value,
        "quality_level": release.quality_level.value,
        "published_at": release.published_at,
    })
    catalog.register_release(release)
    catalog.save()
    assessment = DatasetQualityService(lake).assess(release.release_id)
    if not assessment.passed:
        raise RuntimeError(f"sorted Trade Release failed typed quality: {release.release_id}")
    catalog = DataCatalog(lake)
    catalog.promote_alias(
        f"{source.product_key}@latest-validated",
        release.release_id,
        actor="trade-curation",
        reason="streaming Trade Profile passed after deterministic external sort",
        quality_report_hash=assessment.report_hash,
    )
    return DataCatalog(lake).release(release.release_id)


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}
