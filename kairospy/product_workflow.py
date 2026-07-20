from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import signal
from threading import Event
from types import SimpleNamespace

from kairospy.application import (
    GovernedStrategyRunLoop, RunArtifactRepository, build_simulated_spot_catalog,
    build_run_attribution,run_sma_historical_simulation,
)
from kairospy.contracts import canonicalize_market_event
from kairospy.data import OutputFormat, DatasetClient, RunMode
from kairospy.domain.identity import AccountKey, AccountType, AssetId, InstitutionId, InstrumentId
from kairospy.domain.market_data import Bar
from kairospy.features import FactorRegistry, SmaFactorConfig, SmaFactorRuntime, batch_sma_factors, snapshots_hash
from kairospy.market_data import IterableEventSource, MarketEventEnvelope, MarketEventType
from kairospy.market_data import CanonicalCaptureWriter, CapturedCanonicalEventSource
from kairospy.contracts import BarPayload
from kairospy.ports import Environment
from kairospy.connectors.binance.rest_transport import BinanceTransport, UrllibBinanceTransport
from kairospy.orchestration.runtime_store import SQLiteRuntimeStore
from kairospy.study_platform import (
    SMA_TUTORIAL_RELEASE_ID, StudyWorkspace, StudyWorkspaceRepository,
    ensure_sma_tutorial_dataset, open_study,
)
from kairospy.study_platform.tutorial_data import tutorial_sma_bars
from kairospy.storage.codec import to_primitive
from kairospy.strategies import (
    GovernedStrategyRuntime, SmaCrossStrategy, SmaCrossStrategyConfig, StrategyContext,
    StrategyImplementation, StrategyRegistry,
)
from kairospy.strategies.sma_cross_study_backtest import BarSeries, SmaCrossConfig, backtest_sma_cross
from kairospy.strategies.specs import sma_strategy_spec
from kairospy.strategies.specs import register_builtin_strategies
from kairospy.backtest.reference_scenarios import run_reference_scenario
from kairospy.strategies.btc_iron_condor import BtcIronCondorStrategy
from kairospy.strategies.promotion import evaluate_promotion_artifacts
from kairospy.features import OptionFearCoolingFactorRuntime
from kairospy.reference import ReferenceCatalog
from kairospy.execution.policy import ExecutionMode,ExecutionPolicy
from kairospy.execution.calibration import load_execution_calibration_release
from kairospy.domain.capability import TimeInForce
from kairospy.domain.product import ProductType
from kairospy.domain.strategy_contract import StrategyLifecycle, StrategySpec
from kairospy.strategies.registry import PromotionEvidence
from kairospy.cli_progress import TerminalProgressMatrix


def create_study(args) -> dict[str, object]:
    metadata = _study_input_metadata(args)
    path = StudyWorkspaceRepository(args.lake_root).create(StudyWorkspace(
        args.study_id, args.version, args.hypothesis, metadata["input_release"],
        metadata["input_hash"], metadata["primary_time"], metadata["start"], metadata["end"],
    ))
    return {"study_id": args.study_id, "version": args.version, "status": "sandbox", "workspace": str(path),
            **metadata, "next": f"./pyenv/bin/kairospy --lake-root {args.lake_root} study freeze {args.study_id}"}


def plan_governed_study(args) -> dict[str, object]:
    return _with_graceful_shutdown(lambda stop_event: _plan_governed_study(args, stop_event))


def _plan_governed_study(args, stop_event: Event) -> dict[str, object]:
    if _is_us_equity_momentum_study(args):
        return _plan_us_equity_momentum_study(args)
    from kairospy.connectors.binance.historical_archive import BinanceUsdmPerpetualHourlyArchiveProvider

    start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("study plan requires timezone-aware increasing [start,end) timestamps")
    archive = BinanceUsdmPerpetualHourlyArchiveProvider(
        progress=None if getattr(args, "quiet", False) else _archive_progress,
        stop_event=stop_event,
    )
    source_root = Path(args.lake_root) / "source"
    symbols = tuple(args.symbol) or archive.discover_symbols(source_root)
    plan = archive.acquisition_plan(symbols, start, end, source_root,
                                    actual_archives=not bool(args.symbol))
    plan.pop("records", None)
    if not getattr(args, "quiet", False):
        _archive_progress({"stage": "plan", "event": "complete", "mode": "plan-only", **plan})
    result = {
        "study_id": args.study_id, "dataset": args.dataset,
        "range": {"start": start.isoformat(), "end": end.isoformat(), "boundary": "[start,end)"},
        **{key: value for key, value in plan.items() if key != "matrix"},
        "next": f"./pyenv/bin/kairospy study start {args.study_id} --start {args.start} --end {args.end}",
    }
    if getattr(args, "format", "text") == "json":
        result["matrix"] = plan["matrix"]
    return result


def start_governed_study(args) -> dict[str, object]:
    if not getattr(args, "start", None) and not getattr(args, "end", None):
        result = start_sma_tutorial(SimpleNamespace(output_root=args.lake_root, study_id=args.study_id))
        _ensure_legacy_study_product_manifest(args)
        return result
    return _with_graceful_shutdown(lambda stop_event: _start_governed_study(args, stop_event))


def _ensure_legacy_study_product_manifest(args) -> None:
    path = Path(args.lake_root) / "studies" / args.study_id / "study.json"
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "product": "study",
        "kind": "study.workspace",
        "id": args.study_id,
        "version": getattr(args, "version", "1.0.0"),
        "hypothesis": getattr(args, "hypothesis", "legacy study compatibility workspace"),
        "status": "draft",
        "data": {},
        "factors": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _with_graceful_shutdown(operation):
    stop_event = Event()
    previous = signal.getsignal(signal.SIGINT)
    interrupts = {"count": 0}

    def request_shutdown(_signum, _frame):
        interrupts["count"] += 1
        if interrupts["count"] == 1:
            stop_event.set()
            _archive_progress({"stage": "shutdown", "event": "requested"})
        else:
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, request_shutdown)
    try:
        return operation(stop_event)
    finally:
        signal.signal(signal.SIGINT, previous)


