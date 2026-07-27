from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import StrEnum
import importlib
import json
import sys
import time
from pathlib import Path
from typing import Mapping

import typer

from kairospy.config import load_run_config
from kairospy.backtest import BacktestEngine, SimulatedAccount
from kairospy.context import DataContext
from kairospy.reference import MarketResolver
from kairospy.data import DataStore
from kairospy.runtime import IterableEventSource
from kairospy.runtime.daemon import LiveRunControlPlane


run_app = typer.Typer(no_args_is_help=True, help="Run and live daemon commands")


@run_app.command("config")
def config(
    action: str = typer.Argument(...),
    path: Path = typer.Argument(...),
) -> None:
    run_config = load_run_config(path)
    if action == "validate":
        report = run_config.validation_report()
        _echo({
            "path": str(report.path),
            "valid": report.valid,
            "issues": list(report.issues),
        })
        if not report.valid:
            raise typer.Exit(2)
        return
    if action == "explain":
        _echo(run_config.explain())
        return
    raise typer.BadParameter(f"unsupported config action: {action}")


@run_app.command("backtest")
def backtest(
    config_path: Path = typer.Option(..., "--config"),
    events: Path | None = typer.Option(None, "--events"),
) -> None:
    run_config = load_run_config(config_path)
    run_config.require_mode("backtest")
    strategy = _load_strategy(run_config.strategy, root=run_config.root)
    backtest_config = _table(run_config.values.get("backtest"), "backtest")
    event_path = _resolve_path(events or backtest_config.get("events"), root=run_config.root, source="backtest.events")
    rows = _read_jsonl(event_path)
    account_defaults = run_config.account_defaults
    account = SimulatedAccount(
        run_config.run_id,
        account_defaults.cash,
        cash_currency=account_defaults.currency,
        fee_rate=account_defaults.fee_rate,
    )
    data = DataContext(
        DataStore(":unused:", storage_format="jsonl"),
        markets=MarketResolver(
            default_venue=str(backtest_config.get("venue", "simulated")),
            default_market=str(backtest_config.get("market", "spot")),
        ),
    )
    result = BacktestEngine(strategy, data, account).run(
        IterableEventSource(str(backtest_config.get("stream", event_path.stem)), rows),
    )
    _echo({
        "run_id": run_config.run_id,
        "mode": run_config.mode,
        "strategy_id": result.runtime.strategy_id,
        "event_count": result.runtime.event_count,
        "initial_equity": result.initial_equity,
        "final_equity": result.final_equity,
        "net_profit": result.net_profit,
        "total_return": result.total_return,
        "fills": len(result.fills),
        "closed_trades": len(result.trades),
        "metrics": result.metrics,
    })


@run_app.command("live")
def live(
    action: str = typer.Argument("status"),
    run_id: str = typer.Option(..., "--run-id"),
    foreground: bool = typer.Option(False, "--foreground"),
    duration_seconds: float | None = typer.Option(None, "--duration-seconds"),
    poll_seconds: float = typer.Option(1.0, "--poll-seconds"),
    stale_after_seconds: float = typer.Option(5.0, "--stale-after-seconds"),
    log_file: Path | None = typer.Option(None, "--log-file"),
    reason: str | None = typer.Option(None, "--reason"),
    actor: str = typer.Option("cli", "--actor"),
    force: bool = typer.Option(False, "--force"),
    wait: float = typer.Option(0.0, "--wait"),
) -> None:
    control = LiveRunControlPlane(run_id)
    if action == "start":
        status = (
            control.run_foreground(poll_seconds=poll_seconds, duration_seconds=duration_seconds)
            if foreground or duration_seconds is not None
            else control.start_background(
                poll_seconds=poll_seconds,
                stale_after_seconds=stale_after_seconds,
                log_file=log_file,
            )
        )
        _echo(status.to_dict())
        return
    if action == "status":
        _echo(control.status(stale_after_seconds=stale_after_seconds).to_dict())
        return
    if action in {"stop", "force-stop"}:
        command = control.request_stop(
            reason=reason or f"operator requested {action}",
            actor=actor,
            force=force or action == "force-stop",
        )
        if wait > 0:
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                status = control.status(stale_after_seconds=stale_after_seconds)
                if status.phase.value == "stopped":
                    _echo(status.to_dict())
                    return
                time.sleep(min(0.1, wait))
        _echo({"command": command, "status": control.status(stale_after_seconds=stale_after_seconds).to_dict()})
        return
    if action == "attach":
        _attach(control, stale_after_seconds=stale_after_seconds, poll_seconds=poll_seconds)
        return
    raise typer.BadParameter(f"unsupported live action: {action}")


def _attach(control: LiveRunControlPlane, *, stale_after_seconds: float, poll_seconds: float) -> None:
    last = None
    while True:
        status = control.status(stale_after_seconds=stale_after_seconds).to_dict()
        current = json.dumps(status, sort_keys=True)
        if current != last:
            _echo(status)
            last = current
        if status["phase"] in {"stopped", "failed"}:
            return
        time.sleep(poll_seconds)


def _echo(payload: dict[str, object]) -> None:
    typer.echo(json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True))


def _load_strategy(ref: str | None, *, root: Path) -> object:
    if ref is None or ":" not in ref:
        raise typer.BadParameter("run.strategy must be module:callable")
    module_name, attr_name = ref.split(":", 1)
    project_root = _project_root(root)
    inserted = False
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
        inserted = True
    try:
        module = importlib.import_module(module_name)
    finally:
        if inserted:
            try:
                sys.path.remove(str(project_root))
            except ValueError:
                pass
    factory = getattr(module, attr_name)
    strategy = factory()
    if not hasattr(strategy, "on_market"):
        raise typer.BadParameter(f"strategy factory did not return a Strategy: {ref}")
    return strategy


def _project_root(root: Path) -> Path:
    for directory in (root, *root.parents):
        if (directory / "pyproject.toml").exists() or (directory / "kairos.toml").exists():
            return directory
    return root


def _table(value: object, name: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise typer.BadParameter(f"[{name}] must be a table")
    return value


def _resolve_path(value: object, *, root: Path, source: str) -> Path:
    if value is None:
        raise typer.BadParameter(f"{source} is required")
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if not isinstance(value, dict):
                raise typer.BadParameter(f"event row must be a JSON object: {path}")
            rows.append(value)
    if not rows:
        raise typer.BadParameter(f"event file has no rows: {path}")
    return rows


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = ["run_app"]
