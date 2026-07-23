from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path

from kairospy.data import DatasetClient, OutputFormat
from kairospy.data.acquisition import AcquisitionLimits
from kairospy.data.catalog import DataCatalog
from kairospy.data.contracts import (
    DatasetLayer,
    DataProductDefinition,
    DatasetRelease,
    DatasetStatus,
    DatasetStorageKind,
    QualityLevel,
)
from kairospy.environment import Environment
from kairospy.execution.events import TradeSide
from kairospy.identity import InstrumentId
from kairospy.infrastructure.storage.codec import to_primitive
from kairospy.integrations.connectors.massive import (
    MassiveClient,
    MassiveConfig,
    MassiveEntitlementDiagnostics,
    MassiveEquityDailyOhlcvPipeline,
    MassiveEquityHourlyOhlcvPipeline,
    MassiveEquityIdentityResolver,
    MassiveFlatFileBatchDownloader,
    MassiveFlatFileClient,
    MassiveMarketSnapshotBuilder,
    MassiveReferencePipeline,
    MassiveVendorArchiveClient,
    OptionCloseImpliedVolatilityPipeline,
    OptionDailyOhlcvPipeline,
    SpxwDailyOhlcvPipeline,
)
from kairospy.market.repository import ParquetMarketEventRepository
from kairospy.reference.contracts import ProductType
from kairospy.data.extensions.bootstrap import register_default_products
from kairospy.data.quality.services import DatasetQualityService
from kairospy.infrastructure.storage.data_lake import write_json
from kairospy.surface.cli.prompts import prompt_text as _prompt_text


def _massive_marketdata_config(args: argparse.Namespace | None = None) -> MassiveConfig:
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