def _start_governed_study(args, stop_event: Event) -> dict[str, object]:
    if _is_us_equity_momentum_study(args):
        return _start_us_equity_momentum_study(args)
    """Acquire and govern a Dataset Release, then bind and scaffold a Study."""
    from kairospy.data.bootstrap import default_provider_registry, register_default_products
    from kairospy.data.acquisition import AcquisitionLimits
    from kairospy.data.quality import DatasetQualityService

    if not getattr(args, "start", None) or not getattr(args, "end", None):
        raise ValueError("study start requires both --start and --end for governed data acquisition")
    start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("study start requires timezone-aware increasing [start,end) timestamps")
    register_default_products(args.lake_root)
    client = DatasetClient(
        args.lake_root, providers=default_provider_registry(
            args.lake_root, progress=None if getattr(args, "quiet", False) else _archive_progress,
            stop_event=stop_event,
        ),
        acquisition_limits=AcquisitionLimits(maximum_requests=100_000),
    )
    plan = client.plan(args.dataset, start=start, end=end, provider="binance", venue="binance")
    acquired = not plan.complete
    if not args.symbol:
        try:
            current = client.catalog.release(args.dataset)
        except KeyError:
            current = None
        lineage = client.metadata(current.release_id).get("lineage", {}) if current is not None else {}
        universe = lineage.get("universe", {}) if isinstance(lineage, dict) else {}
        if current is None or not plan.complete or not isinstance(universe, dict) or universe.get("kind") != "full-market":
            acquired = True
            release = client.acquire(plan, refresh=True)
        else:
            release = current
    else:
        release = client.acquire(plan, instruments=tuple(args.symbol)) if acquired else client.catalog.release(args.dataset)
    assessment = DatasetQualityService(args.lake_root).assess(release.release_id)
    if not assessment.passed:
        failed = [item.name for item in assessment.checks if not item.passed]
        if plan.complete and not args.symbol and failed == ["deterministic_order"]:
            acquired = True
            release = client.acquire(plan, refresh=True)
            assessment = DatasetQualityService(args.lake_root).assess(release.release_id)
            failed = [item.name for item in assessment.checks if not item.passed]
        if assessment.passed:
            failed = []
    if not assessment.passed:
        raise RuntimeError(f"dataset release {release.release_id} failed quality gates: {', '.join(failed)}")
    if not release.content_hash:
        raise ValueError(f"dataset release {release.release_id} has no frozen content hash")
    product = client.catalog.product(release.product_key)
    study_start, study_end = start, end
    if product.primary_time == "available_time" and product.dimensions.get("frequency") == "1h":
        study_start, study_end = start + timedelta(hours=1), end + timedelta(hours=1)
    workspace = StudyWorkspace(
        args.study_id, args.version, args.hypothesis, release.release_id, release.content_hash,
        product.primary_time, study_start.isoformat(), study_end.isoformat(),
    )
    repository = StudyWorkspaceRepository(args.lake_root)
    workspace_path = Path(args.lake_root) / "study-workspaces" / args.study_id / args.version / "workspace.json"
    if workspace_path.exists():
        current = repository.load(args.study_id, args.version)
        if current.candidate_hash != workspace.candidate_hash:
            raise ValueError(f"Study version already exists with different semantics: {workspace_path}")
    else:
        workspace_path = repository.create(workspace)
    script = open_study(args.study_id, root=args.lake_root, version=args.version).scaffold()
    return {
        "study_id": args.study_id, "version": args.version, "status": "sandbox",
        "dataset": str(release.product_key), "release_id": release.release_id,
        "content_hash": release.content_hash, "acquired": acquired,
        "quality_level": assessment.level.value, "quality_passed": assessment.passed,
        "symbols": list(args.symbol) if args.symbol else "full-market",
        "data_range": {"start": start.isoformat(), "end": end.isoformat(), "time": "period_start",
                       "boundary": "[start,end)"},
        "study_range": {"start": study_start.isoformat(), "end": study_end.isoformat(),
                        "time": product.primary_time, "boundary": "[start,end)"},
        "workspace": str(workspace_path), "script": str(script),
        "next": f"./pyenv/bin/python {script}",
    }


def _is_us_equity_momentum_study(args) -> bool:
    return (
        str(getattr(args, "dataset", "")) == "features.momentum.equity.us.1d"
        or str(getattr(args, "study_id", "")) == "us-equity-momentum"
    )


def _plan_us_equity_momentum_study(args) -> dict[str, object]:
    from kairospy.data.bootstrap import register_default_products
    from kairospy.data.catalog import DataCatalog

    start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("study plan requires timezone-aware increasing [start,end) timestamps")
    register_default_products(args.lake_root)
    catalog = DataCatalog(args.lake_root)
    releases = []
    missing = []
    for logical_key in _us_equity_momentum_snapshot_keys(catalog):
        try:
            release = catalog.release(logical_key)
            releases.append({
                "logical_key": logical_key,
                "release_id": release.release_id,
                "content_hash": release.content_hash,
                "quality_level": release.quality_level.value,
                "status": release.status.value,
            })
        except KeyError:
            missing.append(logical_key)
    return {
        "study_id": args.study_id,
        "dataset": "features.momentum.equity.us.1d",
        "range": {"start": start.isoformat(), "end": end.isoformat(), "boundary": "[start,end)"},
        "ready": not missing,
        "required_releases": releases,
        "missing_releases": missing,
        "next": (
            f"./pyenv/bin/kairospy --lake-root {args.lake_root} study start {args.study_id} "
            f"--dataset features.momentum.equity.us.1d --start {args.start} --end {args.end}"
        ),
    }


def _start_us_equity_momentum_study(args) -> dict[str, object]:
    from kairospy.data.bootstrap import register_default_products
    from kairospy.data.quality import DatasetQualityService

    start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("study start requires timezone-aware increasing [start,end) timestamps")
    register_default_products(args.lake_root)
    client = DatasetClient(args.lake_root)
    release = client.catalog.release("features.momentum.equity.us.1d")
    quality = DatasetQualityService(args.lake_root)
    snapshot = _us_equity_momentum_input_snapshot(client, require_corporate_action_match=True)
    input_assessments = {
        item["logical_key"]: quality.assess(str(item["release_id"]))
        for item in snapshot
    }
    failed_inputs = _failed_us_equity_momentum_inputs(input_assessments)
    if failed_inputs:
        raise RuntimeError(f"US equity momentum input releases failed quality gates: {', '.join(failed_inputs)}")
    assessment = input_assessments["features.momentum.equity.us.1d"]
    if not assessment.passed or assessment.level.value not in {"Q3", "Q4"}:
        failed = [item.name for item in assessment.checks if not item.passed]
        raise RuntimeError(
            f"US equity momentum feature release {release.release_id} is not Q3-ready: {', '.join(failed) or assessment.level.value}"
        )
    release = client.catalog.release(release.release_id)
    if not release.content_hash:
        raise ValueError(f"dataset release {release.release_id} has no frozen content hash")
    product = client.catalog.product(release.product_key)
    workspace = StudyWorkspace(
        args.study_id, args.version, _us_equity_hypothesis(args.hypothesis), release.release_id, release.content_hash,
        product.primary_time, start.isoformat(), end.isoformat(),
    )
    repository = StudyWorkspaceRepository(args.lake_root)
    workspace_path = Path(args.lake_root) / "study-workspaces" / args.study_id / args.version / "workspace.json"
    if workspace_path.exists():
        current = repository.load(args.study_id, args.version)
        if current.candidate_hash != workspace.candidate_hash:
            raise ValueError(f"Study version already exists with different semantics: {workspace_path}")
    else:
        workspace_path = repository.create(workspace)
    snapshot_path = workspace_path.with_name("input_releases.json")
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    script = open_study(args.study_id, root=args.lake_root, version=args.version).scaffold()
    return {
        "study_id": args.study_id,
        "version": args.version,
        "status": "sandbox",
        "dataset": str(release.product_key),
        "release_id": release.release_id,
        "content_hash": release.content_hash,
        "quality_level": assessment.level.value,
        "quality_passed": assessment.passed,
        "input_releases": snapshot,
        "study_range": {"start": start.isoformat(), "end": end.isoformat(), "time": product.primary_time, "boundary": "[start,end)"},
        "workspace": str(workspace_path),
        "script": str(script),
        "next": f"./pyenv/bin/python {script}",
    }


def _us_equity_momentum_input_snapshot(
    client: DatasetClient, *, require_corporate_action_match: bool = False,
) -> list[dict[str, object]]:
    snapshot = []
    for logical_key in (
        "market.returns.equity.us.1d",
        "market.universe.equity.us.1d",
        "features.liquidity.equity.us.1d",
        "features.momentum.equity.us.1d",
    ):
        release = client.catalog.release(logical_key)
        snapshot.append({
            "logical_key": logical_key,
            "release_id": release.release_id,
            "content_hash": release.content_hash,
            "quality_level": release.quality_level.value,
            "status": release.status.value,
        })
    action_release = _matched_us_equity_corporate_action_release(
        client, require=require_corporate_action_match,
    )
    if action_release is not None:
        snapshot.append({
            "logical_key": "reference.corporate_actions.equity.us.massive",
            "release_id": action_release.release_id,
            "content_hash": action_release.content_hash,
            "quality_level": action_release.quality_level.value,
            "status": action_release.status.value,
        })
    identity_release = _matched_us_equity_identity_release(client, require=require_corporate_action_match)
    if identity_release is not None:
        snapshot.append({
            "logical_key": "reference.identity.equity.us.massive",
            "release_id": identity_release.release_id,
            "content_hash": identity_release.content_hash,
            "quality_level": identity_release.quality_level.value,
            "status": identity_release.status.value,
        })
    return snapshot


