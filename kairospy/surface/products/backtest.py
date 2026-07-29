from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import StrEnum
import json
from typing import Mapping

import typer

from kairospy.application.system import TradingConfigurationError, TradingSystemLauncher


backtest_app = typer.Typer(no_args_is_help=True, help="Backtest commands")
_TRADING_LAUNCHER = TradingSystemLauncher()


@backtest_app.command("run")
def run(
    config_path: str = typer.Option(..., "--config"),
) -> None:
    try:
        result = _TRADING_LAUNCHER.run_backtest_config(config_path)
    except TradingConfigurationError as error:
        raise typer.BadParameter(str(error)) from error
    _echo({"run_id": getattr(result, "run_id"), **backtest_result_summary(result)})


def backtest_result_summary(result: object) -> dict[str, object]:
    runtime = getattr(result, "runtime", None)
    account = getattr(result, "account", None)
    return _jsonable({
        "mode": getattr(getattr(account, "environment", None), "value", None),
        "strategy_id": getattr(runtime, "strategy_id", None),
        "event_count": getattr(runtime, "event_count", None),
        "initial_equity": getattr(result, "initial_equity", None),
        "final_equity": getattr(result, "final_equity", None),
        "net_profit": getattr(result, "net_profit", None),
        "total_return": getattr(result, "total_return", None),
        "fills": len(tuple(getattr(result, "fills", ()))),
        "closed_trades": len(tuple(getattr(result, "trades", ()))),
        "metrics": getattr(result, "metrics", {}),
    })


def _echo(payload: Mapping[str, object]) -> None:
    typer.echo(json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True))


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "value"):
        return getattr(value, "value")
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = ["backtest_app"]
