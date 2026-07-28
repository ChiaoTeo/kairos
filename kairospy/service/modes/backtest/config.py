from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
import importlib
import json
from pathlib import Path
import sys
from typing import Mapping

from kairospy.config import ConfigError, load_run_config
from kairospy.context import DataContext, StrategyContext
from kairospy.service.domains.execution import BasisPointSlippageModel, ImmediateFillModel
from kairospy.core.market import MarketSubscription, MarketSubscriptionRegistry
from kairospy.core.reference import MarketResolver
from kairospy.data import DataStore
from kairospy.modes.backtest import BacktestEngine, SimulatedAccount
from kairospy.runtime import IterableEventSource
from kairospy.runtime.line import RuntimeMode
from kairospy.service.domains.market import MarketDataResolver, MarketDataService


class BacktestConfigurationError(ValueError):
    pass


class BacktestSourceKind(StrEnum):
    EVENTS = "events"
    DATASET = "dataset"


@dataclass(frozen=True, slots=True)
class ConfiguredBacktest:
    run_id: str
    engine: BacktestEngine
    source: IterableEventSource
    source_kind: BacktestSourceKind
    source_value: str
    run_directory: Path
    normalized_config: Mapping[str, object]


def configured_backtest(config_path: Path) -> ConfiguredBacktest:
    try:
        run_config = load_run_config(config_path)
        run_config.require_mode(RuntimeMode.BACKTEST.value)
    except ConfigError as error:
        raise BacktestConfigurationError(str(error)) from error

    values = run_config.values
    strategy_params = _table(values.get("strategy"), "strategy").get("params", {})
    if not isinstance(strategy_params, Mapping):
        raise BacktestConfigurationError("[strategy.params] must be a table")
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
    strategy = _load_strategy(run_config.strategy, root=run_config.root, params=strategy_params)
    subscription_strategy = _load_strategy(run_config.strategy, root=run_config.root, params=strategy_params)
    source, source_kind, source_value = _event_source(
        backtest,
        data_context,
        subscription_strategy,
        market_resolver,
        root=run_config.root,
    )
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
    return ConfiguredBacktest(
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
    strategy: object,
    market_resolver: MarketResolver,
    *,
    root: Path,
) -> tuple[IterableEventSource, BacktestSourceKind, str]:
    stream = str(backtest.get("stream") or "backtest")
    if backtest.get("events") is not None:
        path = _resolve_path(backtest["events"], root=root, source="backtest.events")
        return IterableEventSource(stream if stream != "backtest" else path.stem, _read_jsonl(path)), BacktestSourceKind.EVENTS, str(path)
    dataset = backtest.get("dataset")
    query_start = backtest.get("start")
    query_end = backtest.get("end")
    query_limit = _optional_int(backtest.get("limit"), "backtest.limit")
    if dataset is None:
        dataset = _dataset_from_strategy_subscription(backtest, data_context, strategy, market_resolver)
    if dataset is None:
        raise BacktestConfigurationError(
            "backtest.events, backtest.dataset, or an on_start market data subscription is required"
        )
    dataset_name = str(dataset)
    rows = data_context.store.read_rows(
        dataset_name,
        start=query_start,
        end=query_end,
        limit=query_limit,
    )
    if not rows:
        raise BacktestConfigurationError(f"backtest dataset has no rows: {dataset_name}")
    return IterableEventSource(stream if stream != "backtest" else dataset_name, rows), BacktestSourceKind.DATASET, dataset_name


def _dataset_from_strategy_subscription(
    backtest: Mapping[str, object],
    data_context: DataContext,
    strategy: object,
    market_resolver: MarketResolver,
) -> str | None:
    subscriptions = _discover_on_start_subscriptions(strategy, data_context, market_resolver)
    if not subscriptions:
        return None
    subscription = _primary_backtest_subscription(subscriptions)
    venue = subscription.venue or str(backtest.get("venue", "simulated"))
    market = subscription.market or str(backtest.get("market", "spot"))
    service = MarketDataService(
        data_context.store,
        MarketDataResolver(default_venue=venue, default_market=market),
    )
    return service.resolve_subscription(subscription).dataset_id


def _discover_on_start_subscriptions(
    strategy: object,
    data_context: DataContext,
    market_resolver: MarketResolver,
) -> tuple[MarketSubscription, ...]:
    registry = MarketSubscriptionRegistry()
    context = StrategyContext(
        data_context,
        strategy_id=getattr(strategy, "strategy_id", "strategy"),
        phase="start",
        market_resolver=market_resolver,
        _subscriptions=registry,
    )
    strategy.on_start(context)
    return registry.list()


def _primary_backtest_subscription(subscriptions: tuple[MarketSubscription, ...]) -> MarketSubscription:
    bar_subscriptions = [
        subscription
        for subscription in subscriptions
        if any(field.family == "bar" for field in subscription.spec.fields)
    ]
    return bar_subscriptions[0] if bar_subscriptions else subscriptions[0]


def _load_strategy(ref: str | None, *, root: Path, params: Mapping[str, object]) -> object:
    if ref is None or ":" not in ref:
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
    strategy = factory(**dict(params))
    if not hasattr(strategy, "on_market"):
        raise BacktestConfigurationError(f"strategy factory did not return a Strategy: {ref}")
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
        raise BacktestConfigurationError("backtest.storage_format must be parquet or jsonl")
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


def _table(value: object, name: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise BacktestConfigurationError(f"[{name}] must be a table")
    return value


def _optional_int(value: object, source: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise BacktestConfigurationError(f"{source} must be an integer")
    try:
        parsed = int(value)
    except Exception as error:
        raise BacktestConfigurationError(f"{source} must be an integer") from error
    if parsed < 0:
        raise BacktestConfigurationError(f"{source} cannot be negative")
    return parsed


def _jsonable(value: object) -> object:
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


__all__ = ["BacktestConfigurationError", "BacktestSourceKind", "ConfiguredBacktest", "configured_backtest"]
