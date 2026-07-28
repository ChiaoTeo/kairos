from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import StrEnum
import json
from pathlib import Path
from typing import Mapping

import typer

from kairospy.modes.backtest import backtest_result_summary
from kairospy.runtime.account_journal import RunAccountJournal
from kairospy.runtime.line import RuntimeMode
from kairospy.service.modes.backtest import BacktestConfigurationError, ConfiguredBacktest, configured_backtest


backtest_app = typer.Typer(no_args_is_help=True, help="Backtest commands")


@backtest_app.command("run")
def run(
    config_path: Path = typer.Option(..., "--config"),
) -> None:
    try:
        configured = configured_backtest(config_path)
    except BacktestConfigurationError as error:
        raise typer.BadParameter(str(error)) from error
    result = configured.engine.run(configured.source)
    summary = {"run_id": configured.run_id, **backtest_result_summary(result)}
    artifact = BacktestArtifactWriter(configured.run_directory, configured.run_id)
    artifact.write(configured, result, summary)
    _echo({"run_directory": str(configured.run_directory), **summary})


class BacktestArtifactWriter:
    def __init__(self, directory: Path, run_id: str) -> None:
        self.directory = directory
        self.run_id = run_id

    def write(self, configured: ConfiguredBacktest, result: object, summary: Mapping[str, object]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        _write_json(self.directory / "summary.json", summary)
        _write_json(self.directory / "metrics.json", getattr(result, "metrics", {}))
        _write_json(self.directory / "config.normalized.json", configured.normalized_config)
        _write_jsonl(self.directory / "equity.jsonl", getattr(result, "equity_curve", ()))
        _write_jsonl(self.directory / "fills.jsonl", getattr(result, "fills", ()))
        _write_jsonl(self.directory / "trades.jsonl", getattr(result, "trades", ()))
        _write_jsonl(self.directory / "intent_states.jsonl", getattr(getattr(result, "runtime", None), "intent_states", ()))
        (self.directory / "report.md").write_text(_report_markdown(configured, summary), encoding="utf-8")
        RunAccountJournal(self.directory, run_id=self.run_id, mode=RuntimeMode.BACKTEST.value).record_backtest_result(
            result,
            run_id=self.run_id,
            mode=RuntimeMode.BACKTEST.value,
        )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = tuple(values or ())
    path.write_text("".join(json.dumps(_jsonable(row), sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _echo(payload: Mapping[str, object]) -> None:
    typer.echo(json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True))


def _report_markdown(configured: ConfiguredBacktest, summary: Mapping[str, object]) -> str:
    lines = [
        f"# Backtest {configured.run_id}",
        "",
        "## Summary",
        "",
        f"- Source: {configured.source_kind.value} {configured.source_value}",
        f"- Initial equity: {summary.get('initial_equity')}",
        f"- Final equity: {summary.get('final_equity')}",
        f"- Net profit: {summary.get('net_profit')}",
        f"- Total return: {summary.get('total_return')}",
        f"- Fills: {summary.get('fills')}",
        f"- Closed trades: {summary.get('closed_trades')}",
        "",
    ]
    return "\n".join(lines)


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


__all__ = ["backtest_app"]
