from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
import importlib
import json
from pathlib import Path
import sys
from typing import Mapping

from kairospy.application.runtime import RuntimeMode, RuntimeRunSpec, RuntimeRunner
from kairospy.application.runtime.services.account import account_current_view_key
from kairospy.application.service.domain.account import SimulatedAccount
from kairospy.application.service.domain.execution import (
    BasisPointSlippageModel,
    ImmediateFillModel,
    PercentageCommissionModel,
)
from kairospy.application.service.domain.market import IterableMarketEventSource, MarketDataResolver, MarketDataSpec
from kairospy.application.strategy import Strategy
from kairospy.config import ConfigError, load_run_config
from kairospy.core.account import AccountContext
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.data import DataStore

from .account import BacktestAccountService
from .execution import BacktestExecutionService
from .market import BacktestMarketDataService
from .metrics import MetricsModel, closed_trades_from_fills, equity_point_from_account_view


class BacktestConfigurationError(ValueError):
    pass


class BacktestSourceKind(StrEnum):
    EVENTS = "events"
    DATASET = "dataset"


@dataclass(frozen=True, slots=True)
class BacktestRunResult:
    run_id: str
    mode: RuntimeMode
    runtime: object
    views: object
    intents: object
    controls: object
    account: AccountContext
    account_view: object | None
    fills: tuple[object, ...] = ()
    equity_curve: tuple[object, ...] = ()
    trades: tuple[object, ...] = ()
    metrics: Mapping[str, object] = field(default_factory=dict)

    @property
    def initial_equity(self) -> Decimal:
        value = getattr(self.account_view, "initial_equity", None)
        if value is not None:
            return Decimal(str(value))
        value = getattr(self.account_view, "cash", None)
        return Decimal("0") if value is None else Decimal(str(value))

    @property
    def final_equity(self) -> Decimal:
        value = getattr(self.account_view, "equity", None)
        return Decimal("0") if value is None else Decimal(str(value))

    @property
    def net_profit(self) -> Decimal:
        value = getattr(self.account_view, "net_profit", None)
        return self.final_equity - self.initial_equity if value is None else Decimal(str(value))

    @property
    def total_return(self) -> Decimal:
        value = getattr(self.account_view, "total_return", None)
        if value is not None:
            return Decimal(str(value))
        return Decimal("0") if self.initial_equity == 0 else self.net_profit / self.initial_equity


@dataclass(frozen=True, slots=True)
class ConfiguredBacktest:
    run_id: str
    strategy: Strategy
    source: IterableMarketEventSource
    source_kind: BacktestSourceKind
    source_value: str
    run_directory: Path
    normalized_config: Mapping[str, object]
    data: BacktestMarketDataService
    account: BacktestAccountService
    execution: BacktestExecutionService
    coordinator: ExecutionCoordinator

    def run(self) -> BacktestRunResult:
        runtime = RuntimeRunner.run_sync(
            RuntimeRunSpec(
                run_id=self.run_id,
                mode=RuntimeMode.BACKTEST,
                strategy=self.strategy,
                source=self.source,
                data=self.data,
                account=self.account,
                execution=self.coordinator,
                providers=(self.execution,),
            )
        )
        account_view = runtime.views.get(account_current_view_key(self.account.account.context), None)
        fills = self.execution.fills
        equity_curve = tuple(
            item
            for item in (
                equity_point_from_account_view(
                    None if runtime.runtime.last_event is None else runtime.runtime.last_event.time,
                    account_view,
                ),
            )
            if item is not None
        )
        trades = closed_trades_from_fills(fills)
        metrics = MetricsModel().evaluate(equity_curve, trades, initial_equity=self.account.account.initial_cash)
        result = BacktestRunResult(
            run_id=self.run_id,
            mode=RuntimeMode.BACKTEST,
            runtime=runtime.runtime,
            views=runtime.views,
            intents=runtime.intents,
            controls=runtime.controls,
            account=self.account.account.context,
            account_view=account_view,
            fills=fills,
            equity_curve=equity_curve,
            trades=trades,
            metrics=metrics,
        )
        return result