def _us_equity_momentum_snapshot_keys(catalog) -> tuple[str, ...]:
    base = (
        "market.returns.equity.us.1d",
        "market.universe.equity.us.1d",
        "features.liquidity.equity.us.1d",
        "features.momentum.equity.us.1d",
    )
    try:
        catalog.release("reference.corporate_actions.equity.us.massive")
    except KeyError:
        action = ()
    else:
        action = ("reference.corporate_actions.equity.us.massive",)
    try:
        catalog.release("reference.identity.equity.us.massive")
    except KeyError:
        identity = ()
    else:
        identity = ("reference.identity.equity.us.massive",)
    return (*base, *action, *identity)


def _matched_us_equity_corporate_action_release(client: DatasetClient, *, require: bool):
    try:
        returns = client.catalog.release("market.returns.equity.us.1d")
    except KeyError:
        return None
    lineage_path = Path(client.root) / returns.relative_path / "lineage.json"
    if not lineage_path.exists():
        if require:
            raise RuntimeError(f"US equity returns release {returns.release_id} has no lineage.json")
        return None
    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    source = lineage.get("source") if isinstance(lineage, dict) else None
    used_hash = source.get("corporate_actions_sha256") if isinstance(source, dict) else None
    if not used_hash:
        return None
    candidates = [
        item for item in getattr(client.catalog, "_releases", {}).values()
        if str(item.product_key) == "reference.corporate_actions.equity.us.massive"
        and item.content_hash == used_hash
    ]
    if candidates:
        return sorted(candidates, key=lambda item: (item.published_at or "", item.release_id))[-1]
    if require:
        raise RuntimeError(
            f"US equity returns release {returns.release_id} uses corporate action hash {used_hash}, "
            "but no matching reference.corporate_actions.equity.us.massive release is registered"
        )
    return None


def _matched_us_equity_identity_release(client: DatasetClient, *, require: bool):
    try:
        universe = client.catalog.release("market.universe.equity.us.1d")
    except KeyError:
        return None
    lineage_path = Path(client.root) / universe.relative_path / "lineage.json"
    if not lineage_path.exists():
        if require:
            raise RuntimeError(f"US equity universe release {universe.release_id} has no lineage.json")
        return None
    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    source = lineage.get("source") if isinstance(lineage, dict) else None
    used_hash = source.get("reference_sha256") if isinstance(source, dict) else None
    if not used_hash:
        return None
    candidates = [
        item for item in getattr(client.catalog, "_releases", {}).values()
        if str(item.product_key) == "reference.identity.equity.us.massive"
        and item.content_hash == used_hash
    ]
    if candidates:
        return sorted(candidates, key=lambda item: (item.published_at or "", item.release_id))[-1]
    if require:
        raise RuntimeError(
            f"US equity universe release {universe.release_id} uses identity reference hash {used_hash}, "
            "but no matching reference.identity.equity.us.massive release is registered"
        )
    return None


def _failed_us_equity_momentum_inputs(input_assessments: dict[str, object]) -> list[str]:
    failed = []
    for logical_key, assessment in input_assessments.items():
        target = {"Q3", "Q4"}
        if logical_key in {"reference.corporate_actions.equity.us.massive", "reference.identity.equity.us.massive"}:
            target = {"Q2", "Q3", "Q4"}
        if not assessment.passed or assessment.level.value not in target:
            failed.append(f"{logical_key}({assessment.level.value})")
    return failed


def _us_equity_hypothesis(value: str | None) -> str:
    default = "US equities with stronger point-in-time cross-sectional momentum may outperform weaker eligible equities over subsequent holding windows"
    if not value or not value.strip():
        return default
    crypto_default = "At each hour, idiosyncratic moves are concentrated in a minority of crypto perpetuals"
    if value.startswith(crypto_default):
        return default
    return value


_ARCHIVE_MATRIX: TerminalProgressMatrix | None = None
_ARCHIVE_CELL_COMPLETED: dict[tuple[str, str], int] = {}
_ARCHIVE_CELL_PLANNED: dict[tuple[str, str], int] = {}
_ARCHIVE_CELL_CACHED: dict[tuple[str, str], int] = {}


