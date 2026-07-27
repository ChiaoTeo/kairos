from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import StrEnum
import importlib
import json
from pathlib import Path
import sys
from typing import Mapping

import typer

from kairospy.config import ConfigError, load_run_config
from kairospy.context import DataContext
from kairospy.core.execution.simulation import BasisPointSlippageModel, ImmediateFillModel
from kairospy.core.reference import MarketResolver
from kairospy.data import DataStore
from kairospy.modes.backtest import BacktestEngine, SimulatedAccount, backtest_result_summary
from kairospy.runtime import IterableEventSource
from kairospy.runtime.account_journal import RunAccountJournal
from kairospy.runtime.line import RuntimeMode


backtest_app = typer.Typer(no_args_is_help=True, help="Backtest commands")


@backtest_app.command("run")
def run(
    config_path: Path = typer.Option(..., "--config"),
) -> None:
    configured = _configured_backtest(config_path)
    result = configured.engine.run(configured.source)
    summary = {"run_id": configured.run_id, **backtest_result_summary(result)}
    artifact = BacktestArtifactWriter(configured.run_directory, configured.run_id)
    artifact.write(configured, result, summary)
    _echo({"run_directory": str(configured.run_directory), **summary})


class BacktestArtifactWriter:
    def __init__(self, directory: Path, run_id: str) -> None:
        self.directory = directory
        self.run_id = run_id

    def write(self, configured: "_ConfiguredBacktest", result: object, summary: Mapping[str, object]) -> None:
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


class BacktestSourceKind(StrEnum):
    EVENTS = "events"
    DATASET = "dataset"


class _ConfiguredBacktest:
    def __init__(
        self,
        *,
        run_id: str,
        engine: BacktestEngine,
        source: IterableEventSource,
        source_kind: BacktestSourceKind,
        source_value: str,
        run_directory: Path,
        normalized_config: Mapping[str, object],
    ) -> None:
        self.run_id = run_id
        self.engine = engine
        self.source = source
        self.source_kind = source_kind
        self.source_value = source_value
        self.run_directory = run_directory
        self.normalized_config = normalized_config


def _configured_backtest(config_path: Path) -> _ConfiguredBacktest:
    try:
        run_config = load_run_config(config_path)
        run_config.require_mode(RuntimeMode.BACKTEST.value)
    except ConfigError as error:
        raise typer.BadParameter(str(error)) from error

    values = run_config.values
    strategy_params = _table(values.get("strategy"), "strategy").get("params", {})
    if not isinstance(strategy_params, Mapping):
        raise typer.BadParameter("[strategy.params] must be a table")
    strategy = _load_strategy(run_config.strategy, root=run_config.root, params=strategy_params)
    backtest = _table(values.get("backtest"), "backtest")
    account_defaults = run_config.account_defaults
    account = SimulatedAccount(
        run_config.run_id,
        account_defaults.cash,
        cash_currency=account_defaults.currency,
        fee_rate=account_defaults.fee_rate,
        price_field=str(backtest.get("price_field", "close")),
    )
    market_resolver = MarketResolver(
        default_venue=str(backtest.get("venue", "simulated")),
        default_market=str(backtest.get("market", "spot")),
    )
    data_root = _data_root(backtest, root=run_config.root)
    storage_format = _storage_format(backtest)
    data_context = DataContext(DataStore(data_root, storage_format=storage_format))
    source, source_kind, source_value = _event_source(backtest, data_context, root=run_config.root)
    engine = BacktestEngine(
        strategy,
        data_context,
        account,
        fill_model=_fill_model(backtest),
        slippage_model=_slippage_model(values.get("execution")),
        market_resolver=market_resolver,
    )
    run_directory = _run_directory(backtest, root=run_config.root, run_id=run_config.run_id)
    normalized_config = {
        "run": {"id": run_config.run_id, "mode": RuntimeMode.BACKTEST.value, "strategy": run_config.strategy},
        "strategy": {"params": dict(strategy_params)},
        "backtest": {
            **{str(key): _jsonable(value) for key, value in backtest.items()},
            "source_kind": source_kind.value,
            "source": source_value,
            "data_root": str(data_root),
            "storage_format": storage_format,
        },
        "account": {
            "cash": account_defaults.cash,
            "currency": account_defaults.currency,
            "fee_rate": account_defaults.fee_rate,
            "price_field": account.price_field,
        },
    }
    return _ConfiguredBacktest(
        run_id=run_config.run_id,
        engine=engine,
        source=source,
        source_kind=source_kind,
        source_value=source_value,
        run_directory=run_directory,
        normalized_config=normalized_config,
    )