def configured_backtest(config_path: Path) -> ConfiguredBacktest:
    try:
        run_config = load_run_config(config_path)
        run_config.require_mode(RuntimeMode.BACKTEST.value)
    except ConfigError as error:
        raise BacktestConfigurationError(str(error)) from error
    if run_config.strategy is None:
        raise BacktestConfigurationError("run.strategy is required")

    values = run_config.values
    strategy_params = _strategy_params(values)
    backtest = _table(values.get("backtest"), "backtest")
    execution_config = _table(values.get("execution"), "execution") if values.get("execution") is not None else {}
    account_defaults = run_config.account_defaults
    account_config = SimulatedAccount(
        run_config.run_id,
        account_defaults.cash,
        cash_currency=account_defaults.currency,
        fee_rate=account_defaults.fee_rate,
        price_field=str(backtest.get("price_field", "close")),
    )
    coordinator = ExecutionCoordinator()
    account_service = BacktestAccountService(account_config, coordinator)
    market_resolver = MarketResolver(
        default_venue=str(backtest.get("venue", "simulated")),
        default_market=str(backtest.get("market", "spot")),
    )
    data = BacktestMarketDataService(
        DataStore(_data_root(backtest, root=run_config.root), storage_format=_storage_format(backtest)),
        resolver=MarketDataResolver(market_resolver),
    )
    source, source_kind, source_value = _event_source(backtest, data, root=run_config.root)
    execution = BacktestExecutionService(
        coordinator,
        account=account_config.context,
        cash_currency=account_config.cash_currency,
        price_field=account_config.price_field,
        fill_model=_fill_model(backtest),
        slippage_model=_slippage_model(execution_config),
        commission_model=PercentageCommissionModel(account_config.fee_rate),
    )
    return ConfiguredBacktest(
        run_id=run_config.run_id,
        strategy=_load_strategy(run_config.strategy, root=run_config.root, params=strategy_params),
        source=source,
        source_kind=source_kind,
        source_value=source_value,
        run_directory=_run_directory(backtest, root=run_config.root, run_id=run_config.run_id),
        normalized_config=_normalized_config(
            run_id=run_config.run_id,
            strategy=run_config.strategy,
            strategy_params=strategy_params,
            backtest=backtest,
            execution=execution_config,
            account=account_config,
            source_kind=source_kind,
            source_value=source_value,
            data=data,
        ),
        data=data,
        account=account_service,
        execution=execution,
        coordinator=coordinator,
    )


def _event_source(
    backtest: Mapping[str, object],
    data: BacktestMarketDataService,
    *,
    root: Path,
) -> tuple[IterableMarketEventSource, BacktestSourceKind, str]:
    stream = str(backtest.get("stream") or "backtest")
    if backtest.get("events") is not None:
        path = _resolve_path(backtest["events"], root=root, source="backtest.events")
        return IterableMarketEventSource(stream if stream != "backtest" else path.stem, _read_jsonl(path)), BacktestSourceKind.EVENTS, str(path)
    dataset = backtest.get("dataset")
    if dataset is None:
        raise BacktestConfigurationError("backtest.events or backtest.dataset is required")
    spec = MarketDataSpec(
        symbol=str(backtest.get("symbol") or dataset),
        kind=str(backtest.get("kind") or "ohlcv"),
        venue=None if backtest.get("venue") is None else str(backtest["venue"]),
        market=None if backtest.get("market") is None else str(backtest["market"]),
        timeframe=None if backtest.get("timeframe") is None else str(backtest["timeframe"]),
        start=backtest.get("start"),
        end=backtest.get("end"),
        limit=_optional_int(backtest.get("limit"), "backtest.limit"),
        dataset=str(dataset),
        stream=stream if stream != "backtest" else None,
    )
    resolved = data.resolve(spec)
    rows = data.read(spec)
    if not rows:
        raise BacktestConfigurationError(f"backtest dataset has no rows: {resolved.dataset_id}")
    return IterableMarketEventSource(resolved.stream_name, rows), BacktestSourceKind.DATASET, resolved.dataset_id