def _archive_progress(event: dict[str, object]) -> None:
    """Human progress goes to stderr so --format json remains machine-readable on stdout."""
    global _ARCHIVE_MATRIX, _ARCHIVE_CELL_COMPLETED, _ARCHIVE_CELL_PLANNED, _ARCHIVE_CELL_CACHED
    stage, kind = event.get("stage"), event.get("event")
    if stage == "index" and kind == "start":
        _ARCHIVE_MATRIX = TerminalProgressMatrix(
            "Binance official archive index", ("Index",), ("Status",),
        )
        _ARCHIVE_MATRIX.set_cell("Index", "Status", f"loading {event.get('kind', 'archive')} keys")
        _ARCHIVE_MATRIX.set_footer("Planning only from ZIP objects actually published by Binance")
        _ARCHIVE_MATRIX.render(force=True)
        return
    if stage == "index" and kind == "progress" and _ARCHIVE_MATRIX:
        _ARCHIVE_MATRIX.set_cell("Index", "Status", f"{event['completed']}/{event['total']}")
        _ARCHIVE_MATRIX.set_footer(f"Discovered actual files: {event['records']}")
        _ARCHIVE_MATRIX.render()
        return
    if stage == "index" and kind == "page" and _ARCHIVE_MATRIX:
        _ARCHIVE_MATRIX.set_cell("Index", "Status", f"page {event['page']}")
        _ARCHIVE_MATRIX.set_footer(f"Official object keys scanned: {event['keys']}")
        _ARCHIVE_MATRIX.render()
        return
    if stage == "index" and kind in {"complete", "stale-cache"} and _ARCHIVE_MATRIX:
        _ARCHIVE_MATRIX.set_cell("Index", "Status", str(kind))
        _ARCHIVE_MATRIX.set_footer(f"Official archive records: {event['records']}")
        _ARCHIVE_MATRIX.render(final=True)
        return
    if stage == "shutdown" and kind == "requested" and _ARCHIVE_MATRIX:
        _ARCHIVE_MATRIX.set_footer(*_ARCHIVE_MATRIX.footer[:2],
                                   "Shutdown requested: no new files will start; waiting for in-flight files")
        _ARCHIVE_MATRIX.render(final=True)
        return
    if stage == "plan" and kind == "complete":
        matrix = event.get("matrix", [])
        years = tuple(str(value) for value in sorted({int(item["year"]) for item in matrix}))
        columns = tuple(f"{month:02d}" for month in range(1, 13))
        _ARCHIVE_MATRIX = TerminalProgressMatrix(
            ("Binance USD-M perpetual 1h local coverage (cached / official files)"
             if event.get("mode") == "plan-only" else
             "Binance USD-M perpetual 1h acquisition progress (processed / official files)"),
            years, columns,
        )
        _ARCHIVE_CELL_COMPLETED = {}
        _ARCHIVE_CELL_PLANNED = {}
        _ARCHIVE_CELL_CACHED = {}
        for item in matrix:
            row, column = str(item["year"]), f"{int(item['month']):02d}"
            key = row, column
            planned = int(item["tasks"])
            cached = int(item["cached_monthly"]) + int(item["cached_daily_files"])
            _ARCHIVE_CELL_COMPLETED[(row, column)] = 0
            _ARCHIVE_CELL_PLANNED[key] = planned
            _ARCHIVE_CELL_CACHED[key] = cached
            value = f"{cached}/{planned}" if event.get("mode") == "plan-only" else f"0/{planned}"
            _ARCHIVE_MATRIX.set_cell(row, column, value)
        _ARCHIVE_MATRIX.set_footer(
            f"Plan: universe_symbols={event['symbols']} planned_symbols={event.get('planned_symbols', event['symbols'])} "
            f"months={event['months']} actual_files={event['total_tasks']}",
            f"Raw cache: monthly={event['cached_monthly']} daily_files={event['cached_daily_files']} "
            f"uncached_files={event.get('uncached_files', 0)}",
            ("Status: plan only; no bars downloaded" if event.get("mode") == "plan-only"
             else "Status: planned; starting download"),
        )
        _ARCHIVE_MATRIX.render(force=True)
        return
    if stage == "download" and kind == "start":
        if _ARCHIVE_MATRIX:
            _ARCHIVE_MATRIX.set_footer(*_ARCHIVE_MATRIX.footer[:2], "Status: downloading")
            _ARCHIVE_MATRIX.render()
        return
    if stage == "download" and kind == "progress":
        if _ARCHIVE_MATRIX:
            key = str(event["year"]), f"{int(event['month']):02d}"
            _ARCHIVE_CELL_COMPLETED[key] = _ARCHIVE_CELL_COMPLETED.get(key, 0) + 1
            planned = _ARCHIVE_CELL_PLANNED.get(key, 0)
            _ARCHIVE_MATRIX.set_cell(*key, f"{_ARCHIVE_CELL_COMPLETED[key]}/{planned}")
            completed, total = int(event["completed"]), int(event["total"])
            percent = completed / total * 100 if total else 100
            _ARCHIVE_MATRIX.set_footer(
                _ARCHIVE_MATRIX.footer[0],
                f"Progress: {completed}/{total} ({percent:.1f}%) downloaded={event['downloaded']} "
                f"cached={event['cached']} unavailable={event['unavailable']} failed={event['failed']}",
                f"Rows={event['rows']} current={event['current']} status={event['status']}",
            )
            _ARCHIVE_MATRIX.render(final=completed == total)
        return
    if stage == "download" and kind == "complete":
        if _ARCHIVE_MATRIX:
            _ARCHIVE_MATRIX.set_footer(
                _ARCHIVE_MATRIX.footer[0],
                f"Raw complete: downloaded={event['downloaded']} cached={event['cached']} "
                f"unavailable={event['unavailable']} failed={event['failed']}",
                f"Rows={event['rows']}; preparing Canonical data",
            )
            _ARCHIVE_MATRIX.render(final=True)
        return
    if stage == "organize" and kind == "start":
        if _ARCHIVE_MATRIX:
            _ARCHIVE_MATRIX.set_footer(*_ARCHIVE_MATRIX.footer[:2],
                                       f"Status: organizing {event['raw_rows']} rows into Canonical Parquet")
            _ARCHIVE_MATRIX.render(final=True)
        return
    if stage == "organize" and kind == "complete":
        if _ARCHIVE_MATRIX:
            _ARCHIVE_MATRIX.set_footer(*_ARCHIVE_MATRIX.footer[:2],
                                       f"Complete: Release {event['release_id']} rows={event['rows']}")
            _ARCHIVE_MATRIX.render(final=True)


def start_sma_tutorial(args) -> dict[str, object]:
    """Create the smallest safe first-use workspace without exposing governance plumbing."""
    root = Path(args.output_root)
    release = ensure_sma_tutorial_dataset(root)
    bars = tutorial_sma_bars()
    workspace = StudyWorkspace(
        args.study_id, "1.0.0",
        "When the 5-hour SMA is above the 15-hour SMA, the next-period direction may be positive",
        release.release_id, str(release.content_hash), "available_time",
        bars[0].end.isoformat(), (bars[-1].end + timedelta(hours=1)).isoformat(),
    )
    repository = StudyWorkspaceRepository(root)
    target = root/"study-workspaces"/args.study_id/"1.0.0"/"workspace.json"
    if target.exists():
        current = open_study(args.study_id, root=root, version="1.0.0").workspace
        if current.candidate_hash != workspace.candidate_hash:
            raise ValueError(f"tutorial study already exists with different semantics: {target}")
        path = target
        created = False
    else:
        path = repository.create(workspace)
        created = True
    command = f"./pyenv/bin/kairospy --lake-root {root} study data {args.study_id} --head 10"
    return {
        "tutorial": "sma", "created": created, "root": str(root), "study_id": args.study_id,
        "status": "sandbox", "hypothesis": workspace.hypothesis,
        "dataset": workspace.input_release_id, "input_hash": workspace.input_content_hash,
        "range": {"start": workspace.start, "end": workspace.end, "boundary": "[start,end)"},
        "workspace": str(path), "lesson": "inspect and profile the bound Dataset Release before writing a factor",
        "next": command,
    }


def inspect_study(args) -> dict[str, object]:
    return open_study(args.study_id, root=args.lake_root, version=args.version).describe()


def preview_study_data(args) -> dict[str, object]:
    if args.head < 1:
        raise ValueError("--head must be positive")
    session = open_study(args.study_id, root=args.lake_root, version=args.version)
    columns = tuple(args.column or ())
    if not columns:
        fields = tuple(session.describe()["fields"])
        preferred = ("available_time", "open", "high", "low", "close", "volume")
        columns = tuple(name for name in preferred if name in fields)
    rows = session.data.rows(columns=columns or None)
    selected = rows[:args.head]
    return {
        "study_id": args.study_id, "version": args.version, "dataset": session.workspace.input_release_id,
        "shown": len(selected), "total": len(rows), "columns": tuple(selected[0]) if selected else (),
        "rows": selected,
        "next": f"./pyenv/bin/kairospy --lake-root {args.lake_root} study profile {args.study_id}",
    }


def profile_study(args) -> dict[str, object]:
    session = open_study(args.study_id, root=args.lake_root, version=args.version)
    return {
        "study_id": args.study_id, "version": args.version, "dataset": session.workspace.input_release_id,
        **session.profile().as_dict(),
        "next": f"./pyenv/bin/kairospy --lake-root {args.lake_root} study scaffold {args.study_id}",
    }


def scaffold_study(args) -> dict[str, object]:
    session = open_study(args.study_id, root=args.lake_root, version=args.version)
    path = session.scaffold()
    return {
        "study_id": args.study_id, "version": args.version, "script": str(path),
        "next": f"./pyenv/bin/python {path}",
    }


def freeze_study(args) -> dict[str, object]:
    directory = StudyWorkspaceRepository(args.lake_root).freeze(args.study_id, args.version)
    payload = json.loads((directory/"study_candidate.json").read_text(encoding="utf-8"))
    return {"study_id": args.study_id, "version": args.version, "status": payload["status"],
            "candidate_hash": payload["candidate_hash"], "directory": str(directory),
            "next": "register a Factor Release only after recording the study evidence"}


def register_sma_factor(args) -> dict[str, object]:
    runtime = SmaFactorRuntime(
        SmaFactorConfig(args.fast, args.slow), input_identity=args.input_identity,
        factor_id=args.factor_id, version=args.version,
    )
    directory = FactorRegistry(Path(args.lake_root)/"factors").register(runtime.spec)
    return {"factor_id": runtime.spec.factor_id, "version": runtime.spec.version,
            "factor_spec_hash": runtime.spec.spec_hash, "directory": str(directory)}