def data_command(args: argparse.Namespace) -> int:
    if args.action == "apply":
        from kairospy.surface import product as product_surface
        payload = product_surface.data_apply(args)
        _emit_data_payload(args, "Kairos Data Manifest", payload)
        return 0
    if args.action == "start":
        from kairospy.surface import product as product_surface
        payload = product_surface.data_start(args)
        _emit_data_payload(args, "Kairos Data Start", payload)
        return 0
    if args.action == "add":
        from kairospy.surface import product as product_surface
        try:
            payload = product_surface.data_add(args)
        except Exception as error:
            from kairospy.data.storage.metadata import DataNeedsTimeError

            if isinstance(error, DataNeedsTimeError):
                payload = error.to_payload(dataset_id=str(args.name), source=args.source)
                _emit_data_payload(args, "Kairos Dataset", payload)
                return 2
            if isinstance(error, product_surface.DataAddInputError):
                payload = error.to_payload(dataset_id=str(args.name))
                _emit_data_payload(args, "Kairos Dataset", payload)
                return 2
            raise
        _emit_data_payload(args, "Kairos Dataset", payload)
        return 0
    if args.action == "use":
        if not args.list_products and not args.key:
            raise SystemExit("data use requires a built-in product key or --list-products")
        from kairospy.surface import product as product_surface
        try:
            payload = product_surface.data_use(args)
        except product_surface.DataProductNotFoundError as error:
            payload = error.to_payload()
            _emit_data_payload(args, "Kairos Built-In Data Product", payload)
            return 2
        _emit_data_payload(args, "Kairos Dataset", payload)
        return 0
    if args.action in {"product", "products"}:
        from kairospy.surface import product as product_surface
        if args.product_action == "list":
            _emit_data_payload(args, "Kairos Dataset", product_surface.data_product_list(args))
            return 0
        if args.product_action == "doctor":
            payload = product_surface.data_product_doctor(args)
            _emit_data_payload(args, "Kairos Data Product", payload)
            return 2 if payload.get("status") == "unknown_data_product" else 0
        else:
            raise SystemExit(f"unsupported data product action {args.product_action!r}")
    if args.action == "protocol":
        from kairospy.surface import product as product_surface
        try:
            payload = product_surface.data_protocol(args)
        except (FileNotFoundError, ValueError) as error:
            payload = product_surface.data_protocol_error(args, error)
            _emit_data_payload(args, "Kairos Data Protocol", payload)
            return 2
        _emit_data_payload(args, "Kairos Data Protocol", payload)
        return 0
    if args.action == "connect":
        from kairospy.surface import product as product_surface
        try:
            payload = product_surface.data_connect(args)
        except product_surface.DataProductNotFoundError as error:
            payload = error.to_payload()
            _emit_data_payload(args, "Kairos Built-In Data Product", payload)
            return 2
        _emit_data_payload(args, "Kairos Dataset", payload)
        return 0
    if args.action == "sample":
        from kairospy.surface import product as product_surface
        try:
            if args.format == "json":
                payload = product_surface.data_sample(args)
            else:
                started = {"value": False}

                def _print_sample_row(row):
                    if not started["value"]:
                        print("Kairos Data Sample")
                        print("Rows")
                        started["value"] = True
                    print(json.dumps(to_primitive(row), ensure_ascii=False, sort_keys=True), flush=True)

                payload = product_surface.data_sample(args, on_row=_print_sample_row)
        except product_surface.DataProductNotFoundError as error:
            payload = error.to_payload()
            _emit_data_payload(args, "Kairos Built-In Data Product", payload)
            return 2
        _emit_data_payload(args, "Kairos Data Sample Summary" if args.format != "json" else "Kairos Data Sample", payload)
        return 0
    if args.action == "reconnect":
        from kairospy.surface import product as product_surface
        try:
            payload = product_surface.data_reconnect(args)
        except product_surface.DataLiveDatasetNotConfiguredError as error:
            payload = error.to_payload()
            _emit_data_payload(args, "Kairos Dataset", payload)
            return 2
        _emit_data_payload(args, "Kairos Dataset", payload)
        return 0
    if args.action in {"download", "register-download", "register-provider", "write"}:
        from kairospy.surface import product as product_surface
        handlers = {
            "download": product_surface.data_download,
            "register-download": product_surface.data_register_download,
            "register-provider": product_surface.data_register_provider,
            "write": product_surface.data_write,
        }
        payload = handlers[args.action](args)
        print(json.dumps(to_primitive(payload), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.action == "soak-binance":
        if args.duration_seconds <= 0 or args.minimum_events <= 0 or args.maximum_silence_seconds <= 0:
            raise SystemExit("soak duration, minimum events and maximum silence must be positive")
        import asyncio
        from kairospy.integrations.connectors.binance.market_stream import BinanceStreamSession, WebSocketClientConnector, websocket_url
        from kairospy.integrations.connectors.binance.stream import BinanceCanonicalStreamService
        from kairospy.market.capture import RotatingCanonicalCaptureWriter
        from kairospy.market.soak import run_binance_market_restart_campaign, run_binance_market_soak
        from kairospy.market.stream import BoundedEventChannel

        symbol = args.symbol.upper()
        stream = f"{symbol.lower()}@{args.channel}"
        instrument = InstrumentId(args.instrument or f"crypto:binance:spot:{symbol}")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        journal = args.journal or (
            Path(args.lake_root) / "source" / "live" / "binance" / f"{symbol.lower()}-{args.channel}-{stamp}.jsonl"
        )
        artifact = args.artifact or journal.with_suffix(".soak.json")

        async def soak():
            def build(index: int, *, campaign: bool):
                leg_journal = journal.with_name(
                    f"{journal.stem}.leg-{index:03d}{journal.suffix}",
                ) if campaign else journal
                leg_canonical = leg_journal.with_suffix(".canonical.jsonl")
                output = BoundedEventChannel(max(4096, args.minimum_events * 2))
                service = BinanceCanonicalStreamService(
                    BinanceStreamSession(
                        WebSocketClientConnector(), websocket_url(
                            Environment.LIVE, stream, public_only=True,
                        ), journal=leg_journal,
                    ),
                    {symbol: instrument}, output, source_instance="kairospy-soak", stream_id=stream,
                    canonical_capture=RotatingCanonicalCaptureWriter(
                        leg_canonical, session_id=leg_journal.stem, source="binance",
                        maximum_segment_events=args.capture_segment_events,
                        maximum_segment_bytes=args.capture_segment_bytes,
                        maximum_total_bytes=args.capture_total_bytes,
                    ),
                )
                return service, output
            if args.restart_interval_seconds:
                return await run_binance_market_restart_campaign(
                    lambda index: build(index, campaign=True), stream_id=stream,
                    duration_seconds=args.duration_seconds,
                    restart_interval_seconds=args.restart_interval_seconds,
                    minimum_events=args.minimum_events,
                    maximum_silence_seconds=args.maximum_silence_seconds,
                    artifact_path=artifact,
                    maximum_channel_utilization=args.maximum_channel_utilization,
                )
            service, output = build(1, campaign=False)
            return await run_binance_market_soak(
                service, output, duration_seconds=args.duration_seconds,
                minimum_events=args.minimum_events,
                maximum_silence_seconds=args.maximum_silence_seconds,
                artifact_path=artifact,
                maximum_channel_utilization=args.maximum_channel_utilization,
            )

        result = asyncio.run(soak())
        payload = to_primitive(result)
        if args.live_view_manifest is not None:
            from kairospy.data.quality.freshness import update_live_view_manifest_freshness
            manifest = update_live_view_manifest_freshness(args.live_view_manifest, payload)
            payload["live_view_manifest"] = {
                "artifact": str(args.live_view_manifest),
                "live_view_id": manifest.live_view_id,
                "freshness_status": manifest.freshness_status,
                "manifest_hash": manifest.manifest_hash,
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if result.passed else 2
    if args.action == "live-binance":
        if args.messages <= 0:
            raise SystemExit("--messages must be positive")
        import asyncio
        from kairospy.integrations.connectors.binance.market_stream import BinanceStreamSession, WebSocketClientConnector, websocket_url
        from kairospy.integrations.connectors.binance.stream import BinanceCanonicalStreamService
        from kairospy.market.stream import BoundedEventChannel
        from kairospy.market.capture import CanonicalCaptureWriter

        symbol = args.symbol.upper()
        stream = f"{symbol.lower()}@{args.channel}"
        instrument = InstrumentId(args.instrument or (
            f"crypto:binance:{'futures' if args.futures else 'spot'}:{symbol}"
        ))
        journal = args.journal or (
            Path(args.lake_root) / "source" / "live" / "binance"
            / f"{symbol.lower()}-{args.channel}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
        )
        canonical_path = journal.with_suffix(".canonical.jsonl")

        async def capture():
            output = BoundedEventChannel(max(16, args.messages * 2))
            service = BinanceCanonicalStreamService(
                BinanceStreamSession(
                    WebSocketClientConnector(), websocket_url(
                        Environment.LIVE, stream, futures=args.futures, public_only=not args.futures,
                    ),
                    journal=journal,
                ),
                {symbol: instrument}, output,
                source_instance="kairospy-cli", stream_id=stream,
                canonical_capture=CanonicalCaptureWriter(
                    canonical_path, session_id=journal.stem, source="binance",
                ),
            )
            producer = asyncio.create_task(service.run(message_limit=args.messages))
            events = [event async for event in output.events()]
            await producer
            return service, events

        service, events = asyncio.run(capture())
        print(json.dumps({
            "provider": "binance", "stream": stream, "instrument_id": instrument.value,
            "raw_messages": service.raw_messages, "canonical_events": service.canonical_events,
            "reconnects": service.reconnects, "raw_journal": str(journal),
            "canonical_journal": str(canonical_path),
            "events": to_primitive(events),
        }, ensure_ascii=False, indent=2))
        return 0
    if args.action == "list":
        from kairospy.surface import product as product_surface
        args.dimension = _dimension_filters(args.dimension)
        payload = product_surface.data_list(args)
        _emit_data_payload(args, "Kairos Datasets", payload)
        return 0
    if args.action == "releases":
        payload = _removed_data_command_payload(
            "releases",
            "Data Store Datasets are unversioned directories; use `kairospy data list` or `kairospy data metadata <dataset>`.",
        )
        _emit_data_payload(args, "Kairos Data Releases", payload)
        return 2
    if args.action == "search":
        from kairospy.surface import product as product_surface
        args.dimension = _dimension_filters(args.dimension)
        payload = product_surface.data_list(args)
        payload["operation"] = "search"
        _emit_data_payload(args, "Kairos Data Search", payload)
        return 0
    if args.action == "describe":
        from kairospy.surface import product as product_surface
        _emit_data_payload(args, "Kairos Dataset", product_surface.data_doctor(args))
        return 0
    if args.action == "audit":
        from kairospy.surface import product as product_surface
        _emit_data_payload(args, "Kairos Dataset Audit", product_surface.data_audit(args))
        return 0
    if args.action == "doctor":
        from kairospy.surface import product as product_surface
        _emit_data_payload(args, "Kairos Data Diagnostics", product_surface.data_doctor(args))
        return 0
    if args.action == "metadata":
        from kairospy.surface import product as product_surface
        try:
            payload = product_surface.data_metadata(args)
        except product_surface.DataDatasetInputError as error:
            payload = error.to_payload()
            _emit_data_payload(args, "Kairos Dataset Metadata", payload)
            return 2
        _emit_data_payload(args, "Kairos Dataset Metadata", payload)
        return 0
    if args.action == "diagnostics":
        from kairospy.surface import product as product_surface

        report = product_surface.data_list(args)
        report["operation"] = "diagnostics"
        report["healthy"] = all(item.get("status") == "ready" for item in report.get("datasets", ()))
        _emit_data_payload(args, "Kairos Data Diagnostics", report)
        return 2 if args.strict and not report["healthy"] else 0
    if args.action == "repair-index":
        from kairospy.data import DatasetStore

        path = DatasetStore(args.lake_root).rebuild_index()
        _emit_data_payload(args, "Kairos Dataset Index", {
            "product": "data",
            "operation": "repair-index",
            "status": "rebuilt",
            "index": str(path),
        })
        return 0
    if args.action == "clean-tmp":
        from kairospy.data import DatasetStore

        removed = DatasetStore(args.lake_root).clean_tmp(getattr(args, "dataset", None))
        _emit_data_payload(args, "Kairos Dataset Tmp", {
            "product": "data",
            "operation": "clean-tmp",
            "status": "cleaned",
            "removed": [str(path) for path in removed],
            "count": len(removed),
        })
        return 0
    if args.action == "us-equity-momentum-diagnostics":
        from kairospy.analytics.features import UsEquityMomentumDiagnostics
        report = UsEquityMomentumDiagnostics(args.lake_root).report(workspace=args.workspace, version=args.version)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2 if args.strict and report["summary"]["errors"] else 0
    if args.action == "validate":
        from kairospy.surface import product as product_surface
        try:
            payload = product_surface.data_validate(args)
        except product_surface.DataDatasetInputError as error:
            payload = error.to_payload()
            _emit_data_payload(args, "Kairos Data Validation", payload)
            return 2
        _emit_data_payload(args, "Kairos Data Validation", payload)
        return 0 if payload["status"] == "passed" else 2
    if args.action == "prepare":
        payload = _removed_data_command_payload(
            "prepare",
            "Dataset preparation no longer publishes releases or promotes quality levels; use built-in product ingestion commands.",
        )
        _emit_data_payload(args, "Kairos Data Preparation", payload)
        return 2
    if args.action == "prepare-us-equity-momentum":
        payload = _removed_data_command_payload(
            "prepare-us-equity-momentum",
            "This workflow depended on release preparation and will be rebuilt on top of Dataset Store ingestion.",
        )
        _emit_data_payload(args, "Kairos US Equity Momentum Preparation", payload)
        return 2
    if args.action == "query":
        if args.limit <= 0:
            raise SystemExit("--limit must be positive")
        dataset = _dataset_argument(args)
        from kairospy.surface import product as product_surface
        metadata = None
        try:
            metadata = product_surface.data_metadata(args)
            rows = DatasetClient(args.lake_root).read(
                dataset,
                start=args.start,
                end=args.end,
                columns=tuple(args.field) or None,
                output=OutputFormat.ROWS,
                time_field=str(metadata.get("time") or "") or None,
            )
        except product_surface.DataDatasetInputError as error:
            _emit_data_payload(args, "Kairos Data Query", error.to_payload())
            return 2
        except (FileNotFoundError, KeyError) as error:
            if isinstance(metadata, dict) and metadata.get("live", {}).get("configured"):
                payload = product_surface._historical_not_configured_error("query", dataset).to_payload()
            else:
                payload = product_surface._dataset_not_found_error("query", dataset).to_payload()
            _emit_data_payload(args, "Kairos Data Query", payload)
            return 2
        payload = {
            "product": "data", "operation": "query", "dataset": str(metadata.get("dataset") or dataset),
            "returned_rows": min(len(rows), args.limit), "total_rows": len(rows),
            "rows": to_primitive(rows[:args.limit]),
        }
        _emit_data_payload(args, "Kairos Data Query", payload); return 0
    if args.action == "replay":
        from kairospy.surface import product as product_surface
        try:
            payload = product_surface.data_replay(args)
        except product_surface.DataDatasetInputError as error:
            payload = error.to_payload()
            _emit_data_payload(args, "Kairos Data Replay", payload)
            return 2
        _emit_data_payload(args, "Kairos Data Replay", payload)
        return 0
    if args.action == "freeze":
        payload = _removed_data_command_payload(
            "freeze",
            "Dataset Store does not know workspaces or frozen release refs; strategy/runtime snapshots should own reproducibility.",
        )
        _emit_data_payload(args, "Kairos Data Freeze", payload)
        return 2
    if args.action == "catalog":
        from kairospy.surface import product as product_surface

        _emit_data_payload(args, "Kairos Data Catalog", product_surface.data_list(args))
        return 0
    if args.action == "copy":
        payload = _removed_data_command_payload(
            "copy",
            "Dataset release copy has been removed from the Data product.",
            "Copy Dataset Store directories directly or rebuild datasets through provider ingestion.",
        )
        _emit_data_payload(args, "Kairos Data Copy", payload)
        return 2
    if args.action == "compare":
        payload = _removed_data_command_payload(
            "compare",
            "Data Store has no release identity to compare; compare Dataset tables explicitly in analysis code.",
        )
        _emit_data_payload(args, "Kairos Data Compare", payload)
        return 2
    if args.action == "audit-artifact":
        payload = _removed_data_command_payload(
            "audit-artifact",
            "Release artifact audit has moved out of Dataset storage.",
        )
        _emit_data_payload(args, "Kairos Data Audit Artifact", payload)
        return 2
    if args.action == "alias":
        from kairospy.data import DatasetStore

        dataset = _dataset_argument(args)
        store = DatasetStore(args.lake_root)
        dataset_id = store.resolve(dataset)
        if not store.dataset_path(dataset_id).exists():
            from kairospy.surface import product as product_surface

            _emit_data_payload(args, "Kairos Dataset Alias", product_surface._dataset_not_found_error("alias", dataset).to_payload())
            return 2
        path = store.alias(dataset_id, args.alias)
        _emit_data_payload(args, "Kairos Dataset Alias", {
            "product": "data",
            "operation": "alias",
            "dataset": str(dataset_id),
            "alias": args.alias,
            "path": str(path),
            "status": "ready",
        })
        return 0
    if args.action in {"plan", "acquire"}:
        payload = _removed_data_command_payload(
            args.action,
            "Dataset Store no longer plans/acquires immutable releases; provider-specific product ingestion owns this workflow.",
        )
        _emit_data_payload(args, "Kairos Acquisition Plan", payload)
        return 2
    if args.action == "promote":
        if getattr(args, "for_use", None):
            if not args.dataset:
                raise SystemExit("data promote requires a Dataset name with --for")
            from kairospy.surface import product as product_surface
            try:
                payload = product_surface.data_promote(args)
            except product_surface.DataDatasetInputError as error:
                payload = error.to_payload()
                _emit_data_payload(args, "Kairos Dataset Promotion", payload)
                return 2
            _emit_data_payload(args, "Kairos Dataset Promotion", payload)
            return 0 if payload.get("status") in {"ready_for_workspace", "ready_for_backtest", "ready_for_production"} else 2
        raise SystemExit("data promote requires --for workspace, --for backtest, or --for production")
    if args.action == "quarantine-insecure-provider-cache":
        moved = MassiveVendorArchiveClient.quarantine_non_https(args.lake_root)
        print(json.dumps({"quarantined": len(moved), "paths": [str(item) for item in moved]}, ensure_ascii=False, indent=2)); return 0
    if args.action == "sync-provider-reference":
        pipeline = MassiveReferencePipeline(args.lake_root, MassiveClient(_massive_marketdata_config(args)))
        result: dict[str, object] = {"code_tables": pipeline.sync_code_tables()}
        if args.equity_tickers:
            result["equity_tickers"] = pipeline.sync_equity_tickers(include_inactive=not args.active_only)
        if args.ticker:
            if not args.start or not args.end:
                raise SystemExit("--start and --end are required with --ticker")
            result["corporate_actions"] = pipeline.sync_corporate_actions(args.ticker, datetime.fromisoformat(args.start), datetime.fromisoformat(args.end))
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    if args.action == "build-provider-equity-identity":
        reference_rows = json.loads(args.reference_rows.read_text(encoding="utf-8"))
        ticker_events = json.loads(args.ticker_events.read_text(encoding="utf-8")) if args.ticker_events else []
        resolver = MassiveEquityIdentityResolver()
        resolved = resolver.resolve(reference_rows, ticker_events)
        manifest = resolver.save(resolved, args.lake_root)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0 if not resolved.quarantined else 2
    if args.action == "build-provider-slices":
        dataset = MassiveMarketSnapshotBuilder(args.lake_root, reference_catalog_path=args.reference_catalog_path, dataset_root=args.dataset_root).build(
            args.source_dataset, args.output_dataset, datetime.fromisoformat(args.start), datetime.fromisoformat(args.end),
            sampling_seconds=args.sampling_seconds, max_quote_age_seconds=args.max_quote_age_seconds,
            split=args.split, risk_free_rate=args.risk_free_rate)
        print(f"{dataset.manifest.dataset_id}: slices={dataset.manifest.slice_count} hash={dataset.manifest.content_hash}")
        return 0
    if args.action == "provider-entitlement-diagnostics":
        report = MassiveEntitlementDiagnostics(MassiveClient(_massive_marketdata_config(args))).check(
            underlying=args.underlying, option_ticker=args.option_ticker, date=args.date)
        print(json.dumps({
            "ready": report.ready,
            "api_host": report.api_host,
            "official_underlying_history": report.official_underlying_history,
            "valuation_reference_mode": report.valuation_reference_mode,
            "checks": report.checks,
        }, ensure_ascii=False, indent=2))
        return 0 if report.ready else 2
    if args.action == "compact-market-events":
        result = ParquetMarketEventRepository(Path(args.lake_root) / "canonical" / "market").compact(args.dataset)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    if args.action == "provider-fetch":
        client = MassiveClient(_massive_marketdata_config(args))
        archive = MassiveVendorArchiveClient(args.lake_root, client)
        resource, params = _massive_request(args)
        result = archive.fetch_pages(resource, params, max_pages=args.max_pages)
        print(json.dumps({"fingerprint": result.fingerprint, "directory": str(result.directory), "receipt": result.receipt}, ensure_ascii=False, indent=2))
        return 0
    if args.action == "provider-flat-file":
        client = MassiveClient(_massive_marketdata_config(args))
        flat = MassiveFlatFileClient(args.lake_root, client)
        if args.operation == "usage":
            print(json.dumps(flat.usage(), ensure_ascii=False, indent=2)); return 0
        if not args.key:
            raise SystemExit("--key is required for Massive Flat File status/download")
        if args.operation == "status":
            print(json.dumps(flat.cache_status(args.key), ensure_ascii=False, indent=2)); return 0
        print(flat.download(args.key)); return 0
    if args.action == "provider-flat-file-batch":
        flat = MassiveFlatFileClient(args.lake_root, MassiveClient(_massive_marketdata_config(args)))
        report = MassiveFlatFileBatchDownloader(flat).download_range(
            date.fromisoformat(args.start), date.fromisoformat(args.end), max_files=args.max_files, dry_run=args.dry_run,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2)); return 0
    if args.action in {"prepare-spxw-daily-ohlcv", "prepare-spxw-day-aggs"}:
        manifest = SpxwDailyOhlcvPipeline(args.lake_root).prepare(
            args.dataset_id, date.fromisoformat(args.start), date.fromisoformat(args.end),
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0
    if args.action in {"prepare-option-daily-ohlcv", "prepare-option-day-aggs"}:
        manifest = OptionDailyOhlcvPipeline(args.lake_root, args.option_root).prepare(
            args.dataset_id, date.fromisoformat(args.start), date.fromisoformat(args.end),
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0
    if args.action in {"prepare-equity-daily-ohlcv", "prepare-equity-day-aggs"}:
        manifest = MassiveEquityDailyOhlcvPipeline(
            args.lake_root, MassiveClient(_massive_marketdata_config(args)),
        ).prepare(
            args.dataset_id, args.ticker, date.fromisoformat(args.start), date.fromisoformat(args.end),
            view=args.view,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0
    if args.action in {"prepare-equity-hourly-ohlcv", "prepare-equity-hour-aggs"}:
        manifest = MassiveEquityHourlyOhlcvPipeline(
            args.lake_root, MassiveClient(_massive_marketdata_config(args)),
        ).prepare(
            args.dataset_id, args.ticker, date.fromisoformat(args.start), date.fromisoformat(args.end),
            view=args.view,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0
    if args.action == "prepare-option-close-implied-volatility":
        manifest = OptionCloseImpliedVolatilityPipeline(args.lake_root).prepare(
            args.dataset_id, args.option_dataset, args.equity_dataset,
            risk_free_rate=args.risk_free_rate, dividend_yield=args.dividend_yield,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2)); return 0
    metadata = DatasetClient(args.lake_root).metadata(args.dataset)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


def _removed_data_command_payload(operation: str, why: str) -> dict[str, object]:
    return {
        "product": "data",
        "operation": operation,
        "status": "removed",
        "issues": [{
            "code": f"{operation.replace('-', '_')}_removed",
            "message": f"kairospy data {operation} has been removed.",
            "why": why,
        }],
    }


def _emit_data_payload(args: argparse.Namespace, title: str, payload: object) -> None:
    from kairospy.surface.cli.output import (
        render_builtin_data_products, render_data_catalog, render_dataset_detail, render_dataset_list,
        render_dataset_releases, render_generic_payload, render_key_value_panel, render_status_table,
    )

    primitive = to_primitive(payload)
    if getattr(args, "action", None) != "audit":
        primitive = _hide_default_data_internals(primitive)
    if args.format == "json":
        print(json.dumps(primitive, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if not isinstance(primitive, dict):
        print(json.dumps(primitive, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if args.action == "catalog" and isinstance(primitive.get("products"), list):
        print(render_data_catalog(primitive["products"]))
        return
    if (
        args.action in {"product", "products"}
        and getattr(args, "product_action", None) == "list"
        and isinstance(primitive.get("products"), list)
    ):
        print(render_builtin_data_products("Kairos Built-In Data Products", primitive["products"]))
        return
    if args.action in {"product", "products"} and getattr(args, "product_action", None) == "doctor":
        print(_render_data_product_doctor_payload(title, primitive))
        return
    if args.action == "protocol":
        print(_render_data_protocol_payload(title, primitive))
        return
    if args.action == "use" and getattr(args, "list_products", False) and isinstance(primitive.get("products"), list):
        print(render_builtin_data_products("Kairos Built-In Data Products", primitive["products"]))
        return
    if args.action == "use" and primitive.get("operation") == "use":
        print(_render_data_use_payload(title, primitive))
        return
    if args.action == "list" and isinstance(primitive.get("datasets"), list):
        print(render_dataset_list(title, primitive["datasets"]))
        return
    if args.action == "releases" and isinstance(primitive.get("releases"), list):
        print(render_dataset_releases(title, primitive["releases"]))
        return
    if args.action == "search" and isinstance(primitive.get("datasets"), list):
        print(render_dataset_list(title, primitive["datasets"]))
        return
    if args.action == "acquire" and isinstance(primitive.get("products"), list):
        print(render_dataset_list(title, primitive["products"]))
        return
    if args.action in {"describe", "doctor"}:
        print(_render_data_doctor_payload(title, primitive))
        return
    if args.action == "diagnostics":
        print(render_status_table(title, _diagnostic_rows(primitive)))
        return
    if args.action == "acquire" and primitive.get("operation") == "acquire":
        print(_render_dataset_acquire_payload(title, primitive))
        return
    if args.action in {"plan", "acquire"}:
        print(_render_acquisition_plan_payload(title, primitive))
        return
    if args.action == "query":
        print(_render_query_payload(title, primitive))
        return
    if args.action == "replay":
        print(_render_replay_payload(title, primitive))
        return
    if args.action == "sample":
        print(_render_sample_payload(title, primitive))
        return
    print(render_generic_payload(title, primitive))


def _hide_default_data_internals(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _hide_default_data_internals(item)
            for key, item in value.items()
            if key != "source_kind"
        }
    if isinstance(value, list):
        return [_hide_default_data_internals(item) for item in value]
    return value


def _dimension_filters(values: list[str]) -> dict[str, str]:
    dimensions = {}
    for item in values:
        if "=" not in item:
            raise SystemExit("--dimension must use key=value")
        key, value = item.split("=", 1)
        if not key.strip() or not value.strip():
            raise SystemExit("--dimension key and value cannot be empty")
        dimensions[key.strip()] = value.strip()
    return dimensions


def _render_data_use_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel

    historical = payload.get("historical") if isinstance(payload.get("historical"), dict) else {}
    rows = [
        ("Product", payload.get("product", "")),
        ("Operation", payload.get("operation", "")),
        ("Dataset", payload.get("dataset", "")),
        ("Data Product", payload.get("data_product", "")),
        ("Default Dataset", payload.get("default_dataset", "")),
        ("Title", payload.get("title", "")),
        ("Capability", payload.get("capability", "")),
        ("Target Use", payload.get("target_use", "")),
        ("Status", historical.get("status", "")),
        ("Ready For", ", ".join(str(item) for item in historical.get("ready_for", ()))),
        ("Blocked For", ", ".join(str(item) for item in historical.get("blocked_for", ()))),
        ("Time", payload.get("time", "")),
        ("Requires Account", payload.get("requires_account", "")),
        ("Provider", payload.get("provider", "")),
        ("Venue", payload.get("venue", "")),
    ]
    return render_key_value_panel(title, [(label, value) for label, value in rows if value not in ("", None)])


def _dataset_argument(args: argparse.Namespace) -> str:
    option = getattr(args, "dataset", None)
    positional = getattr(args, "dataset_arg", None)
    if option and positional and str(option) != str(positional):
        raise SystemExit(f"conflicting Dataset values: {positional} and {option}")
    dataset = option or positional
    if not dataset:
        raise SystemExit("Dataset is required")
    return str(dataset)


def _render_sample_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel

    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    rows = [
        ("Product", payload.get("product", "")),
        ("Operation", payload.get("operation", "")),
        ("Source", payload.get("source", "")),
        ("Dataset", payload.get("dataset", "")),
        ("Provider", payload.get("provider", "")),
        ("Venue", payload.get("venue", "")),
        ("Market", runtime.get("market", "")),
        ("Symbol", runtime.get("symbol", "")),
        ("Channel", runtime.get("channel", "")),
        ("Levels", runtime.get("levels", "")),
        ("Interval", runtime.get("interval", "")),
        ("Stream", runtime.get("stream", "")),
        ("Limit", payload.get("limit", "")),
        ("Row Count", payload.get("row_count", "")),
    ]
    return render_key_value_panel(title, [(label, value) for label, value in rows if value not in ("", None)])


def _prompt_acquire_args(args: argparse.Namespace, client: DatasetClient, providers) -> None:
    if args.dataset and args.start and args.end:
        return
    products = _acquirable_product_rows(client, providers)
    if not products:
        raise SystemExit("no acquirable data products are registered")
    if args.dataset is None:
        print("Acquirable Data Products")
        for index, product in enumerate(products, start=1):
            print(f"  {index}. {product['logical_key']}  {product['title']}")
        selected = _prompt_text("Dataset number or logical key", "1").strip()
        if selected.isdigit() and 1 <= int(selected) <= len(products):
            args.dataset = str(products[int(selected) - 1]["logical_key"])
        else:
            args.dataset = selected
    if args.start is None:
        args.start = _prompt_text("Start [inclusive ISO-8601]", "")
    if args.end is None:
        args.end = _prompt_text("End [exclusive ISO-8601]", "")
    if not args.instrument:
        universe = _prompt_text("Universe [full-market or comma-separated instruments]", "full-market").strip()
        if universe and universe != "full-market":
            args.instrument = tuple(item.strip() for item in universe.split(",") if item.strip())


def _acquirable_product_rows(client: DatasetClient, providers) -> list[dict[str, object]]:
    rows = []
    specs = getattr(providers, "_specs", {})
    for key, spec in sorted(specs.items()):
        product = spec.product
        rows.append({
            "logical_key": str(key),
            "title": product.title,
            "layer": product.layer.value,
            "dimensions": dict(product.dimensions),
            "primary_time": product.primary_time,
            "sources": to_primitive(product.sources),
            "releases": [to_primitive(release) for release in client.catalog.releases(product)],
        })
    return rows


def _plan_with_cli_instruments(plan, providers, instruments: tuple[str, ...]):
    if not instruments or plan.selected is None or not plan.connector_available:
        return plan
    from dataclasses import replace
    from kairospy.data.acquisition import AcquisitionRequest

    connector = providers.get(plan.selected.provider, plan.logical_key)
    request = AcquisitionRequest(
        plan.logical_key, plan.missing, plan.selected, instruments,
        base_release_id=plan.local_release_id,
    )
    estimate = connector.estimate(request) if hasattr(connector, "estimate") else plan.estimate
    return replace(plan, estimate=estimate)


def _acquisition_plan_payload(plan, providers, instruments: tuple[str, ...]) -> dict[str, object]:
    payload = to_primitive(plan)
    if plan.selected is None or not plan.connector_available:
        return payload
    connector = providers.get(plan.selected.provider, plan.logical_key)
    task_plan = getattr(connector, "task_plan", None)
    if task_plan is None:
        return payload
    from kairospy.data.acquisition import AcquisitionRequest

    request = AcquisitionRequest(
        plan.logical_key, plan.missing, plan.selected, instruments,
        base_release_id=plan.local_release_id,
    )
    try:
        payload["provider_tasks"] = task_plan(request)
    except Exception as error:
        payload["provider_tasks"] = {"status": "unavailable", "error": f"{type(error).__name__}: {error}"}
    return payload


def _acquisition_limits(args: argparse.Namespace) -> AcquisitionLimits:
    max_requests = int(getattr(args, "max_requests", 10_000))
    max_instruments = int(getattr(args, "max_instruments", 10_000))
    max_bytes = getattr(args, "max_bytes", None)
    if max_requests <= 0 or max_instruments <= 0 or max_bytes is not None and int(max_bytes) <= 0:
        raise SystemExit("acquisition limits must be positive")
    return AcquisitionLimits(maximum_requests=max_requests, maximum_instruments=max_instruments, maximum_bytes=max_bytes)


def _diagnostic_rows(payload: dict[str, object]) -> list[dict[str, object]]:
    if isinstance(payload.get("checks"), list):
        return [
            {
                "name": item.get("name", item.get("check", "check")),
                "status": "ok" if item.get("passed", item.get("healthy", False)) else "warn",
                "detail": item.get("detail", item.get("message", "")),
            }
            for item in payload["checks"] if isinstance(item, dict)
        ]
    summary = payload.get("summary")
    if isinstance(summary, dict):
        return [{"name": key, "status": "ok" if not value else "warn", "detail": value} for key, value in summary.items()]
    healthy = payload.get("healthy", payload.get("passed", True))
    return [{"name": "data", "status": "ok" if healthy else "warn", "detail": "healthy" if healthy else "needs attention"}]


def _render_data_doctor_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel

    def join_values(key: str) -> str:
        values = payload.get(key)
        if isinstance(values, list):
            return ", ".join(str(item) for item in values) if values else "-"
        return "-"

    rows = (
        ("Dataset", payload.get("dataset", "-")),
        ("Status", payload.get("status", "-")),
        ("Time", payload.get("time", "-")),
        ("Ready For", join_values("ready_for")),
        ("Blocked For", join_values("blocked_for")),
        ("Issues", join_values("issues")),
    )
    return render_key_value_panel(title, rows)


def _dataset_acquire_payload(release: DatasetRelease) -> dict[str, object]:
    ready_for = _ready_for_dataset_status(release.status)
    all_uses = ("workspace", "backtest", "production")
    return {
        "product": "data",
        "operation": "acquire",
        "dataset": str(release.product_key),
        "status": _dataset_ready_status(release.status),
        "ready_for": ready_for,
        "blocked_for": [value for value in all_uses if value not in ready_for],
        "provider": release.provider,
        "venue": release.venue,
        "quality_level": release.quality_level.value,
        "format": release.format,
    }


def _dataset_ready_status(status: DatasetStatus) -> str:
    if status is DatasetStatus.APPROVED_FOR_PRODUCTION:
        return "ready_for_production"
    if status is DatasetStatus.APPROVED_FOR_BACKTEST:
        return "ready_for_backtest"
    if status is DatasetStatus.APPROVED_FOR_WORKSPACE:
        return "ready_for_workspace"
    return status.value


def _ready_for_dataset_status(status: DatasetStatus) -> list[str]:
    if status is DatasetStatus.APPROVED_FOR_PRODUCTION:
        return ["workspace", "backtest", "production"]
    if status is DatasetStatus.APPROVED_FOR_BACKTEST:
        return ["workspace", "backtest"]
    if status is DatasetStatus.APPROVED_FOR_WORKSPACE:
        return ["workspace"]
    return []


def _render_dataset_acquire_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel

    def join_values(key: str) -> str:
        values = payload.get(key)
        if isinstance(values, list):
            return ", ".join(str(item) for item in values) if values else "-"
        return "-"

    rows = (
        ("Dataset", payload.get("dataset", "-")),
        ("Status", payload.get("status", "-")),
        ("Ready For", join_values("ready_for")),
        ("Blocked For", join_values("blocked_for")),
        ("Provider", payload.get("provider", "-")),
        ("Venue", payload.get("venue", "-")),
        ("Quality Level", payload.get("quality_level", "-")),
        ("Format", payload.get("format", "-")),
    )
    return render_key_value_panel(title, rows)


def _render_data_product_doctor_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel, render_status_table

    aliases = payload.get("aliases")
    alias_text = ", ".join(str(alias) for alias in aliases) if isinstance(aliases, list) and aliases else "-"
    rows = (
        ("Data Product", payload.get("key") or payload.get("requested_key", "-")),
        ("Requested", payload.get("requested_key", "-")),
        ("Status", payload.get("status", "-")),
        ("Available", "yes" if payload.get("available") else "no"),
        ("Provider", payload.get("provider", "-")),
        ("Venue", payload.get("venue", "-")),
        ("Dataset", payload.get("dataset", "-")),
        ("Capability", payload.get("capability", "-")),
        ("Aliases", alias_text),
    )
    output = [render_key_value_panel(title, rows)]
    issues = payload.get("issues")
    if isinstance(issues, list) and issues:
        output.append(render_status_table(
            "Issues",
            [{"code": item.get("code", ""), "message": item.get("message", "")}
             for item in issues if isinstance(item, dict)],
            columns=("code", "message"),
        ))
    commands = payload.get("next_commands")
    if isinstance(commands, list) and commands:
        output.append(render_status_table(
            "Next Commands",
            [{"command": command} for command in commands],
            columns=("command",),
        ))
    return "\n\n".join(item for item in output if item)


def _render_query_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel, render_status_table

    if payload.get("status") and "returned_rows" not in payload:
        issues = payload.get("issues")
        issue_codes = []
        if isinstance(issues, list):
            for issue in issues:
                if isinstance(issue, dict):
                    issue_codes.append(str(issue.get("code") or issue.get("message") or issue))
                else:
                    issue_codes.append(str(issue))
        return render_key_value_panel(title, (
            ("Dataset", payload.get("dataset", "-")),
            ("Status", payload.get("status", "-")),
            ("Issues", ", ".join(issue_codes) if issue_codes else "-"),
            ("Next Command", payload.get("next_command", "-")),
        ))

    output = [render_key_value_panel(title, (
        ("Dataset", payload.get("dataset", "-")),
        ("Returned Rows", payload.get("returned_rows", "-")),
        ("Total Rows", payload.get("total_rows", "-")),
    ))]
    rows = payload.get("rows")
    if isinstance(rows, list) and rows:
        fields = tuple(str(key) for key in rows[0].keys()) if isinstance(rows[0], dict) else ("row",)
        table_rows = []
        for row in rows:
            table_rows.append({field: row.get(field, "") for field in fields} if isinstance(row, dict) else {"row": row})
        output.append(render_status_table("Rows", table_rows, columns=fields))
    return "\n\n".join(output)


def _render_replay_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel

    if payload.get("status") and "returned_rows" not in payload:
        issues = payload.get("issues")
        issue_codes = []
        if isinstance(issues, list):
            for issue in issues:
                if isinstance(issue, dict):
                    issue_codes.append(str(issue.get("code") or issue.get("message") or issue))
                else:
                    issue_codes.append(str(issue))
        return render_key_value_panel(title, (
            ("Dataset", payload.get("dataset", "-")),
            ("Status", payload.get("status", "-")),
            ("Issues", ", ".join(issue_codes) if issue_codes else "-"),
            ("Next Command", payload.get("next_command", "-")),
        ))

    output = [render_key_value_panel(title, (
        ("Dataset", payload.get("dataset", "-")),
        ("Returned Rows", payload.get("returned_rows", "-")),
        ("Total Rows", payload.get("total_rows", "-")),
    ))]
    rows = payload.get("rows")
    if isinstance(rows, list) and rows:
        output.append("Rows")
        output.extend(json.dumps(to_primitive(row), ensure_ascii=False, sort_keys=True) for row in rows)
    return "\n".join(output)


def _render_data_protocol_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel, render_status_table

    protocols = payload.get("protocols")
    if isinstance(protocols, list):
        rows = [
            {
                "kind": item.get("kind", ""),
                "interface": item.get("interface", ""),
                "used_by": item.get("used_by", ""),
            }
            for item in protocols
            if isinstance(item, dict)
        ]
        return render_status_table(title, rows, columns=("kind", "interface", "used_by"))

    output = [render_key_value_panel(title, (
        ("Kind", payload.get("kind", "-")),
        ("Status", payload.get("status", "-")),
        ("Source", payload.get("source", payload.get("file", "-"))),
        ("Rows", payload.get("row_count", "-")),
        ("Next Command", payload.get("next_command", "-")),
    ))]
    checks = payload.get("checks")
    if isinstance(checks, list) and checks:
        rows = []
        for item in checks:
            if isinstance(item, dict):
                rows.append({
                    "name": item.get("name", ""),
                    "passed": item.get("passed", ""),
                    "value": item.get("value", ""),
                })
        output.append(render_status_table("Checks", rows, columns=("name", "passed", "value")))
    template = payload.get("template")
    if isinstance(template, str) and template:
        output.append(template.rstrip())
    issues = payload.get("issues")
    if isinstance(issues, list) and issues:
        rows = []
        for item in issues:
            if isinstance(item, dict):
                rows.append({
                    "code": item.get("code", ""),
                    "message": item.get("message", ""),
                })
        output.append(render_status_table("Issues", rows, columns=("code", "message")))
    return "\n\n".join(output)


def _render_acquisition_plan_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel, render_status_table

    estimate = payload.get("estimate") if isinstance(payload.get("estimate"), dict) else {}
    selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else {}
    requested = payload.get("requested") if isinstance(payload.get("requested"), dict) else {}
    missing = payload.get("missing") if isinstance(payload.get("missing"), list) else []
    rows = (
        ("Dataset", payload.get("logical_key", "-")),
        ("Provider", selected.get("provider", "-") if selected else "-"),
        ("Venue", selected.get("venue", "-") if selected else "-"),
        ("Provider Access", "available" if payload.get("connector_available") else "unavailable"),
        ("Complete", payload.get("complete", False)),
        ("Missing Ranges", len(missing)),
        ("Estimated Requests", estimate.get("requests", "-") if estimate else "-"),
        ("Estimated Instruments", estimate.get("instruments", "-") if estimate else "-"),
        ("Cost Class", estimate.get("cost_class", "-") if estimate else "-"),
    )
    output = [render_key_value_panel(title, rows)]
    tasks = payload.get("provider_tasks")
    if isinstance(tasks, dict) and tasks:
        task_rows = (
            ("Provider", tasks.get("provider", "-")),
            ("Task Type", tasks.get("task_type", "-")),
            ("Universe", tasks.get("universe", "-")),
            ("Symbols", tasks.get("symbols", "-")),
            ("Total Tasks", tasks.get("total_tasks", "-")),
            ("Cached Tasks", tasks.get("cached_tasks", "-")),
            ("Uncached Tasks", tasks.get("uncached_tasks", "-")),
            ("Resume Supported", tasks.get("resume_supported", "-")),
        )
        output.append(render_key_value_panel("Provider Task Plan", task_rows))
        ranges = tasks.get("ranges")
        if isinstance(ranges, list) and ranges:
            output.append(render_status_table(
                "Task Ranges",
                [item for item in ranges if isinstance(item, dict)],
                columns=("start", "end", "tasks", "cached", "uncached"),
            ))
        matrix = tasks.get("matrix")
        if isinstance(matrix, list) and matrix:
            output.append(render_status_table(
                "Task Matrix",
                [item for item in matrix if isinstance(item, dict)],
                columns=("year", "month", "tasks", "cached_monthly", "cached_daily_files"),
            ))
    elif isinstance(requested, dict):
        output.append(render_key_value_panel("Requested Window", (
            ("Start", requested.get("start", "-")),
            ("End", requested.get("end", "-")),
        )))
    return "\n\n".join(item for item in output if item)


def _massive_request(args: argparse.Namespace) -> tuple[str, dict[str, object]]:
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


def _prepare_us_equity_momentum(args: argparse.Namespace) -> dict[str, object]:
    return _removed_data_command_payload(
        "prepare-us-equity-momentum",
        "This workflow depended on release preparation and will be rebuilt on top of Dataset Store ingestion.",
        "Use Data Product ingestion and Dataset Store reads directly.",
    )


def _latest_us_equity_identity_reference(lake_root: str | Path) -> dict[str, object]:
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
    release = _ensure_us_equity_identity_release(root, path.parent, manifest)
    return {
        "directory": str(path.parent.relative_to(root)),
        "auto_detected": True,
        "content_sha256": manifest.get("sha256"),
        "release_id": release.release_id,
        "quality_level": release.quality_level.value,
        "instrument_count": manifest.get("instrument_count"),
        "mapping_count": manifest.get("mapping_count"),
    }


def _ensure_us_equity_identity_release(root: Path, directory: Path, manifest: dict[str, object]) -> DatasetRelease:
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


def _sync_us_equity_momentum_corporate_actions(
    lake_root: str | Path,
    raw_release_paths: list[str],
    start: datetime,
    end: datetime,
    *,
    dataset_id: str,
) -> dict[str, object]:
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("corporate action sync requires timezone-aware increasing [start,end) timestamps")
    ticker_map = _raw_equity_ticker_map(lake_root, raw_release_paths)
    if not ticker_map:
        raise ValueError("cannot sync corporate actions because prepared raw releases contain no ticker/instrument rows")

    archive = MassiveVendorArchiveClient(lake_root, MassiveClient(_massive_marketdata_config()))
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
                "effective_at": {"$datetime": _corporate_action_date(row.get("execution_date") or row.get("ex_date")).isoformat()},
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
                "ex_date": {"$datetime": _corporate_action_date(row.get("ex_dividend_date")).isoformat()},
                "pay_date": {"$datetime": _corporate_action_date(row.get("pay_date") or row.get("ex_dividend_date")).isoformat()},
                "currency": str(row.get("currency") or "USD"),
                "amount_per_share": {"$decimal": str(amount)},
            })
            dividend_count += 1
        per_ticker[ticker] = {"splits": split_count, "dividends": dividend_count}

    digest = sha256(json.dumps(events, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    directory = (
        Path(lake_root)
        / "reference/provider=massive/corporate_actions/scope=us_equity_momentum_bounded"
        / f"dataset={_safe_dataset_component(dataset_id)}"
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


def _raw_equity_ticker_map(lake_root: str | Path, relative_paths: list[str]) -> dict[str, str]:
    try:
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("US equity momentum corporate action sync requires the 'data' optional dependency") from error
    root = Path(lake_root)
    mapping: dict[str, str] = {}
    for relative in relative_paths:
        source = root / relative
        for path in _parquet_files(source):
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


def _parquet_files(source: Path) -> list[Path]:
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


def _corporate_action_date(value: object) -> datetime:
    if value is None:
        raise ValueError("Massive corporate action is missing a date")
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return datetime.combine(date.fromisoformat(str(value)), datetime.min.time(), timezone.utc)


def _safe_dataset_component(value: str) -> str:
    return "".join(item if item.isalnum() or item in {"-", "_", "."} else "_" for item in value)


def _common_lake_directory(lake_root: str | Path, relative_paths: list[str]) -> str:
    if not relative_paths:
        raise ValueError("at least one raw release path is required")
    if len(relative_paths) == 1:
        return relative_paths[0]
    root = Path(lake_root)
    paths = [root / item for item in relative_paths]
    common = Path(os.path.commonpath([str(item) for item in paths]))
    return str(common.relative_to(root)) if common.is_relative_to(root) else str(common)