def _load_strategy(ref: str, *, root: Path, params: Mapping[str, object]) -> Strategy:
    if ":" not in ref:
        raise BacktestConfigurationError("run.strategy must be module:callable")
    module_name, attr_name = ref.split(":", 1)
    project_root = _project_root(root)
    inserted = False
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
        inserted = True
    if inserted:
        sys.modules.pop(module_name, None)
    try:
        module = importlib.import_module(module_name)
    finally:
        if inserted:
            try:
                sys.path.remove(str(project_root))
            except ValueError:
                pass
    factory = getattr(module, attr_name)
    strategy = factory(**dict(params)) if callable(factory) else factory
    if not hasattr(strategy, "strategy_id"):
        raise BacktestConfigurationError(f"strategy factory did not return a Strategy: {ref}")
    return strategy


def _strategy_params(values: Mapping[str, object]) -> Mapping[str, object]:
    strategy = _table(values.get("strategy"), "strategy") if values.get("strategy") is not None else {}
    params = strategy.get("params", {})
    if not isinstance(params, Mapping):
        raise BacktestConfigurationError("[strategy.params] must be a table")
    return params


def _data_root(backtest: Mapping[str, object], *, root: Path) -> Path:
    return _resolve_path(backtest.get("data_root", ".kairos/data"), root=root, source="backtest.data_root")


def _storage_format(backtest: Mapping[str, object]) -> str:
    value = str(backtest.get("storage_format", "parquet"))
    if value not in {"parquet", "jsonl"}:
        raise BacktestConfigurationError("backtest.storage_format must be parquet or jsonl")
    return value


def _fill_model(backtest: Mapping[str, object]) -> ImmediateFillModel:
    volume_field = backtest.get("volume_field")
    return ImmediateFillModel(volume_field=None if volume_field is None else str(volume_field))


def _slippage_model(execution: Mapping[str, object]) -> BasisPointSlippageModel | None:
    bps = execution.get("slippage_bps")
    return None if bps is None else BasisPointSlippageModel(Decimal(str(bps)))


def _run_directory(backtest: Mapping[str, object], *, root: Path, run_id: str) -> Path:
    runs_root = _resolve_path(backtest.get("runs_root", ".kairos/runs"), root=root, source="backtest.runs_root")
    return runs_root / RuntimeMode.BACKTEST.value / run_id


def _normalized_config(
    *,
    run_id: str,
    strategy: str,
    strategy_params: Mapping[str, object],
    backtest: Mapping[str, object],
    execution: Mapping[str, object],
    account: SimulatedAccount,
    source_kind: BacktestSourceKind,
    source_value: str,
    data: BacktestMarketDataService,
) -> Mapping[str, object]:
    return {
        "run": {"id": run_id, "mode": RuntimeMode.BACKTEST.value, "strategy": strategy},
        "strategy": {"params": dict(strategy_params)},
        "backtest": {
            **{str(key): _jsonable(value) for key, value in backtest.items()},
            "source_kind": source_kind.value,
            "source": source_value,
            "data_root": str(data.store.root),
            "storage_format": data.store.storage_format,
        },
        "account": {
            "cash": account.initial_cash,
            "currency": account.cash_currency,
            "fee_rate": account.fee_rate,
            "price_field": account.price_field,
        },
        "execution": dict(execution),
    }


def _table(value: object, name: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise BacktestConfigurationError(f"[{name}] must be a table")
    return value


def _resolve_path(value: object, *, root: Path, source: str) -> Path:
    if value is None:
        raise BacktestConfigurationError(f"{source} is required")
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
                raise BacktestConfigurationError(f"event row must be a JSON object: {path}")
            rows.append(value)
    if not rows:
        raise BacktestConfigurationError(f"event file has no rows: {path}")
    return rows


def _optional_int(value: object, source: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise BacktestConfigurationError(f"{source} must be an integer")
    parsed = int(value)
    if parsed < 1:
        raise BacktestConfigurationError(f"{source} must be positive")
    return parsed


def _project_root(root: Path) -> Path:
    for directory in (root, *root.parents):
        if (directory / "pyproject.toml").exists() or (directory / "kairos.toml").exists():
            return directory
    return root


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


__all__ = [
    "BacktestConfigurationError",
    "BacktestRunResult",
    "BacktestSourceKind",
    "ConfiguredBacktest",
    "configured_backtest",
]