def verify_sma_factor(args) -> dict[str, object]:
    identity, bars = load_bars(args)
    config = SmaFactorConfig(args.fast, args.slow)
    batch = batch_sma_factors(bars, config, input_identity=identity)
    runtime = SmaFactorRuntime(config, input_identity=identity)
    replay = tuple(snapshot for event in canonical_bar_events(bars)
                   if (snapshot := runtime.update(event)) is not None)
    return {"input_identity": identity, "bars": len(bars), "batch_replay_equal": batch == replay,
            "factor_hash": snapshots_hash(batch), "ready": sum(item.quality.value == "ready" for item in batch)}


def register_sma_strategy(args) -> dict[str, object]:
    config = SmaCrossConfig(args.fast, args.slow, Decimal("100000"), args.fee_bps)
    spec, policy = sma_strategy_spec(config); spec = replace(spec, version=args.version)
    factor = SmaFactorRuntime(
        SmaFactorConfig(args.fast, args.slow), input_identity=args.input_identity,
        factor_id=args.factor_id, version=args.factor_version,
    ).spec
    source = Path(__file__).parent/"strategies"/"sma_cross_strategy.py"
    implementation = StrategyImplementation(
        "kairospy.strategies.sma_cross_strategy:SmaCrossStrategy", sha256(source.read_bytes()).hexdigest(),
    )
    directory = StrategyRegistry(Path(args.lake_root)/"strategies").register(
        spec, policy, implementation=implementation, factor_specs=(factor,),
    )
    return {"strategy_id": spec.strategy_id, "version": spec.version, "strategy_spec_hash": spec.spec_hash,
            "factor_spec_hash": factor.spec_hash, "execution_policy_id": policy.policy_id,
            "directory": str(directory)}


def register_builtin_strategy_releases(args)->dict[str,object]:
    root=Path(args.lake_root)/"strategies";paths=register_builtin_strategies(root);registry=StrategyRegistry(root)
    releases=[]
    for path in paths:
        release=registry.load(path.parent.name,path.name)
        releases.append({"strategy_id":release.strategy_id,"version":release.version,"directory":str(path),
            "implementation":release.implementation.import_path,"factor_bindings":list(release.factor_bindings)})
    return {"count":len(releases),"releases":releases}


def register_btc_iron_condor_candidate(args)->dict[str,object]:
    strategy=BtcIronCondorStrategy(study_spec_hash=args.study_spec_hash);spec=strategy.strategy_spec
    policy=ExecutionPolicy(strategy.config.execution_policy_id,"1.0.0",ExecutionMode.TAKER,TimeInForce.IOC,Decimal("15"),
        order_latency_ms=250,slippage_model="top_of_book",fee_schedule="governed")
    source=Path(__file__).parent/"strategies"/"btc_iron_condor.py";implementation=StrategyImplementation(
        "kairospy.strategies.btc_iron_condor:BtcIronCondorStrategy",sha256(source.read_bytes()).hexdigest())
    factor=OptionFearCoolingFactorRuntime(ReferenceCatalog(),input_identity="runtime-bound").spec
    directory=StrategyRegistry(Path(args.lake_root)/"strategies").register(spec,policy,implementation=implementation,factor_specs=(factor,))
    return {"strategy_id":spec.strategy_id,"version":spec.version,"strategy_spec_hash":spec.spec_hash,
        "factor_spec_hash":factor.spec_hash,"directory":str(directory),"lifecycle":spec.lifecycle.value}


def inspect_strategy_release(args)->dict[str,object]:
    release=StrategyRegistry(Path(args.lake_root)/"strategies").load(args.strategy_id,args.version)
    return {"strategy_id":release.strategy_id,"version":release.version,"directory":str(release.directory),
        "strategy_spec":release.strategy_spec,"execution_policy":release.execution_policy,
        "implementation":to_primitive(release.implementation),"factor_bindings":list(release.factor_bindings)}


def strategy_release_status(args)->dict[str,object]:
    return to_primitive(StrategyRegistry(Path(args.lake_root)/"strategies").status(args.strategy_id,args.version))


def activate_strategy_release(args)->dict[str,object]:
    registry=StrategyRegistry(Path(args.lake_root)/"strategies");path=registry.activate(args.strategy_id,args.version,actor=args.actor,reason=args.reason)
    return {"strategy_id":args.strategy_id,"active_version":registry.active_version(args.strategy_id),"record":str(path)}


def rollback_strategy_release(args)->dict[str,object]:
    registry=StrategyRegistry(Path(args.lake_root)/"strategies");path=registry.rollback(args.strategy_id,actor=args.actor,reason=args.reason)
    return {"strategy_id":args.strategy_id,"active_version":registry.active_version(args.strategy_id),"record":str(path)}


def check_strategy_promotion(args)->dict[str,object]:
    registry=StrategyRegistry(Path(args.lake_root)/"strategies")
    release=registry.load(args.strategy_id,args.version)
    spec=_strategy_spec_from_registry_payload(release.strategy_spec)
    target=_strategy_lifecycle_arg(args.to)
    evidence_paths=tuple(Path(path) for path in args.evidence)
    results=tuple(json.loads(path.read_text(encoding="utf-8")) for path in evidence_paths)
    gate=evaluate_promotion_artifacts(target,results)
    transition_valid, transition_reason = _promotion_transition(spec, target)
    return {"strategy_id":args.strategy_id,"version":args.version,"current_status":spec.lifecycle.value,
        "target_status":target.value,"gate_passed":gate.passed,"gate_reasons":gate.reasons,
        "transition_valid":transition_valid,"transition_reason":transition_reason,
        "evidence_hashes":tuple(sha256(path.read_bytes()).hexdigest() for path in evidence_paths),
        "would_promote":gate.passed and transition_valid}


def promote_strategy_release(args)->dict[str,object]:
    registry=StrategyRegistry(Path(args.lake_root)/"strategies")
    release=registry.load(args.strategy_id,args.version)
    spec=_strategy_spec_from_registry_payload(release.strategy_spec)
    target=_strategy_lifecycle_arg(args.to)
    transition_valid, transition_reason = _promotion_transition(spec, target)
    if not transition_valid:
        raise ValueError(f"strategy promotion transition failed: {transition_reason}")
    results=tuple(json.loads(Path(path).read_text(encoding="utf-8")) for path in args.evidence)
    gate=evaluate_promotion_artifacts(target,results)
    if not gate.passed:
        raise ValueError(f"strategy promotion gate failed: {', '.join(gate.reasons)}")
    evidence_hashes=tuple(sha256(Path(path).read_bytes()).hexdigest() for path in args.evidence)
    evidence=PromotionEvidence(target,tuple(str(Path(path)) for path in args.evidence),evidence_hashes,
        args.actor,args.capital_limit,args.rollback_condition,datetime.now(timezone.utc).isoformat(),
        gate.passed,gate.reasons)
    promoted=registry.promote(spec,target,evidence)
    status=registry.status(args.strategy_id,args.version)
    return {"strategy_id":promoted.strategy_id,"version":promoted.version,"status":promoted.lifecycle.value,
        "strategy_spec_hash":promoted.spec_hash,"gate_passed":gate.passed,"gate_reasons":gate.reasons,
        "evidence_hashes":evidence_hashes,"next_promotion":status.next_promotion,
        "evidence_bundle":status.latest_promotion_bundle,"directory":str(release.directory)}


def _promotion_transition(spec: StrategySpec, target: StrategyLifecycle) -> tuple[bool, str | None]:
    try:
        spec.promote(target)
    except ValueError as error:
        return False, str(error)
    return True, None


