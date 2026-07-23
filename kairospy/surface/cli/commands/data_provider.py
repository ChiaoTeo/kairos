from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path

from kairospy.data.catalog import DataCatalog
from kairospy.data.contracts import DatasetRelease, DatasetStatus, DatasetStorageKind, QualityLevel
from kairospy.data.extensions.bootstrap import register_default_products
from kairospy.data.quality.services import DatasetQualityService
from kairospy.infrastructure.storage.data_lake import write_json
from kairospy.integrations.connectors.massive import MassiveClient, MassiveConfig, MassiveVendorArchiveClient


def removed_data_command_payload(operation: str, why: str, next_step: str | None = None) -> dict[str, object]:
    issue: dict[str, object] = {
        "code": f"{operation.replace('-', '_')}_removed",
        "message": f"kairospy data {operation} has been removed.",
        "why": why,
    }
    if next_step:
        issue["next_step"] = next_step
    return {
        "product": "data",
        "operation": operation,
        "status": "removed",
        "issues": [issue],
    }

def massive_marketdata_config(args: argparse.Namespace | None = None) -> MassiveConfig:
    from kairospy.infrastructure.configuration import ConfigError, load_dotenv_file, load_project_config_or_none
    from kairospy.integrations.config import resolve_massive_marketdata_config

    config = getattr(args, "_kairospy_project_config", None) if args is not None else None
    config = config or load_project_config_or_none()
    if config is not None:
        try:
            return resolve_massive_marketdata_config(config)
        except ConfigError:
            pass
    load_dotenv_file()
    return MassiveConfig.from_env()

def massive_request(args: argparse.Namespace) -> tuple[str, dict[str, object]]:
    if args.resource == "option-contracts":
        if not args.underlying:
            raise SystemExit("--underlying is required for option-contracts")
        return "/v3/reference/options/contracts", {"underlying_ticker": args.underlying, "as_of": args.start, "limit": args.limit, "sort": "ticker", "order": "asc"}
    if args.resource in {"option-quotes", "option-trades"}:
        if not args.ticker or not args.start or not args.end:
            raise SystemExit("--ticker, --start and --end are required for historical option quotes/trades")
        kind = "quotes" if args.resource == "option-quotes" else "trades"
        return f"/v3/{kind}/{args.ticker}", {"timestamp.gte": args.start, "timestamp.lt": args.end, "limit": args.limit, "sort": "timestamp", "order": "asc"}
    if args.resource == "aggregates":
        if not args.ticker or not args.start or not args.end:
            raise SystemExit("--ticker, --start and --end are required for aggregates")
        return f"/v2/aggs/ticker/{args.ticker}/range/{args.multiplier}/{args.timespan}/{args.start}/{args.end}", {"adjusted": True, "sort": "asc", "limit": args.limit}
    if not args.underlying:
        raise SystemExit("--underlying is required for option-chain")
    return f"/v3/snapshot/options/{args.underlying}", {"limit": args.limit}

def prepare_us_equity_momentum(args: argparse.Namespace) -> dict[str, object]:
    return removed_data_command_payload(
        "prepare-us-equity-momentum",
        "This workflow depended on release preparation and will be rebuilt on top of Dataset Store ingestion.",
        "Use Data Product ingestion and Dataset Store reads directly.",
    )

def latest_us_equity_identity_reference(lake_root: str | Path) -> dict[str, object]:
    root = Path(lake_root)
    manifests = sorted((root / "reference/provider=massive/equity_identity").glob("version=*/manifest.json"))
    if not manifests:
        return {"directory": None, "auto_detected": False, "reason": "missing"}
    candidates = []
    for path in manifests:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if int(manifest.get("quarantine_count", 0) or 0) != 0:
            continue
        if not (path.parent / "instruments.json").exists() or not (path.parent / "mappings.json").exists():
            continue
        candidates.append((path, manifest))
    if not candidates:
        return {"directory": None, "auto_detected": False, "reason": "no clean equity_identity manifest"}
    path, manifest = candidates[-1]
    release = ensure_us_equity_identity_release(root, path.parent, manifest)
    return {
        "directory": str(path.parent.relative_to(root)),
        "auto_detected": True,
        "content_sha256": manifest.get("sha256"),
        "release_id": release.release_id,
        "quality_level": release.quality_level.value,
        "instrument_count": manifest.get("instrument_count"),
        "mapping_count": manifest.get("mapping_count"),
    }

