from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path

from kairospy.data import DatasetClient, OutputFormat
from kairospy.environment import Environment
from kairospy.identity import InstrumentId
from kairospy.infrastructure.storage.codec import to_primitive
from kairospy.integrations.connectors.massive import (
    MassiveClient,
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
from kairospy.surface.cli.commands.data_provider import (
    common_lake_directory as _common_lake_directory,
    corporate_action_date as _corporate_action_date,
    ensure_us_equity_identity_release as _ensure_us_equity_identity_release,
    latest_us_equity_identity_reference as _latest_us_equity_identity_reference,
    massive_marketdata_config as _massive_marketdata_config,
    massive_request as _massive_request,
    parquet_files as _parquet_files,
    prepare_us_equity_momentum as _prepare_us_equity_momentum,
    raw_equity_ticker_map as _raw_equity_ticker_map,
    safe_dataset_component as _safe_dataset_component,
    sync_us_equity_momentum_corporate_actions as _sync_us_equity_momentum_corporate_actions,
)
from kairospy.surface.cli.commands.data_acquisition import (
    acquisition_limits as _acquisition_limits,
    acquisition_plan_payload as _acquisition_plan_payload,
    acquirable_product_rows as _acquirable_product_rows,
    plan_with_cli_instruments as _plan_with_cli_instruments,
    prompt_acquire_args as _prompt_acquire_args,
)
from kairospy.surface.cli.rendering.data import (
    dataset_acquire_payload as _dataset_acquire_payload,
    dataset_ready_status as _dataset_ready_status,
    diagnostic_rows as _diagnostic_rows,
    emit_data_payload as _emit_data_payload,
    hide_default_data_internals as _hide_default_data_internals,
    ready_for_dataset_status as _ready_for_dataset_status,
    render_acquisition_plan_payload as _render_acquisition_plan_payload,
    render_data_doctor_payload as _render_data_doctor_payload,
    render_data_product_doctor_payload as _render_data_product_doctor_payload,
    render_data_protocol_payload as _render_data_protocol_payload,
    render_data_use_payload as _render_data_use_payload,
    render_dataset_acquire_payload as _render_dataset_acquire_payload,
    render_query_payload as _render_query_payload,
    render_replay_payload as _render_replay_payload,
    render_sample_payload as _render_sample_payload,
)


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
    if args.action == "resolve":
        from kairospy.data import DataStreamResolver
        from kairospy.integrations.data_products.resolver import DataProductResolver

        stream_ref = DataStreamResolver(args.lake_root).resolve(args.stream)
        product_plan = DataProductResolver().resolve(args.stream)
        storage_dataset = str(stream_ref.dataset_id)
        product_payload = product_plan.to_payload()
        payload = {
            "product": "data",
            "operation": "resolve",
            "stream": str(stream_ref.stream_id),
            "space": str(stream_ref.space),
            "name": stream_ref.stream,
            "dataset": storage_dataset,
            "storage": {
                "dataset": storage_dataset,
                "data": str(DatasetClient(args.lake_root).store.data_path(stream_ref.dataset_id)),
                "live": str(DatasetClient(args.lake_root).store.live_path(stream_ref.dataset_id)),
                "source": stream_ref.source,
            },
            "plan": product_payload,
            "status": "resolved",
        }
        if product_payload.get("dataset") != storage_dataset:
            payload["compatible_dataset"] = product_payload.get("dataset")
        _emit_data_payload(args, "Kairos Data Resolve", payload)
        return 0
    if args.action == "import":
        from kairospy.surface import product as product_surface

        args.name = args.stream
        try:
            payload = product_surface.data_add(args)
        except Exception as error:
            from kairospy.data.storage.metadata import DataNeedsTimeError

            if isinstance(error, DataNeedsTimeError):
                payload = error.to_payload(dataset_id=str(args.stream), source=args.source)
                _emit_data_payload(args, "Kairos Data Import", payload)
                return 2
            if isinstance(error, product_surface.DataAddInputError):
                payload = error.to_payload(dataset_id=str(args.stream))
                _emit_data_payload(args, "Kairos Data Import", payload)
                return 2
            raise
        payload["operation"] = "import"
        payload["stream"] = str(args.stream)
        _emit_data_payload(args, "Kairos Data Import", payload)
        return 0
    if args.action == "read":
        from kairospy.data import DataApi

        data = DataApi(args.lake_root)
        query = {
            "start": args.start,
            "end": args.end,
            "columns": tuple(args.field) or None,
            "output": OutputFormat.ROWS,
            "time_field": args.time_field,
        }
        if any(character in args.stream for character in "*?[]"):
            rows_by_stream = data.read_pattern(args.stream, **query)
            payload = {
                "product": "data",
                "operation": "read",
                "pattern": args.stream,
                "streams": {
                    stream: {
                        "returned_rows": min(len(rows), args.limit),
                        "total_rows": len(rows),
                        "rows": to_primitive(rows[:args.limit]),
                    }
                    for stream, rows in rows_by_stream.items()
                },
                "status": "ready",
            }
        else:
            rows = data.read(args.stream, **query)
            ref = data.resolve_stream(args.stream)
            payload = {
                "product": "data",
                "operation": "read",
                "stream": str(ref.stream_id),
                "dataset": str(ref.dataset_id),
                "returned_rows": min(len(rows), args.limit),
                "total_rows": len(rows),
                "rows": to_primitive(rows[:args.limit]),
                "status": "ready",
            }
        _emit_data_payload(args, "Kairos Data Read", payload)
        return 0
    if args.action == "replace-window":
        from kairospy.data import DataApi

        result = DataApi(args.lake_root).replace_window(
            args.stream,
            args.source,
            start=args.start,
            end=args.end,
            time_field=getattr(args, "time_field", None),
        )
        _emit_data_payload(args, "Kairos Data Replace Window", {
            "product": "data",
            "operation": "replace-window",
            **result,
            "status": "replaced",
        })
        return 0
    if args.action == "get":
        from kairospy.integrations.data_products.resolver import DataProductResolver
        from kairospy.surface import product as product_surface

        plan = DataProductResolver().resolve(args.stream)
        source_plan = dict(plan.source_plan or {})
        product_key = str(source_plan.get("product_key") or plan.product_key or "")
        if not product_key:
            raise SystemExit(f"data get cannot resolve a historical Data Product for stream {args.stream!r}")
        if plan.capability not in {"historical", "both"}:
            raise SystemExit(f"data get requires a historical stream; {args.stream!r} resolved to {plan.capability}")
        request = argparse.Namespace(
            lake_root=args.lake_root,
            key=product_key,
            list_products=False,
            start=args.start,
            end=args.end,
            provider=plan.provider,
            venue=plan.venue,
            instrument=([str(source_plan["instrument"])] if source_plan.get("instrument") else []),
            for_use="workspace",
            refresh=bool(getattr(args, "refresh", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
            as_dataset=None,
        )
        payload = product_surface.data_use(request)
        payload["operation"] = "get"
        payload["stream"] = str(plan.target_stream)
        payload["target_dataset"] = str(plan.dataset_id)
        payload["source_plan"] = source_plan
        compatible_dataset = payload.get("dataset")
        if not bool(getattr(args, "dry_run", False)):
            materialized = _materialize_stream_dataset(
                args.lake_root,
                source_dataset=str(payload.get("dataset") or ""),
                target_dataset=str(plan.dataset_id),
                source_plan=source_plan,
                primary_time=plan.primary_time,
                data_product=product_key,
                provider=plan.provider,
                venue=plan.venue,
            )
            if materialized is not None:
                payload["dataset"] = materialized["dataset"]
                payload["stream_materialization"] = materialized
        dataset = str(payload.get("dataset") or "")
        if dataset and not bool(getattr(args, "dry_run", False)):
            from kairospy.data import DatasetStore

            DatasetStore(args.lake_root).alias(dataset, str(plan.target_stream))
            payload["stream_alias"] = {
                "stream": str(plan.target_stream),
                "dataset": dataset,
            }
        if str(compatible_dataset or payload.get("dataset")) != str(plan.dataset_id):
            payload["compatible_dataset"] = compatible_dataset or payload.get("dataset")
        _emit_data_payload(args, "Kairos Data Get", payload)
        return 0
    if args.action == "probe":
        from kairospy.integrations.data_products.resolver import DataProductResolver
        from kairospy.surface import product as product_surface

        plan = DataProductResolver().resolve(args.stream)
        source_plan = dict(plan.source_plan or {})
        product_key = str(source_plan.get("product_key") or plan.product_key or "")
        if not product_key:
            raise SystemExit(f"data probe cannot resolve a live Data Product for stream {args.stream!r}")
        if plan.capability not in {"live", "both"}:
            raise SystemExit(f"data probe requires a live stream; {args.stream!r} resolved to {plan.capability}")
        request = argparse.Namespace(
            lake_root=args.lake_root,
            source=product_key,
            as_dataset=None,
            instrument=([str(source_plan["instrument"])] if source_plan.get("instrument") else []),
            channel=source_plan.get("channel"),
            market=source_plan.get("market"),
            levels=source_plan.get("levels"),
            interval=source_plan.get("interval"),
            limit=args.limit,
            format=args.format,
        )
        if bool(getattr(args, "dry_run", False)):
            payload = {
                "product": "data",
                "operation": "probe",
                "status": "planned",
                "stream": str(plan.target_stream),
                "target_dataset": str(plan.dataset_id),
                "source": product_key,
                "provider": plan.provider,
                "venue": plan.venue,
                "limit": args.limit,
                "source_plan": source_plan,
            }
        else:
            payload = product_surface.data_sample(request)
            payload["operation"] = "probe"
            payload["stream"] = str(plan.target_stream)
            payload["target_dataset"] = str(plan.dataset_id)
            payload["source_plan"] = source_plan
        _emit_data_payload(args, "Kairos Data Probe", payload)
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

        target = _stream_or_dataset_option(args)
        removed = DatasetStore(args.lake_root).clean_tmp(target)
        _emit_data_payload(args, "Kairos Dataset Tmp", {
            "product": "data",
            "operation": "clean-tmp",
            "status": "cleaned",
            **({"stream": str(target)} if target is not None else {}),
            "removed": [str(path) for path in removed],
            "count": len(removed),
        })
        return 0
    if args.action == "delete-stream-data":
        from kairospy.data import DataStreamResolver, DatasetStore

        ref = DataStreamResolver(args.lake_root).resolve(args.stream)
        result = DatasetStore(args.lake_root).delete_data(
            ref.dataset_id,
            start=args.start,
            end=args.end,
            time_field=getattr(args, "time_field", None),
            all_data=bool(getattr(args, "all_data", False)),
        )
        _emit_data_payload(args, "Kairos Data Delete Stream Data", {
            "product": "data",
            "operation": "delete-stream-data",
            "stream": str(ref.stream_id),
            "dataset": str(ref.dataset_id),
            "source": ref.source,
            **result,
            "status": "deleted",
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


def _materialize_stream_dataset(
    root: object,
    *,
    source_dataset: str,
    target_dataset: str,
    source_plan: dict[str, object],
    primary_time: str | None,
    data_product: str,
    provider: str | None,
    venue: str | None,
) -> dict[str, object] | None:
    if not source_dataset or not target_dataset or source_dataset == target_dataset:
        return None
    instrument = str(source_plan.get("instrument") or "")
    if not instrument:
        return None
    from kairospy.data import DatasetStore, DatasetWriter
    from kairospy.data.storage.reader import DatasetReader

    store = DatasetStore(root)
    rows = DatasetReader(store).read(source_dataset, output="rows")
    selected = [row for row in rows if _row_matches_stream_instrument(row, instrument)]
    if not selected:
        return None
    fields = list(selected[0].keys())
    store.ensure_dataset(target_dataset, metadata={
        "primary_time": primary_time,
        "fields": fields,
        "data_product": data_product,
        "provider": provider,
        "venue": venue,
        "source": {
            "source_kind": "stream_materialization",
            "dataset": source_dataset,
            "instrument": instrument,
        },
    })
    DatasetWriter(store).append(
        target_dataset,
        selected,
        partition_by=("event_day",),
        time_field=primary_time,
    )
    return {
        "dataset": target_dataset,
        "source_dataset": source_dataset,
        "row_count": len(selected),
        "instrument": instrument,
    }


def _row_matches_stream_instrument(row: dict[str, object], instrument: str) -> bool:
    normalized = _normalize_instrument_token(instrument)
    candidates = (
        row.get("symbol"),
        row.get("coin"),
        row.get("instrument"),
        row.get("instrument_id"),
        row.get("source_instrument_id"),
    )
    return any(normalized and normalized in _normalize_instrument_token(value) for value in candidates if value is not None)


def _normalize_instrument_token(value: object) -> str:
    return "".join(character for character in str(value).upper() if character.isalnum())


def _dataset_argument(args: argparse.Namespace) -> str:
    option = getattr(args, "dataset", None)
    positional = getattr(args, "dataset_arg", None)
    if option and positional and str(option) != str(positional):
        raise SystemExit(f"conflicting Dataset values: {positional} and {option}")
    dataset = option or positional
    if not dataset:
        raise SystemExit("Dataset is required")
    return str(dataset)


def _stream_or_dataset_option(args: argparse.Namespace) -> str | None:
    stream = getattr(args, "stream", None)
    dataset = getattr(args, "dataset", None)
    if stream and dataset and str(stream) != str(dataset):
        raise SystemExit(f"conflicting Stream/Dataset values: {stream} and {dataset}")
    value = stream or dataset
    return str(value) if value else None