def _strategy_lifecycle_arg(value: str) -> StrategyLifecycle:
    return StrategyLifecycle(value)


def _strategy_spec_from_registry_payload(payload: dict[str,object]) -> StrategySpec:
    values={key:value for key,value in payload.items() if key!="strategy_spec_hash"}
    for key in ("products",):
        values[key]=tuple(ProductType(item) for item in values[key])
    values["lifecycle"]=StrategyLifecycle(values["lifecycle"])
    values["risk_budget_fraction"]=Decimal(str(values["risk_budget_fraction"]))
    for key in (
        "strategy_archetypes","return_drivers","risk_drivers","features","entry_rules",
        "exit_rules","rebalance_rules","required_data_capabilities","required_execution_capabilities",
    ):
        values[key]=tuple(values[key])
    for key in ("universe","signal","portfolio_construction"):
        values[key]=tuple(tuple(item) for item in values[key])
    return StrategySpec(**values)


def run_strategy_backtest_workflow(args)->dict[str,object]:
    strategy=args.strategy.split("@",1)[0]
    if strategy!="sma-cross-v1":
        raise ValueError(f"run backtest currently supports sma-cross-v1, got {args.strategy!r}")
    return run_sma_backtest_workflow(args)


def run_reference_strategy_workflow(args)->dict[str,object]:
    conservative=run_reference_scenario(args.strategy,"conservative");stress=run_reference_scenario(args.strategy,"stress")
    return {"strategy":args.strategy,"conservative":to_primitive(conservative),"stress":to_primitive(stress),
        "stress_is_worse":stress.final_cash<conservative.final_cash,
        "replay_equal":conservative==run_reference_scenario(args.strategy,"conservative")}


def run_sma_backtest_workflow(args) -> dict[str, object]:
    identity, bars = load_bars(args)
    config = SmaCrossConfig(args.fast, args.slow, args.initial_cash, args.fee_bps)
    batch = backtest_sma_cross(BarSeries(identity, bars), config)
    governed = asyncio.run(_governed_run(identity, bars, args.fast, args.slow, args.initial_cash, args.fee_bps))
    calibration = _execution_calibration_binding(args)
    calibrated_result = _calibrated_sma_backtest_result(identity, bars, config, batch, calibration)
    artifact=RunArtifactRepository(_artifact_root(args)).write(mode="backtest",input_identity=identity,
        strategy_id="sma-cross-v1",strategy_version="1.2.0",
        config=_run_config(args),result=governed,execution={"driver":"batch-next-open","fill_model":"next-open-bar",
        "calibration":calibration,"calibrated_result":calibrated_result,
        "trades":len(batch.trades),"final_equity":str(batch.metrics["final_equity"])},
        attribution=build_run_attribution(governed,
        starting_equity=args.initial_cash,ending_equity=batch.metrics["final_equity"],orders=len(batch.trades),fills=len(batch.trades),
        fees=batch.metrics["commissions"]))
    return {"mode": "backtest", "input_identity": identity, "bars": len(bars),
            "trades": len(batch.trades), "final_equity": str(batch.metrics["final_equity"]),
            "calibrated_final_equity": calibrated_result.get("calibrated_final_equity"),
            "factor_hash": governed.factor_hash, "decision_hash": governed.decision_hash,
            "intent_hash": governed.intent_hash, "audit_hash": governed.audit_hash,
            "execution_calibration": calibration,"artifact":str(artifact.path)}


def run_sma_simulation_workflow(args) -> dict[str, object]:
    identity, bars = load_bars(args); instrument = bars[0].instrument_id
    account = AccountKey(InstitutionId("simulated"), args.account_id, AccountType.CRYPTO_SPOT)
    catalog = build_simulated_spot_catalog(
        instrument_id=instrument, account=account, base_asset=AssetId(args.base_asset),
        quote_asset=AssetId(args.quote_asset), effective_from=bars[0].start-timedelta(days=1),
    )
    result = asyncio.run(run_sma_historical_simulation(
        root=args.run_root, events=tuple(canonical_bar_events(bars)), catalog=catalog,
        instrument_id=instrument, account=account, cash_asset=AssetId(args.quote_asset),
        initial_cash=args.initial_cash, factor_config=SmaFactorConfig(args.fast, args.slow),
        fee_bps=args.fee_bps, input_identity=identity,
    ))
    artifact=RunArtifactRepository(_artifact_root(args)).write(mode="historical-simulation",input_identity=identity,
        strategy_id="sma-cross-v1",strategy_version="1.2.0",config=_run_config(args),result=result.strategy_run,
        execution={"driver":"simulated-venue","orders":result.orders,"fills":result.fills,
        "runtime_database":str(result.runtime_database),"restart_ready":result.restart_ready,
        "final_cash":str(result.final_cash),"final_position":str(result.final_position)},
        attribution=build_run_attribution(result.strategy_run,starting_equity=args.initial_cash,
        ending_equity=result.final_cash+result.final_position*bars[-1].close,
        orders=result.orders,fills=result.fills,fees=result.fees))
    return {"mode": "historical-simulation", "input_identity": identity, "bars": len(bars),
            "orders": result.orders, "fills": result.fills, "final_cash": str(result.final_cash),
            "final_position": str(result.final_position), "restart_ready": result.restart_ready,
            "runtime_database": str(result.runtime_database), "factor_hash": result.strategy_run.factor_hash,
            "decision_hash": result.strategy_run.decision_hash, "intent_hash": result.strategy_run.intent_hash,
            "audit_hash": result.strategy_run.audit_hash,"artifact":str(artifact.path)}


def run_sma_paper_workflow(args) -> dict[str, object]:
    capture = Path(args.capture) if args.capture else Path(args.run_root)/"capture"/"sma-bars.canonical.jsonl"
    market_data_source = "capture"
    if getattr(args, "live_binance_symbol", None) and getattr(args, "fixture", False):
        raise ValueError("run paper accepts only one market input source: --fixture or --live-binance-symbol")
    if getattr(args, "live_binance_symbol", None):
        market_data_source = "binance-public-klines"
        live_capture = _write_binance_spot_bar_capture(
            capture,
            symbol=args.live_binance_symbol,
            interval=getattr(args, "live_binance_interval", "1m"),
            limit=getattr(args, "live_binance_limit", 120),
            base_url=getattr(args, "live_binance_base_url", "https://data-api.binance.vision"),
        )
    if args.fixture and not capture.exists():
        market_data_source = "fixture"
        writer=CanonicalCaptureWriter(capture,session_id="sma-paper-fixture",source="fixture")
        for event in canonical_bar_events(fixture_sma_bars()):writer.append(event)
        writer.finalize()
    if not capture.exists():
        raise ValueError("run paper requires --capture, --fixture, or --live-binance-symbol")
    events=asyncio.run(_captured_events(capture));bars=tuple(_bar_from_event(event) for event in events)
    identity=f"capture:{sha256(capture.read_bytes()).hexdigest()}";instrument=bars[0].instrument_id
    account=AccountKey(InstitutionId("simulated"),args.account_id,AccountType.CRYPTO_SPOT)
    catalog=build_simulated_spot_catalog(instrument_id=instrument,account=account,
        base_asset=AssetId(args.base_asset),quote_asset=AssetId(args.quote_asset),effective_from=bars[0].start-timedelta(days=1))
    result=asyncio.run(run_sma_historical_simulation(root=args.run_root,events=events,catalog=catalog,
        instrument_id=instrument,account=account,cash_asset=AssetId(args.quote_asset),initial_cash=args.initial_cash,
        factor_config=SmaFactorConfig(args.fast,args.slow),fee_bps=args.fee_bps,input_identity=identity,
        mode="paper-trading",environment=Environment.PAPER))
    artifact=RunArtifactRepository(_artifact_root(args)).write(mode="paper-trading",input_identity=identity,
        strategy_id="sma-cross-v1",strategy_version="1.2.0",config=_run_config(args),result=result.strategy_run,
        execution={"driver":"simulated-paper-trading","market_data_source":market_data_source,
        "live_capture":live_capture if getattr(args, "live_binance_symbol", None) else None,
        "capture":str(capture),"orders":result.orders,
        "fills":result.fills,"runtime_database":str(result.runtime_database),"restart_ready":result.restart_ready},
        attribution=build_run_attribution(result.strategy_run,starting_equity=args.initial_cash,
        ending_equity=result.final_cash+result.final_position*bars[-1].close,
        orders=result.orders,fills=result.fills,fees=result.fees))
    return {"mode":"paper-trading","market_data_source":market_data_source,
        "capture":str(capture),"input_identity":identity,"bars":len(bars),
        "orders":result.orders,"fills":result.fills,"restart_ready":result.restart_ready,
        "runtime_database":str(result.runtime_database),"artifact":str(artifact.path),
        "factor_hash":result.strategy_run.factor_hash,"decision_hash":result.strategy_run.decision_hash,
        "intent_hash":result.strategy_run.intent_hash,"audit_hash":result.strategy_run.audit_hash}


