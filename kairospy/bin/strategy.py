from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time
from decimal import Decimal
from typing import Any, Mapping

from kairospy.application.strategy.composition import compose_strategy_process
from kairospy.application.observability import configure_from_environment, record_gauge
from kairospy.application.workspace import WorkspaceApplication
from kairospy.strategy import StrategyOutput


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one KairosPy strategy server")
    parser.add_argument("command", nargs="?", choices=("server",), default="server")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--strategy",
        required=True,
        help="strategy module and factory, e.g. strategies.sma:strategy",
    )
    parser.add_argument("--launch-id", required=True)
    parser.add_argument(
        "--instance-id", "--instance", dest="instance_id", required=True
    )
    parser.add_argument("--mode", default="paper")
    parser.add_argument(
        "--params", default="{}", help="JSON object passed to the strategy factory"
    )
    return parser


def _params(value: str) -> dict[str, object]:
    parsed: Any = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("--params must be a JSON object")
    return parsed


def _write_backtest_report(composition, workspace) -> None:
    # The process mode is not part of StrategyApplication's business state; this
    # helper is called only for a backtest process.
    instance = workspace.instance(
        "backtest",
        composition.application.launch_id,
        composition.application.instance_id,
    )
    dataset = _replay_dataset_identity(instance.state("market", "replay.jsonl"))
    config = _file_identity(instance.normalized_config())
    config["summary"] = _backtest_config_summary(instance.normalized_config())
    final_account = None
    account_ids = composition.application.context.account.account_ids
    if account_ids:
        final_account = asdict(
            composition.application.context.account.account(account_ids[0])
        )
    notifications = _notification_report(instance.artifact("notifications.jsonl"))
    report = {
        "schema_version": 1,
        "launch_id": composition.application.launch_id,
        "instance_id": composition.application.instance_id,
        "strategy_id": composition.application.strategy.strategy_id,
        "completed_at_unix_nanos": time.time_ns(),
        "status": composition.application.status.state.value,
        "event_count": composition.application.status.event_count,
        "dataset": dataset,
        "config": config,
        "clock_events": list(composition.application.clock_events),
        "event_trace": list(composition.application.event_trace),
        "fills": list(composition.application.backtest_fills),
        "metrics": _backtest_metrics(
            composition.application.backtest_fills, composition.application.equity_curve
        ),
        "equity_curve": list(composition.application.equity_curve),
        # The final quote can settle an order after the last pre-strategy
        # mark.  Read Account once more at report time so this field is the
        # authoritative terminal state, not merely the last curve sample.
        "final_account": final_account
        if final_account is not None
        else (
            composition.application.equity_curve[-1]["snapshot"]
            if composition.application.equity_curve
            else None
        ),
        "notifications": notifications,
    }
    report["deterministic_result_sha256"] = _deterministic_result_sha256(report)
    path = instance.state("backtest", "report.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _backtest_metrics(
    fills: list[Mapping[str, Any]], curve: list[Mapping[str, Any]]
) -> dict[str, Any]:
    """Stable, integer-preserving summary for machine comparison."""
    realized_pnl = Decimal("0")
    total_fees = Decimal("0")
    for fill in fills:
        quantity = _wire_decimal(fill.get("quantity"))
        price = _wire_decimal(fill.get("price"))
        fee = _wire_decimal(fill.get("fee"))
        notional = quantity * price
        if str(fill.get("side", "")).lower() == "sell":
            realized_pnl += notional
        else:
            realized_pnl -= notional
        total_fees += fee
    return {
        "fill_count": len(fills),
        "buy_fill_count": sum(
            1 for fill in fills if str(fill.get("side", "")).lower() == "buy"
        ),
        "sell_fill_count": sum(
            1 for fill in fills if str(fill.get("side", "")).lower() == "sell"
        ),
        "equity_sample_count": len(curve),
        "first_equity_time": curve[0].get("observed_at_unix_nanos") if curve else None,
        "last_equity_time": curve[-1].get("observed_at_unix_nanos") if curve else None,
        "realized_pnl": _decimal_text(realized_pnl - total_fees),
        "total_fees": _decimal_text(total_fees),
    }