def ensure_us_equity_identity_release(root: Path, directory: Path, manifest: dict[str, object]) -> DatasetRelease:
    digest = str(manifest.get("sha256") or "")
    if not digest:
        raise ValueError(f"equity identity manifest is missing sha256: {directory}")
    register_default_products(root)
    catalog = DataCatalog(root)
    release_id = f"identity_{digest[:24]}"
    try:
        return catalog.release(release_id)
    except KeyError:
        pass
    product = catalog.product("reference.identity.equity.us.massive")
    catalog.register_release(DatasetRelease(
        release_id,
        product.key,
        f"content.{digest[:16]}",
        "reference.identity.equity.us.massive.v1",
        "1",
        "massive.equity_identity",
        "1",
        str(directory.relative_to(root)),
        "json",
        digest,
        "massive",
        "us-securities",
        ("reference.identity.equity.us.massive@latest-workspace",),
        DatasetStatus.APPROVED_FOR_WORKSPACE,
        QualityLevel.WORKSPACE,
        datetime.now(timezone.utc).isoformat(),
        DatasetStorageKind.REFERENCE,
        "1",
    ))
    catalog.save()
    assessment = DatasetQualityService(root).assess(release_id)
    return DataCatalog(root).release(release_id)

def sync_us_equity_momentum_corporate_actions(
    lake_root: str | Path,
    raw_release_paths: list[str],
    start: datetime,
    end: datetime,
    *,
    dataset_id: str,
) -> dict[str, object]:
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("corporate action sync requires timezone-aware increasing [start,end) timestamps")
    ticker_map = raw_equity_ticker_map(lake_root, raw_release_paths)
    if not ticker_map:
        raise ValueError("cannot sync corporate actions because prepared raw releases contain no ticker/instrument rows")

    archive = MassiveVendorArchiveClient(lake_root, MassiveClient(massive_marketdata_config()))
    events: list[dict[str, object]] = []
    receipts: list[str] = []
    per_ticker: dict[str, dict[str, int]] = {}
    for ticker, instrument_id in sorted(ticker_map.items()):
        split_archive = archive.fetch_pages("/v3/reference/splits", {
            "ticker": ticker,
            "execution_date.gte": start.date(),
            "execution_date.lt": end.date(),
            "limit": 1000,
        })
        dividend_archive = archive.fetch_pages("/v3/reference/dividends", {
            "ticker": ticker,
            "ex_dividend_date.gte": start.date(),
            "ex_dividend_date.lt": end.date(),
            "limit": 1000,
        })
        receipts.extend([
            str((split_archive.directory / "receipt.json").relative_to(Path(lake_root))),
            str((dividend_archive.directory / "receipt.json").relative_to(Path(lake_root))),
        ])
        split_count = 0
        dividend_count = 0
        for row in archive.iter_results(split_archive):
            ratio = Decimal(str(row["split_to"])) / Decimal(str(row["split_from"]))
            if ratio <= 0:
                raise ValueError(f"Massive split ratio must be positive for {ticker}")
            events.append({
                "source": "massive.splits",
                "source_id": str(row.get("id") or f"{ticker}:{row.get('execution_date') or row.get('ex_date')}:{ratio}"),
                "ticker": ticker,
                "instrument_id": instrument_id,
                "effective_at": {"$datetime": corporate_action_date(row.get("execution_date") or row.get("ex_date")).isoformat()},
                "ratio": {"$decimal": str(ratio)},
            })
            split_count += 1
        for row in archive.iter_results(dividend_archive):
            amount = Decimal(str(row["cash_amount"]))
            if amount < 0:
                raise ValueError(f"Massive dividend amount cannot be negative for {ticker}")
            events.append({
                "source": "massive.dividends",
                "source_id": str(row.get("id") or f"{ticker}:{row.get('ex_dividend_date')}:{amount}"),
                "ticker": ticker,
                "instrument_id": instrument_id,
                "ex_date": {"$datetime": corporate_action_date(row.get("ex_dividend_date")).isoformat()},
                "pay_date": {"$datetime": corporate_action_date(row.get("pay_date") or row.get("ex_dividend_date")).isoformat()},
                "currency": str(row.get("currency") or "USD"),
                "amount_per_share": {"$decimal": str(amount)},
            })
            dividend_count += 1
        per_ticker[ticker] = {"splits": split_count, "dividends": dividend_count}

    digest = sha256(json.dumps(events, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    directory = (
        Path(lake_root)
        / "reference/provider=massive/corporate_actions/scope=us_equity_momentum_bounded"
        / f"dataset={safe_dataset_component(dataset_id)}"
        / f"version={digest}"
    )
    write_json(directory / "events.json", events)
    write_json(directory / "manifest.json", {
        "manifest_version": 1,
        "provider": "massive",
        "scope": "us_equity_momentum_bounded",
        "dataset_id": dataset_id,
        "identity_source": "prepared raw release instrument_id",
        "boundary": "[start,end)",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "ticker_count": len(ticker_map),
        "event_count": len(events),
        "sha256": digest,
        "source_receipts": receipts,
        "per_ticker": per_ticker,
        "known_limitations": [
            "bounded ticker corporate action sync; requires full point-in-time identity mapping before full-market backtest readiness",
        ],
    })
    catalog = DataCatalog(lake_root)
    product = catalog.product("reference.corporate_actions.equity.us.massive")
    release_id = f"corpact_{digest[:24]}"
    relative = str(directory.relative_to(Path(lake_root)))
    catalog.register_release(DatasetRelease(
        release_id,
        product.key,
        f"content.{digest[:16]}",
        "reference.corporate_actions.equity.us.massive.v1",
        "1",
        "massive.corporate_actions",
        "1",
        relative,
        "json",
        digest,
        "massive",
        "us-securities",
        ("reference.corporate_actions.equity.us.massive@latest-workspace",),
        DatasetStatus.APPROVED_FOR_WORKSPACE,
        QualityLevel.WORKSPACE,
        datetime.now(timezone.utc).isoformat(),
        DatasetStorageKind.REFERENCE,
        "1",
    ))
    catalog.save()
    assessment = DatasetQualityService(lake_root).assess(release_id)
    return {
        "synced": True,
        "directory": str(directory.relative_to(Path(lake_root))),
        "release_id": release_id,
        "content_sha256": digest,
        "quality_level": assessment.level.value,
        "quality_passed": assessment.passed,
        "event_count": len(events),
        "ticker_count": len(ticker_map),
        "per_ticker": per_ticker,
    }

def raw_equity_ticker_map(lake_root: str | Path, relative_paths: list[str]) -> dict[str, str]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("US equity momentum corporate action sync requires the 'data' optional dependency") from error
    root = Path(lake_root)
    mapping: dict[str, str] = {}
    for relative in relative_paths:
        source = root / relative
        for path in parquet_files(source):
            for row in pq.read_table(path, columns=["ticker", "instrument_id"]).to_pylist():
                ticker = str(row.get("ticker") or "").strip().upper()
                instrument_id = str(row.get("instrument_id") or "").strip()
                if not ticker or not instrument_id:
                    continue
                previous = mapping.get(ticker)
                if previous is not None and previous != instrument_id:
                    raise ValueError(f"ticker {ticker} maps to multiple instrument IDs in prepared raw data")
                mapping[ticker] = instrument_id
    return mapping

def parquet_files(source: Path) -> list[Path]:
    manifest_path = source / "manifest.json"
    declared: list[Path] = []
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key in ("file", "files"):
            value = manifest.get(key)
            if isinstance(value, str):
                declared.append(source / value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        declared.append(source / item)
                    elif isinstance(item, dict) and item.get("path"):
                        declared.append(source / str(item["path"]))
    existing = sorted({path for path in declared if path.suffix == ".parquet" and path.exists()})
    return existing or sorted(source.glob("**/part-*.parquet")) or sorted(source.glob("*.parquet"))

def corporate_action_date(value: object) -> datetime:
    if value is None:
        raise ValueError("Massive corporate action is missing a date")
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return datetime.combine(date.fromisoformat(str(value)), datetime.min.time(), timezone.utc)

def safe_dataset_component(value: str) -> str:
    return "".join(item if item.isalnum() or item in {"-", "_", "."} else "_" for item in value)

def common_lake_directory(lake_root: str | Path, relative_paths: list[str]) -> str:
    if not relative_paths:
        raise ValueError("at least one raw release path is required")
    if len(relative_paths) == 1:
        return relative_paths[0]
    root = Path(lake_root)
    paths = [root / item for item in relative_paths]
    common = Path(os.path.commonpath([str(item) for item in paths]))
    return str(common.relative_to(root)) if common.is_relative_to(root) else str(common)

# Backward-compatible private aliases for tests and legacy imports.
_massive_marketdata_config = massive_marketdata_config
_massive_request = massive_request
_prepare_us_equity_momentum = prepare_us_equity_momentum
_latest_us_equity_identity_reference = latest_us_equity_identity_reference
_ensure_us_equity_identity_release = ensure_us_equity_identity_release
_sync_us_equity_momentum_corporate_actions = sync_us_equity_momentum_corporate_actions
_raw_equity_ticker_map = raw_equity_ticker_map
_parquet_files = parquet_files
_corporate_action_date = corporate_action_date
_safe_dataset_component = safe_dataset_component
_common_lake_directory = common_lake_directory