def _event_source(
    backtest: Mapping[str, object],
    data_context: DataContext,
    *,
    root: Path,
) -> tuple[IterableEventSource, BacktestSourceKind, str]:
    stream = str(backtest.get("stream") or "backtest")
    if backtest.get("events") is not None:
        path = _resolve_path(backtest["events"], root=root, source="backtest.events")
        return IterableEventSource(stream if stream != "backtest" else path.stem, _read_jsonl(path)), BacktestSourceKind.EVENTS, str(path)
    dataset = backtest.get("dataset")
    if dataset is None:
        raise typer.BadParameter("backtest.dataset or backtest.events is required")
    dataset_name = str(dataset)
    rows = data_context.store.read_rows(
        dataset_name,
        start=backtest.get("start"),
        end=backtest.get("end"),
        limit=_optional_int(backtest.get("limit"), "backtest.limit"),
    )
    if not rows:
        raise typer.BadParameter(f"backtest dataset has no rows: {dataset_name}")
    return IterableEventSource(stream if stream != "backtest" else dataset_name, rows), BacktestSourceKind.DATASET, dataset_name


def _load_strategy(ref: str | None, *, root: Path, params: Mapping[str, object]) -> object:
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
    strategy = factory(**dict(params))
    if not hasattr(strategy, "on_market"):
        raise typer.BadParameter(f"strategy factory did not return a Strategy: {ref}")
    return strategy


def _project_root(root: Path) -> Path:
    for directory in (root, *root.parents):
        if (directory / "pyproject.toml").exists() or (directory / "kairos.toml").exists():
            return directory
    return root


def _data_root(backtest: Mapping[str, object], *, root: Path) -> Path:
    value = backtest.get("data_root", ".kairos/data")
    return _resolve_path(value, root=root, source="backtest.data_root")


def _storage_format(backtest: Mapping[str, object]) -> str:
    value = str(backtest.get("storage_format", "parquet"))
    if value not in {"parquet", "jsonl"}:
        raise typer.BadParameter("backtest.storage_format must be parquet or jsonl")
    return value


def _fill_model(backtest: Mapping[str, object]) -> ImmediateFillModel:
    volume_field = backtest.get("volume_field")
    return ImmediateFillModel(volume_field=None if volume_field is None else str(volume_field))


def _slippage_model(execution: object):
    table = _table(execution, "execution") if execution is not None else {}
    bps = table.get("slippage_bps")
    if bps is None:
        return None
    return BasisPointSlippageModel(Decimal(str(bps)))


def _run_directory(backtest: Mapping[str, object], *, root: Path, run_id: str) -> Path:
    runs_root = _resolve_path(backtest.get("runs_root", ".kairos/runs"), root=root, source="backtest.runs_root")
    return runs_root / RuntimeMode.BACKTEST.value / run_id


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


def _table(value: object, name: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise typer.BadParameter(f"[{name}] must be a table")
    return value


def _optional_int(value: object, source: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise typer.BadParameter(f"{source} must be an integer")
    try:
        parsed = int(value)
    except Exception as error:
        raise typer.BadParameter(f"{source} must be an integer") from error
    if parsed < 0:
        raise typer.BadParameter(f"{source} cannot be negative")
    return parsed


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = tuple(values or ())
    path.write_text("".join(json.dumps(_jsonable(row), sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _echo(payload: Mapping[str, object]) -> None:
    typer.echo(json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True))


def _report_markdown(configured: _ConfiguredBacktest, summary: Mapping[str, object]) -> str:
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