def run_sma_shadow_workflow(args) -> dict[str, object]:
    capture = Path(args.capture) if args.capture else Path(args.run_root)/"capture"/"sma-bars.canonical.jsonl"
    if args.fixture and not capture.exists():
        writer=CanonicalCaptureWriter(capture,session_id="sma-shadow-fixture",source="fixture")
        for event in canonical_bar_events(fixture_sma_bars()):writer.append(event)
        writer.finalize()
    events=asyncio.run(_captured_events(capture));bars=tuple(_bar_from_event(event) for event in events)
    identity=f"capture:{sha256(capture.read_bytes()).hexdigest()}"
    result=asyncio.run(_governed_run(identity,bars,args.fast,args.slow,args.initial_cash,args.fee_bps))
    artifact=RunArtifactRepository(_artifact_root(args)).write(mode="shadow",input_identity=identity,
        strategy_id="sma-cross-v1",strategy_version="1.2.0",config=_run_config(args),result=result,
        execution={"driver":"no-execution-shadow","capture":str(capture),"orders":0,"fills":0,
        "hypothetical_intents":len(result.economic_intents),"submitted_orders":0},
        attribution=build_run_attribution(result,starting_equity=args.initial_cash,
        ending_equity=args.initial_cash,orders=0,fills=0,fees=Decimal("0")))
    return {"mode":"shadow","capture":str(capture),"input_identity":identity,"bars":len(bars),
        "hypothetical_intents":len(result.economic_intents),"orders":0,"fills":0,
        "submitted_orders":0,"artifact":str(artifact.path),
        "factor_hash":result.factor_hash,"decision_hash":result.decision_hash,
        "intent_hash":result.intent_hash,"audit_hash":result.audit_hash}


def _write_binance_spot_bar_capture(
    capture: Path, *, symbol: str, interval: str, limit: int, base_url: str,
    transport: BinanceTransport | None = None,
) -> dict[str, object]:
    if limit <= 0:
        raise ValueError("live Binance bar limit must be positive")
    transport = transport or UrllibBinanceTransport(base_url)
    rows = transport.request("GET", "/api/v3/klines", {"symbol": symbol.upper(), "interval": interval, "limit": limit})
    if not rows:
        raise ValueError(f"Binance returned no bars for {symbol} {interval}")
    writer = CanonicalCaptureWriter(capture, session_id=f"binance-live-bars-{symbol.lower()}-{interval}", source="binance")
    instrument = InstrumentId(f"crypto:binance:spot:{symbol.upper()}")
    for sequence, row in enumerate(rows):
        start = datetime.fromtimestamp(int(row[0]) / 1000, timezone.utc)
        end = datetime.fromtimestamp((int(row[6]) + 1) / 1000, timezone.utc)
        bar = Bar(
            instrument, start, end,
            Decimal(str(row[1])), Decimal(str(row[2])), Decimal(str(row[3])),
            Decimal(str(row[4])), Decimal(str(row[5])),
        )
        event = MarketEventEnvelope(
            bar.instrument_id, bar.start, bar.end, bar.end, "binance", "spot.klines",
            symbol.upper(), MarketEventType.BAR, sequence,
            {"period_start":bar.start,"period_end":bar.end,"open":bar.open,"high":bar.high,
             "low":bar.low,"close":bar.close,"volume":bar.volume},
            receive_time=bar.end,
        )
        writer.append(canonicalize_market_event(event, source_instance="binance-rest-klines"))
    writer.finalize()
    return {"capture": str(capture), "bars": len(rows), "symbol": symbol.upper(), "interval": interval}


def inspect_run(args) -> dict[str, object]:
    if getattr(args,"artifact",None):
        repository=RunArtifactRepository(Path(args.artifact).parents[2])
        return repository.explain(repository.load(args.artifact),at=args.at)
    store = SQLiteRuntimeStore(args.db); ledger = store.load_ledger()
    unresolved = store.unresolved_orders()
    return {"runtime_database": str(store.path), "transactions": len(ledger.transactions),
            "entries": len(ledger.entries), "unresolved_orders": [to_primitive(item) for item in unresolved]}


def replay_run_artifact(args) -> dict[str, object]:
    repository=RunArtifactRepository(Path(args.artifact).parents[2]);artifact=repository.load(args.artifact)
    config=artifact.payload["config"]
    args.fast=int(config["fast"]);args.slow=int(config["slow"]);args.initial_cash=Decimal(str(config["initial_cash"]))
    args.fee_bps=Decimal(str(config["fee_bps"]));identity,bars=load_bars(args)
    replay=asyncio.run(_governed_run(identity,bars,args.fast,args.slow,args.initial_cash,args.fee_bps))
    comparisons={name:artifact.payload[name]==getattr(replay,name) for name in
        ("factor_hash","decision_hash","intent_hash")}
    comparisons["strategy_run_audit_hash"]=artifact.payload["strategy_run_audit_hash"]==replay.audit_hash
    return {"artifact":str(args.artifact),"input_identity":identity,"comparisons":comparisons,
            "passed":all(comparisons.values()),"replay_audit_hash":replay.audit_hash}


def replay_capture_artifact(args) -> dict[str, object]:
    artifact=RunArtifactRepository(Path(args.artifact).parents[2]).load(args.artifact);config=artifact.payload["config"]
    events=asyncio.run(_captured_events(Path(args.capture)));bars=tuple(_bar_from_event(event) for event in events)
    identity=f"capture:{sha256(Path(args.capture).read_bytes()).hexdigest()}"
    replay=asyncio.run(_governed_run_from_events(identity,events,bars,int(config["fast"]),int(config["slow"]),
        Decimal(str(config["initial_cash"])),Decimal(str(config["fee_bps"]))))
    comparisons={name:artifact.payload[name]==getattr(replay,name) for name in ("factor_hash","decision_hash","intent_hash")}
    comparisons["strategy_run_audit_hash"]=artifact.payload["strategy_run_audit_hash"]==replay.audit_hash
    return {"capture":str(args.capture),"artifact":str(args.artifact),"comparisons":comparisons,
        "passed":all(comparisons.values()),"input_identity":identity}


def replay_sma_capture(args) -> dict[str, object]:
    return replay_capture_artifact(args)


async def _governed_run(identity, bars, fast, slow, capital, fee_bps=Decimal("10")):
    return await _governed_run_from_events(identity, tuple(canonical_bar_events(bars)), bars, fast, slow, capital, fee_bps)


