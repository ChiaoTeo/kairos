from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from kairospy.application.runtime import RuntimeMode
from kairospy.application.service.domain.market import MarketDataResolver
from kairospy.application.service.runtime.market import ReplayMarketDataPolicy
from kairospy.application.strategy import Strategy
from kairospy.core.account import AccountContext
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.data import DataStore

from ..common import (
    AccountPerformanceMixin,
    jsonable as common_jsonable,
    load_required_run_config,
    load_strategy as common_load_strategy,
    resolve_path as common_resolve_path,
    strategy_params as common_strategy_params,
    table as common_table,
)
from .market import BacktestMarketDataService


class BacktestConfigurationError(ValueError):
    pass


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
    decision_trace: tuple[object, ...] = ()
    risk_snapshots: tuple[object, ...] = ()
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
    run_directory: Path
    normalized_config: Mapping[str, object]
    data: BacktestMarketDataService
    account_config: BacktestAccountConfig
    market_policy: ReplayMarketDataPolicy
    backtest_config: Mapping[str, object]
    execution_config: Mapping[str, object]


def configured_backtest(config_path: Path, *, strategy_ref: str | None = None) -> ConfiguredBacktest:
    run_config = load_required_run_config(config_path, mode=RuntimeMode.BACKTEST, error_type=BacktestConfigurationError, strategy_ref=strategy_ref)
    values = run_config.values
    strategy_params = _strategy_params(values)
    backtest = _table(values.get("backtest"), "backtest")
    timeline_config = _table(values.get("timeline"), "timeline") if values.get("timeline") is not None else {}
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
        default_venue=_default_text(backtest.get("venue"), "simulated"),
        default_market=_default_text(backtest.get("market"), "spot"),
    )
    market_policy = _market_policy(backtest)
    data = BacktestMarketDataService(
        DataStore(_data_root(backtest, root=run_config.root), storage_format=_storage_format(backtest)),
        resolver=MarketDataResolver(market_resolver),
        policy=market_policy,
    )
    return ConfiguredBacktest(
        run_id=run_config.run_id,
        strategy=_load_strategy(run_config.strategy, root=run_config.root, params=strategy_params),
        run_directory=_run_directory(backtest, root=run_config.root, run_id=run_config.run_id),
        normalized_config=_normalized_config(
            run_id=run_config.run_id,
            strategy=run_config.strategy,
            strategy_params=strategy_params,
            backtest=backtest,
            execution=execution_config,
            account=account_config,
            data=data,
            market_policy=market_policy,
            timeline=timeline_config,
        ),
        data=data,
        account_config=account_config,
        market_policy=market_policy,
        backtest_config=backtest,
        execution_config=execution_config,
    )


def _market_policy(backtest: Mapping[str, object]) -> ReplayMarketDataPolicy:
    if backtest.get("events") is not None:
        raise BacktestConfigurationError("backtest.events is no longer supported; use strategy context.subscribe(...) with [backtest.market]")
    if backtest.get("dataset") is not None:
        raise BacktestConfigurationError("backtest.dataset is no longer supported; use strategy context.subscribe(...) with [backtest.market]")
    market = _table(backtest.get("market"), "backtest.market", allow_none=False)
    if market.get("start") is None:
        raise BacktestConfigurationError("backtest.market.start is required")
    if market.get("end") is None:
        raise BacktestConfigurationError("backtest.market.end is required")
    try:
        return ReplayMarketDataPolicy(
            start=market["start"],
            end=market["end"],
            on_missing=str(market.get("on_missing") or "error"),
        )
    except ValueError as error:
        raise BacktestConfigurationError(str(error)) from error


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
    data: BacktestMarketDataService,
    market_policy: ReplayMarketDataPolicy,
    timeline: Mapping[str, object],
) -> Mapping[str, object]:
    return {
        "run": {"id": run_id, "mode": RuntimeMode.BACKTEST.value, "strategy": strategy},
        "strategy": {"params": dict(strategy_params)},
        "backtest": {
            **{str(key): _jsonable(value) for key, value in backtest.items()},
            "data_root": str(data.store.root),
            "storage_format": data.store.storage_format,
            "market": {
                "start": market_policy.start,
                "end": market_policy.end,
                "on_missing": market_policy.on_missing,
            },
        },
        "account": {
            "cash": account.cash,
            "currency": account.currency,
            "fee_rate": account.fee_rate,
            "price_field": account.price_field,
        },
        "execution": dict(execution),
        "timeline": dict(timeline),
    }


def _table(value: object, name: str, *, allow_none: bool = True) -> Mapping[str, object]:
    return common_table(value, name, BacktestConfigurationError, allow_none=allow_none)


def _default_text(value: object, default: str) -> str:
    return value if isinstance(value, str) and value.strip() else default


def _resolve_path(value: object, *, root: Path, source: str) -> Path:
    return common_resolve_path(value, root=root, source=source, error_type=BacktestConfigurationError)


def _jsonable(value: object) -> object:
    return common_jsonable(value)


__all__ = [
    "BacktestAccountConfig",
    "BacktestConfigurationError",
    "BacktestRunResult",
    "ConfiguredBacktest",
    "configured_backtest",
]
