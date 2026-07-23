from __future__ import annotations

import argparse
import json

from kairospy.integrations.connectors.ibkr.option_chain_provider import IbkrSpxwOptionChainProvider
from kairospy.research.capture.option_capture import OptionCaptureService
from kairospy.research.capture.report import summarize
from kairospy.research.capture.spec import MarketDataType, OptionChainCaptureSpec
from kairospy.research.capture.series import SeriesCaptureProgress, SeriesCaptureService, SeriesCaptureSpec
from kairospy.infrastructure.storage.repository import FileOptionCaptureRepository
from kairospy.data.snapshots.market_snapshot_storage import MarketSnapshotStorageDriver
from kairospy.reference.contracts import OptionRight


def option_capture_spec(args: argparse.Namespace) -> OptionChainCaptureSpec:
    values: dict[str, Any] = {}
    if args.config:
        values = json.loads(args.config.read_text(encoding="utf-8"))
    for name in ("expiry_count", "strikes_each_side", "market_data_type"):
        value = getattr(args, name, None)
        if value is not None:
            values[name] = value
    if "market_data_type" in values:
        values["market_data_type"] = MarketDataType(values["market_data_type"])
    if "rights" in values:
        values["rights"] = tuple(OptionRight(value) for value in values["rights"])
    return OptionChainCaptureSpec(**values)

def capture_command(args: argparse.Namespace) -> int:
    repository = FileOptionCaptureRepository(args.data_root)
    service = OptionCaptureService(repository)
    if args.action == "governance-audit":
        from kairospy.governance import audit_governance
        result=audit_governance(args.lake_root)
        print(json.dumps({"passed":result.passed,"checked_datasets":result.checked_datasets,
            "checked_experiments":result.checked_experiments,"checked_strategies":result.checked_strategies,
            "violations":result.violations},ensure_ascii=False,indent=2))
        return 0 if result.passed else 2
    if args.action == "capture-series":
        if args.instruments:
            return _capture_normalized_series(args)
        spec = _spec(args)
        provider = IbkrSpxwOptionChainProvider(spec, host=args.host, port=args.port, client_id=args.client_id)
        series_spec = SeriesCaptureSpec(args.dataset_id, args.samples, args.interval_seconds, args.split, args.checkpoint_samples)
        def report_progress(progress: SeriesCaptureProgress) -> None:
            checkpoint = " saved" if progress.checkpoint_saved else ""
            print(
                f"Sample {progress.completed_samples}/{progress.total_samples} "
                f"requested={progress.requested_contracts} qualified={progress.qualified_contracts} "
                f"quotes={progress.quoted_contracts} checkpoint={checkpoint or 'pending'} "
                f"at={progress.timestamp.isoformat()}",
                flush=True,
            )

        dataset = SeriesCaptureService(
            MarketSnapshotStorageDriver(args.dataset_root), on_progress=report_progress,
        ).capture(provider, spec, series_spec, append=args.append)
        print(f"Dataset: {dataset.manifest.dataset_id}")
        print(f"Slices: {dataset.manifest.slice_count}")
        print(f"Hash: {dataset.manifest.content_hash}")
        return 0
    if args.action == "capture":
        spec = _spec(args)
        provider = IbkrSpxwOptionChainProvider(spec, host=args.host, port=args.port, client_id=args.client_id)
        snapshot, result = service.capture_snapshot(provider, spec)
        print(summarize(result))
        print(f"Directory: {repository.run_dir(snapshot.run_id)}")
        return 0
    if args.action == "analyze":
        result = service.analyze_offline(args.run_id)
        print(summarize(result))
        print(f"Report: {repository.run_dir(args.run_id) / 'report.csv'}")
        return 0
    manifest = repository.load_manifest(args.run_id)
    print(f"Run: {args.run_id}")
    print(f"Status: {manifest['status']}")
    print(f"Events: {manifest['collected_event_count']}")
    print(f"Contracts: {manifest['selected_contract_count']}")
    print(f"Quality issues: {manifest['quality_issue_count']}")
    print(f"Offline analyzable: {manifest['offline_analyzable']}")
    print(f"Directory: {repository.run_dir(args.run_id)}")
    report = repository.run_dir(args.run_id) / "report.csv"
    print(f"Report: {report if report.exists() else 'not generated'}")
    if manifest.get("error_message"):
        print(f"Error ({manifest.get('error_stage')}): {manifest['error_message']}")
    return 0