async def _governed_run_from_events(identity, events, bars, fast, slow, capital, fee_bps=Decimal("10")):
    spec, policy = sma_strategy_spec(SmaCrossConfig(fast, slow, capital, fee_bps))
    return await GovernedStrategyRunLoop(
        IterableEventSource(tuple(events)),
        SmaFactorRuntime(SmaFactorConfig(fast, slow), input_identity=identity),
        GovernedStrategyRuntime(
            SmaCrossStrategy(SmaCrossStrategyConfig(bars[0].instrument_id)), spec,
            execution_policy_id=policy.policy_id,
        ), lambda market: StrategyContext(market, object(), (), object()),
        approved_capital=capital,
    ).run()


def load_bars(args) -> tuple[str, tuple[Bar, ...]]:
    if getattr(args, "fixture", False):
        release = ensure_sma_tutorial_dataset(args.lake_root)
        return release.release_id, _dataset_bars(args.lake_root, release.release_id, None, None)
    if not getattr(args, "dataset", None): raise ValueError("--dataset is required unless --fixture is used")
    return _dataset_bars(args.lake_root, args.dataset, args.start, args.end, include_release=True)


def _dataset_bars(lake_root, dataset, start, end, *, include_release=False):
    query = DatasetClient(lake_root, run_mode=RunMode.BACKTEST).get(
        dataset, start=start, end=end,
        fields=("instrument_id", "period_start", "period_end", "open", "high", "low", "close", "volume"),
    )
    rows = query.collect(OutputFormat.ROWS)
    bars = tuple(Bar(
        InstrumentId(str(row["instrument_id"])), _time(row["period_start"]), _time(row["period_end"]),
        Decimal(str(row["open"])), Decimal(str(row["high"])), Decimal(str(row["low"])),
        Decimal(str(row["close"])), Decimal(str(row["volume"])),
    ) for row in rows)
    if not bars: raise ValueError("selected dataset range contains no bars")
    return (query.release_id, bars) if include_release else bars


def fixture_sma_bars() -> tuple[Bar, ...]:
    return tutorial_sma_bars()


def _study_input_metadata(args) -> dict[str, str]:
    dataset = getattr(args, "dataset", None)
    if dataset:
        if dataset == SMA_TUTORIAL_RELEASE_ID:
            ensure_sma_tutorial_dataset(args.lake_root)
        client = DatasetClient(args.lake_root, run_mode=RunMode.STUDY)
        release = client.resolve(dataset)
        if not release.content_hash:
            raise ValueError(f"dataset release {release.release_id!r} has no frozen content hash")
        product = client.catalog.product(release.product_key)
        coverage = client.coverage(release.release_id).get("coverage", {})
        if isinstance(coverage.get("coverage"), dict):
            coverage = coverage["coverage"]
        inferred = {
            "input_release": release.release_id, "input_hash": release.content_hash,
            "primary_time": product.primary_time,
            "start": str(coverage.get("start") or ""), "end": str(coverage.get("end") or ""),
        }
        if release.release_id == SMA_TUTORIAL_RELEASE_ID:
            bars = tutorial_sma_bars()
            inferred["start"] = bars[0].end.isoformat()
            inferred["end"] = (bars[-1].end + timedelta(hours=1)).isoformat()
    else:
        inferred = {"input_release": "", "input_hash": "", "primary_time": "available_time", "start": "", "end": ""}
    values = {
        name: str(getattr(args, name, None) or inferred[name])
        for name in ("input_release", "input_hash", "primary_time", "start", "end")
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        hint = "use --dataset to infer them" if not dataset else "the selected Dataset Release has incomplete metadata"
        raise ValueError(f"missing study input metadata: {', '.join(missing)}; {hint}")
    return values


def canonical_bar_events(bars: tuple[Bar, ...]):
    for sequence,bar in enumerate(bars):
        event=MarketEventEnvelope(bar.instrument_id,bar.start,bar.end,bar.end,"product-workflow","bars",
            bar.instrument_id.value,MarketEventType.BAR,sequence,{"period_start":bar.start,"period_end":bar.end,
            "open":bar.open,"high":bar.high,"low":bar.low,"close":bar.close,"volume":bar.volume},receive_time=bar.end)
        yield canonicalize_market_event(event,source_instance="product-workflow-replay")


def _time(value): return value if isinstance(value,datetime) else datetime.fromisoformat(str(value).replace("Z","+00:00"))


async def _captured_events(path:Path):return tuple([event async for event in CapturedCanonicalEventSource(path).events()])
def _bar_from_event(event):
    if not isinstance(event.payload,BarPayload):raise TypeError("SMA paper session requires canonical Bar capture")
    p=event.payload;return Bar(event.instrument_id,p.period_start,p.period_end,p.open,p.high,p.low,p.close,p.volume)


def _artifact_root(args)->Path:return Path(getattr(args,"artifact_root",None) or Path(args.lake_root)/"runs")
def _run_config(args):return {"fast":args.fast,"slow":args.slow,"initial_cash":str(args.initial_cash),"fee_bps":str(args.fee_bps)}


def _execution_calibration_binding(args) -> dict[str, object]:
    path = getattr(args, "execution_calibration", None)
    if not path:
        return {"status": "unbound", "reason": "no execution calibration release supplied"}
    release = load_execution_calibration_release(path)
    manifest = release.manifest
    return {
        "status": "bound",
        "release_id": release.release_id,
        "release_hash": release.release_hash,
        "manifest": str(release.manifest_path),
        "venue": manifest["venue"],
        "environment": manifest["environment"],
        "strategy_id": manifest.get("strategy_id"),
        "sample_count": manifest["sample_count"],
        "time_range": manifest["time_range"],
        "summary": manifest["summary"],
    }


def _calibrated_sma_backtest_result(
    identity: str, bars: tuple[Bar, ...], config: SmaCrossConfig,
    baseline, calibration: dict[str, object],
) -> dict[str, object]:
    if calibration.get("status") != "bound":
        return {"status": "unbound", "reason": "no execution calibration release supplied"}
    fee_bps = _calibrated_fee_bps(calibration)
    adjusted_config = replace(config, fee_bps=fee_bps)
    adjusted = backtest_sma_cross(BarSeries(identity, bars), adjusted_config)
    baseline_final = Decimal(str(baseline.metrics["final_equity"]))
    adjusted_final = Decimal(str(adjusted.metrics["final_equity"]))
    return {
        "status": "applied",
        "method": "fee_bps_mean",
        "baseline_fee_bps": str(config.fee_bps),
        "calibrated_fee_bps": str(fee_bps),
        "baseline_final_equity": str(baseline_final),
        "calibrated_final_equity": str(adjusted_final),
        "delta_final_equity": str(adjusted_final - baseline_final),
        "baseline_commissions": str(baseline.metrics["commissions"]),
        "calibrated_commissions": str(adjusted.metrics["commissions"]),
        "release_id": calibration["release_id"],
        "release_hash": calibration["release_hash"],
        "limitations": (
            "adjustment currently applies the calibration release mean fee_bps only",
            "latency, slippage, partial-fill and venue bucket models are reported but not yet simulated",
        ),
    }


def _calibrated_fee_bps(calibration: dict[str, object]) -> Decimal:
    summary = calibration.get("summary", {})
    fee_bps = summary.get("fee_bps") if isinstance(summary, dict) else None
    if not isinstance(fee_bps, dict) or fee_bps.get("mean") is None:
        raise ValueError("execution calibration release has no fee_bps mean for calibrated backtest comparison")
    return Decimal(str(fee_bps["mean"]))