def _wire_decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        return Decimal("0")
    return Decimal(value)


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _deterministic_result_sha256(report: Mapping[str, Any]) -> str:
    """Hash only replay-result facts, excluding runtime identity and wall time."""
    dataset = report.get("dataset")
    config = report.get("config")
    fills = report.get("fills", [])
    normalized_fills = [
        {
            key: fill.get(key)
            for key in (
                "instrument_id",
                "side",
                "quantity",
                "price",
                "fee",
                "occurred_at_unix_nanos",
            )
        }
        for fill in fills
        if isinstance(fill, Mapping)
    ]
    payload = {
        "dataset": {
            key: dataset.get(key)
            for key in (
                "sha256",
                "event_count",
                "first_event_time_unix_nanos",
                "last_event_time_unix_nanos",
                "observation_types",
                "timeframes",
                "derivations",
                "source_ids",
            )
        }
        if isinstance(dataset, Mapping)
        else dataset,
        "config": {
            "sha256": config.get("sha256") if isinstance(config, Mapping) else None,
            "summary": config.get("summary") if isinstance(config, Mapping) else None,
        },
        "clock_events": report.get("clock_events", []),
        "event_trace": report.get("event_trace", []),
        "fills": normalized_fills,
        "equity_curve": report.get("equity_curve", []),
        "final_account": report.get("final_account"),
        "metrics": report.get("metrics", {}),
        "notifications": (
            {
                key: report["notifications"].get(key)
                for key in ("count", "content_sha256")
            }
            if isinstance(report.get("notifications"), Mapping)
            else {}
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _notification_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"count": 0, "content_sha256": None, "artifact": str(path)}
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    deterministic = [
        {
            key: record.get(key)
            for key in (
                "destination_id",
                "title",
                "body",
                "severity",
                "occurred_at",
                "attributes",
                "outcome",
            )
        }
        for record in records
    ]
    deterministic.sort(
        key=lambda record: (
            str(record.get("occurred_at")),
            str(record.get("title")),
            str(record.get("destination_id")),
        )
    )
    encoded = json.dumps(deterministic, sort_keys=True, separators=(",", ":"))
    return {
        "count": len(records),
        "content_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "artifact": str(path),
    }


def _replay_dataset_identity(path: Path) -> dict[str, Any]:
    """Return stable provenance for the exact replay stream consumed."""
    identity: dict[str, Any] = {
        "path": str(path),
        "available": path.is_file(),
    }
    if not path.is_file():
        return identity

    raw = path.read_bytes()
    identity["sha256"] = hashlib.sha256(raw).hexdigest()
    event_count = 0
    first_event_time: int | None = None
    last_event_time: int | None = None
    observation_types: set[str] = set()
    timeframes: set[str] = set()
    derivations: set[str] = set()
    source_ids: set[str] = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        event_count += 1
        for kind in ("Quote", "Bar", "Trade", "quote", "bar", "trade"):
            payload = record.get(kind)
            if not isinstance(payload, dict):
                continue
            observation_types.add(kind.lower())
            event_time = payload.get("observed_at_unix_nanos")
            if isinstance(event_time, int):
                first_event_time = (
                    event_time
                    if first_event_time is None
                    else min(first_event_time, event_time)
                )
                last_event_time = (
                    event_time
                    if last_event_time is None
                    else max(last_event_time, event_time)
                )
            source_id = payload.get("source_id")
            if isinstance(source_id, str) and source_id:
                source_ids.add(source_id)
            timeframe = payload.get("timeframe")
            if isinstance(timeframe, str) and timeframe:
                timeframes.add(timeframe)
            derivation = payload.get("derivation")
            if isinstance(derivation, str) and derivation:
                derivations.add(derivation)
            break
    identity.update(
        {
            "event_count": event_count,
            "first_event_time_unix_nanos": first_event_time,
            "last_event_time_unix_nanos": last_event_time,
            "observation_types": sorted(observation_types),
            "timeframes": sorted(timeframes),
            "derivations": sorted(derivations),
            "source_ids": sorted(source_ids),
        }
    )
    return identity


def _backtest_config_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    market = value.get("backtest_market") or value.get("backtest", {}).get("market", {})
    launch = value.get("launch", {})
    execution = value.get("execution", {})
    return {
        "mode": launch.get("mode") if isinstance(launch, dict) else None,
        "strategy": launch.get("strategy") if isinstance(launch, dict) else None,
        "market": market if isinstance(market, dict) else {},
        "execution": execution if isinstance(execution, dict) else {},
    }


def _file_identity(path: Path) -> dict[str, Any]:
    """Identify a launch artifact without making reporting depend on it."""
    if not path.is_file():
        return {"path": str(path), "available": False}
    raw = path.read_bytes()
    return {
        "path": str(path),
        "available": True,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


async def _run(args: argparse.Namespace) -> None:
    workspace = WorkspaceApplication().open(args.workspace)
    telemetry = configure_from_environment(
        "strategy",
        instance_id=args.instance_id,
        workspace_id=workspace.identity.workspace_id,
    )
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    composition = None
    try:
        composition = compose_strategy_process(
            workspace,
            strategy_ref=args.strategy,
            launch_id=args.launch_id,
            instance_id=args.instance_id,
            mode=args.mode,
            params=_params(args.params),
        )
        await composition.notifications.runtime.start()
        sys.stdout = StrategyOutput(composition.application.logger, source="stdout")
        sys.stderr = StrategyOutput(composition.application.logger, source="stderr")
        await composition.control.start()
        record_gauge("kairos.process.ready", 1)
        await composition.control.serve_until_stopped()
        await composition.notifications.runtime.flush()
        if args.mode == "backtest":
            _write_backtest_report(composition, workspace)
    finally:
        if composition is not None:
            await composition.control.close()
            await composition.notifications.runtime.close()
        sys.stdout.flush()
        sys.stderr.flush()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        if telemetry is not None:
            telemetry.force_flush()
            telemetry.shutdown()


def main() -> int:
    args = _parser().parse_args()
    try:
        asyncio.run(_run(args))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"strategy process failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
