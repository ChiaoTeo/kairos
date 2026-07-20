from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from kairos.storage.codec import to_primitive
from kairos.storage.data_lake import write_json

from .catalog import DataCatalog
from .contracts import (
    DataProductDefinition, DataReleaseManifest, DataSetContractArtifact, DatasetRelease, DatasetStatus,
    DatasetStorageKind, QualityLevel,
)
from .products import DataProductContract


def content_release_id(product: DataProductContract, material: object) -> str:
    payload = json.dumps(to_primitive(material), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str).encode()
    digest = sha256(str(product.key).encode() + b"\0" + payload).hexdigest()[:24]
    return f"ds_{digest}"


def content_release_id_from_rows(product: DataProductContract, rows: list[dict[str, object]]) -> str:
    digest = sha256(str(product.key).encode() + b"\0")
    fields = tuple(sorted(rows[0])) if rows else ()
    digest.update("\x1f".join(fields).encode())
    for row in rows:
        digest.update(b"\x1e")
        for field in fields:
            digest.update(str(row.get(field, "")).encode())
            digest.update(b"\x1f")
    return f"ds_{digest.hexdigest()[:24]}"


def release_path(product: DataProductContract, release_id: str) -> str:
    return f"{product.relative_path}/release={release_id}"


def merge_release_rows(root: str | Path, base_release_id: str | None, rows: list[dict[str, object]], *,
                       primary_key: tuple[str, ...], order_by: tuple[str, ...]) -> list[dict[str, object]]:
    combined: list[dict[str, object]] = []
    if base_release_id is not None:
        catalog = DataCatalog(root)
        release = catalog.release(base_release_id)
        paths = sorted((Path(root) / release.relative_path).glob("**/*.parquet"))
        if paths:
            try:
                import pyarrow.dataset as ds
            except ImportError as error:
                raise RuntimeError("merging an immutable release requires the data optional dependency") from error
            table = ds.dataset([str(path) for path in paths], format="parquet").to_table()
            combined.extend({key: value for key, value in item.items()
                             if key not in {"event_year", "event_month", "event_day"}}
                            for item in table.to_pylist())
    combined.extend(rows)
    if primary_key:
        unique = {tuple(_primary_key_value(key, row.get(key, "")) for key in primary_key): row for row in combined}
        combined = list(unique.values())
    if not order_by:
        return combined
    return sorted(combined, key=lambda row: tuple(str(row.get(key, "")) for key in order_by))


