from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from kairospy.application.runtime import RuntimeMode
from kairospy.application.service.domain.market import IterableMarketEventSource, MarketDataResolver, MarketDataSpec
from kairospy.application.strategy import Strategy
from kairospy.core.account import AccountContext
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.data import DataStore

from ..common import (
    AccountPerformanceMixin,
    jsonable as common_jsonable,
    load_required_run_config,
    load_strategy as common_load_strategy,
    optional_int as common_optional_int,
    read_jsonl as common_read_jsonl,
    resolve_path as common_resolve_path,
    strategy_params as common_strategy_params,
    table as common_table,
)
from .market import BacktestMarketDataService


class BacktestConfigurationError(ValueError):
    pass


class BacktestSourceKind(StrEnum):
    EVENTS = "events"
    DATASET = "dataset"


@dataclass(frozen=True, slots=True)
class BacktestRunResult(AccountPerformanceMixin):
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


@dataclass(frozen=True, slots=True)
class BacktestAccountConfig:
    account_id: str
    cash: Decimal
    currency: str
    fee_rate: Decimal
    price_field: str


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
    account_config: BacktestAccountConfig
    backtest_config: Mapping[str, object]
    execution_config: Mapping[str, object]


def configured_backtest(config_path: Path) -> ConfiguredBacktest:
    run_config = load_required_run_config(config_path, mode=RuntimeMode.BACKTEST, error_type=BacktestConfigurationError)
    values = run_config.values
    strategy_params = _strategy_params(values)
    backtest = _table(values.get("backtest"), "backtest")
    execution_config = _table(values.get("execution"), "execution") if values.get("execution") is not None else {}
    account_defaults = run_config.account_defaults
    account_config = BacktestAccountConfig(
        account_id=run_config.run_id,
        cash=account_defaults.cash,
        currency=account_defaults.currency,
        fee_rate=account_defaults.fee_rate,
        price_field=str(backtest.get("price_field", "close")),
    )
    market_resolver = MarketResolver(
        default_venue=str(backtest.get("venue", "simulated")),
        default_market=str(backtest.get("market", "spot")),
    )
    data = BacktestMarketDataService(
        DataStore(_data_root(backtest, root=run_config.root), storage_format=_storage_format(backtest)),
        resolver=MarketDataResolver(market_resolver),
    )
    source, source_kind, source_value = _event_source(backtest, data, root=run_config.root)
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
        account_config=account_config,
        backtest_config=backtest,
        execution_config=execution_config,
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
    return common_load_strategy(ref, root=root, params=params, error_type=BacktestConfigurationError)


def _strategy_params(values: Mapping[str, object]) -> Mapping[str, object]:
    return common_strategy_params(values, BacktestConfigurationError)


def _data_root(backtest: Mapping[str, object], *, root: Path) -> Path:
    if backtest.get("data_root") is None:
        return Path(".kairos/data").resolve()
    return _resolve_path(backtest["data_root"], root=root, source="backtest.data_root")


def _storage_format(backtest: Mapping[str, object]) -> str:
    value = str(backtest.get("storage_format", "parquet"))
    if value not in {"parquet", "jsonl"}:
        raise BacktestConfigurationError("backtest.storage_format must be parquet or jsonl")
    return value


def _run_directory(backtest: Mapping[str, object], *, root: Path, run_id: str) -> Path:
    runs_root = Path(".kairos/runs").resolve() if backtest.get("runs_root") is None else _resolve_path(backtest["runs_root"], root=root, source="backtest.runs_root")
    return runs_root / RuntimeMode.BACKTEST.value / run_id


def _normalized_config(
    *,
    run_id: str,
    strategy: str,
    strategy_params: Mapping[str, object],
    backtest: Mapping[str, object],
    execution: Mapping[str, object],
    account: BacktestAccountConfig,
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
            "cash": account.cash,
            "currency": account.currency,
            "fee_rate": account.fee_rate,
            "price_field": account.price_field,
        },
        "execution": dict(execution),
    }


def _table(value: object, name: str) -> Mapping[str, object]:
    return common_table(value, name, BacktestConfigurationError)


def _resolve_path(value: object, *, root: Path, source: str) -> Path:
    return common_resolve_path(value, root=root, source=source, error_type=BacktestConfigurationError)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return common_read_jsonl(path, BacktestConfigurationError)


def _optional_int(value: object, source: str) -> int | None:
    return common_optional_int(value, source, BacktestConfigurationError, positive=True)


def _jsonable(value: object) -> object:
    return common_jsonable(value)


__all__ = [
    "BacktestAccountConfig",
    "BacktestConfigurationError",
    "BacktestRunResult",
    "BacktestSourceKind",
    "ConfiguredBacktest",
    "configured_backtest",
]