def _primary_key_value(name: str, value: object) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif any(token in name for token in ("time", "date", "start", "end", "expiry")) and isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    else:
        return str(value)
    if parsed.tzinfo is None:
        raise ValueError(f"primary-key timestamp {name!r} must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def publish_release(root: str | Path, product: DataProductContract, release_id: str, manifest: dict[str, object], *,
                    provider: str, venue: str | None, transform_id: str, transform_version: str,
                    quality_level: QualityLevel = QualityLevel.STUDY) -> DatasetRelease:
    lake = Path(root)
    relative_path = release_path(product, release_id)
    directory = lake / relative_path
    quality_path = directory / "quality.json"
    if not quality_path.exists():
        raise ValueError(f"release {release_id} cannot be published without quality.json")
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    if not quality.get("passed"):
        raise ValueError(f"release {release_id} failed quality gates")
    levels = list(QualityLevel)
    if levels.index(quality_level) < levels.index(product.minimum_publication_level):
        raise ValueError(
            f"release quality {quality_level.value} is below data product contract minimum "
            f"{product.minimum_publication_level.value}"
        )
    content_hash = str(manifest.get("dataset_sha256") or "")
    if not content_hash:
        raise ValueError(f"release {release_id} has no content hash")
    status = (DatasetStatus.APPROVED_FOR_BACKTEST if quality_level in {QualityLevel.BACKTEST, QualityLevel.PRODUCTION}
              else DatasetStatus.APPROVED_FOR_STUDY)
    catalog = DataCatalog(lake)
    try:
        existing = catalog.release(release_id)
    except KeyError:
        existing = None
    if existing is not None:
        if (existing.content_hash, existing.provider, existing.venue) != (content_hash, provider, venue):
            raise ValueError(f"release ID {release_id!r} already refers to different content")
        return existing
    published_at = datetime.now(timezone.utc).isoformat()
    release_manifest = DataReleaseManifest(
        str(product.key),
        release_id,
        DataSetContractArtifact.from_product_contract(product).contract_hash,
        content_hash,
        product.product.primary_time,
        _schema_fields(directory),
        quality_level,
        {"provider": provider, "venue": venue, "transform_id": transform_id, "transform_version": transform_version},
        published_at,
    )
    write_json(directory / "data_release_manifest.json", release_manifest.to_primitive())
    release = DatasetRelease(
        release_id, product.key, f"content.{content_hash[:16]}",
        product.schema_id, _schema_version(product.schema_id), transform_id, transform_version,
        relative_path, "parquet", content_hash, provider, venue,
        (f"{product.key}@latest-validated",), status, quality_level, published_at,
        product.storage_kind, product.layout_version,
    )
    write_json(directory / "release.json", {
        "release_schema_version": 1, "release_id": release.release_id, "logical_key": str(release.product_key),
        "release_version": release.release_version, "schema_id": release.schema_id,
        "schema_version": release.schema_version, "transform_id": release.transform_id,
        "transform_version": release.transform_version, "content_hash": release.content_hash,
        "contract_hash": release_manifest.contract_hash,
        "data_release_manifest_hash": release_manifest.manifest_hash,
        "artifact_ref": release_manifest.artifact_ref,
        "provider": release.provider, "venue": release.venue, "status": release.status.value,
        "quality_level": release.quality_level.value, "published_at": release.published_at,
    })
    write_json(directory / "usage.json", {
        "usage_schema_version": 1, "logical_key": str(product.key), "primary_time": product.product.primary_time,
        "default_view": product.product.default_view.value, "dimensions": dict(product.product.dimensions),
        "known_limitations": [],
    })
    catalog.register_product_spec(product)
    catalog.register_release(release)
    catalog.save()
    return release


MARKET_REPLAY_DATASET_SCHEMA_ID = "market_replay_dataset.v2"


def register_market_replay_dataset(root: str | Path, dataset, directory: str | Path, product: DataProductDefinition, *,
                                   provider: str, venue: str | None, synthetic: bool = False) -> DatasetRelease:
    lake, path = Path(root), Path(directory)
    try:
        relative_path = str(path.relative_to(lake))
    except ValueError as error:
        raise ValueError("MarketReplayDataset storage must be inside the governed lake root") from error
    status = DatasetStatus.APPROVED_FOR_BACKTEST
    release = DatasetRelease(
        dataset.manifest.dataset_id, product.key, dataset.manifest.end.isoformat(), MARKET_REPLAY_DATASET_SCHEMA_ID, "2",
        "synthetic_market_replay_dataset" if synthetic else "market_replay_dataset", "2", relative_path, "parquet",
        dataset.manifest.content_hash, provider, venue, (), status, QualityLevel.BACKTEST,
        dataset.manifest.end.isoformat(), DatasetStorageKind.MARKET_SNAPSHOTS, "1",
    )
    catalog = DataCatalog(lake); catalog.register_product(product, enrich=True); catalog.register_release(release); catalog.save()
    from .release_metadata import ensure_release_metadata
    ensure_release_metadata(lake, release.release_id)
    return release

def _schema_version(schema_id: str) -> str:
    tail = schema_id.rsplit(".", 1)[-1]
    return tail[1:] if tail.startswith("v") and tail[1:].isdigit() else "1"


def _schema_fields(directory: Path) -> tuple[str, ...]:
    path = directory / "schema.json"
    if not path.exists():
        return ()
    schema = json.loads(path.read_text(encoding="utf-8"))
    fields = schema.get("fields")
    if isinstance(fields, dict):
        return tuple(str(name) for name in fields)
    if isinstance(fields, list):
        result = []
        for item in fields:
            result.append(str(item.get("name") if isinstance(item, dict) else item))
        return tuple(result)
    columns = schema.get("columns")
    if isinstance(columns, list):
        result = []
        for item in columns:
            result.append(str(item.get("name") if isinstance(item, dict) else item))
        return tuple(result)
    return ()
