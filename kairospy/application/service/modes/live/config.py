from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping

from kairospy.config import load_run_config
from kairospy.application.context import DataContext
from kairospy.core.account import AccountContext, AccountRef, Environment
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.data import DataStore
from kairospy.infrastructure.integrations.connectors.exchange.okx import OkxBroker
from kairospy.infrastructure.integrations.payloads import CcxtAccountPayloadAdapter
from kairospy.application.mode.live import JsonLiveRuntimeStateStore, LiveEngine, LiveEngineDaemonTarget
from kairospy.application.runtime.control import RunDaemonTarget
from kairospy.application.runtime.model import RuntimeMode
from kairospy.application.service.domains.execution import LiveTradingSafetyPolicy
from kairospy.application.service.domains.market import IterableEventSource
from kairospy.application.service.operations.run.config import (
    BrokerFactory,
    ExchangeFactory,
    RunConfigurationError,
    _configured_account,
    _int_value,
    _load_strategy,
    _optional_int,
    _resolve_path,
    _table,
)


class _ConfiguredLiveTickerSourceFactory:
    def __init__(
        self,
        *,
        exchange_factory: ExchangeFactory,
        market_resolver: MarketResolver,
        symbol: str,
        params: Mapping[str, object] | None = None,
    ) -> None:
        self.exchange_factory = exchange_factory
        self.market_resolver = market_resolver
        self.symbol = symbol
        self.params = dict(params or {})

    def __call__(self, iteration: int) -> IterableEventSource:
        market = self.market_resolver.resolve(self.symbol)
        exchange_client = self.exchange_factory(market.venue)
        quote = exchange_client.fetch_quote(market, params=self.params)
        return IterableEventSource(f"{market.venue}.quote.{market.source_symbol}", (quote,))


def configured_live_target(
    config_path: Path,
    *,
    exchange_factory: ExchangeFactory,
    broker_factory: BrokerFactory | None = None,
) -> RunDaemonTarget:
    run_config = load_run_config(config_path)
    run_config.require_mode(RuntimeMode.LIVE.value)
    strategy = _load_strategy(run_config.strategy, root=run_config.root)
    live_config = _table(run_config.values.get(RuntimeMode.LIVE.value), RuntimeMode.LIVE.value)
    venue = str(live_config.get("venue", "")).strip()
    if not venue:
        raise RunConfigurationError("live.venue is required")
    market = str(live_config.get("market", "spot"))
    symbol = str(live_config.get("symbol", "")).strip()
    if not symbol:
        raise RunConfigurationError("live.symbol is required")
    account_config = _configured_account(run_config, RuntimeMode.LIVE)
    if account_config.venue != venue:
        raise RunConfigurationError(f"live account venue {account_config.venue!r} does not match live.venue {venue!r}")
    market_resolver = MarketResolver(default_venue=venue, default_market=market)
    market_resolver.resolve(symbol)
    broker = broker_factory(venue) if broker_factory is not None else _default_live_broker(venue, account_config.credential)
    state_path = _resolve_optional_path(
        live_config.get("state_path"),
        root=run_config.root,
        default=run_config.root / ".kairos" / "runs" / "live" / run_config.run_id / "state.json",
    )
    engine = LiveEngine(
        strategy,
        DataContext(DataStore(":unused:", storage_format="jsonl")),
        AccountContext(AccountRef(venue, account_config.account_id, market), Environment.LIVE),
        broker,
        account_payload_adapter=CcxtAccountPayloadAdapter(),
        equity_currency=str(live_config.get("equity_currency", account_config.currency)),
        state_store=JsonLiveRuntimeStateStore(state_path),
        market_resolver=market_resolver,
        trading_safety=_live_trading_safety(live_config.get("safety")),
    )
    stream_config = _table(live_config.get("stream"), "live.stream") if live_config.get("stream") is not None else {}
    account_stream_config = _table(live_config.get("account_stream"), "live.account_stream") if live_config.get("account_stream") is not None else {}
    return LiveEngineDaemonTarget(
        engine,
        _ConfiguredLiveTickerSourceFactory(
            exchange_factory=exchange_factory,
            market_resolver=market_resolver,
            symbol=symbol,
            params=_params_table(stream_config, default={"type": market}),
        ),
        symbol=symbol,
        balance_params=_params_table(live_config.get("balance_params"), default={"type": market}),
        order_params=_params_table(live_config.get("order_params"), default={"type": market}),
        max_balance_events=_int_value(account_stream_config.get("max_balance_events", 0), "live.account_stream.max_balance_events"),
        max_order_events=_int_value(account_stream_config.get("max_order_events", 0), "live.account_stream.max_order_events"),
        max_trade_events=_int_value(account_stream_config.get("max_trade_events", 0), "live.account_stream.max_trade_events"),
        max_iterations=_optional_int(live_config.get("max_iterations"), "live.max_iterations"),
    )


def _default_live_broker(venue: str, credential: str | None = None) -> object:
    normalized = venue.strip().lower()
    if normalized in {"okx", "okex"}:
        return OkxBroker.from_credential(credential)
    raise RunConfigurationError(f"unsupported live broker venue: {venue}")


def _live_trading_safety(raw: object) -> LiveTradingSafetyPolicy:
    safety = _table(raw, "live.safety") if raw is not None else {}
    trading_enabled = _bool_value(safety.get("trading_enabled", False), "live.safety.trading_enabled")
    require_limit_orders = _bool_value(safety.get("require_limit_orders", True), "live.safety.require_limit_orders")
    max_notional = safety.get("max_order_notional")
    return LiveTradingSafetyPolicy(
        trading_enabled=trading_enabled,
        require_limit_orders=require_limit_orders,
        max_order_notional=None if max_notional is None else Decimal(str(max_notional)),
    )


def _params_table(value: object, *, default: Mapping[str, object] | None = None) -> Mapping[str, object]:
    values = dict(default or {})
    if value is None:
        return values
    if not isinstance(value, Mapping):
        raise RunConfigurationError("live params must be a table")
    values.update(value)
    return values


def _resolve_optional_path(value: object, *, root: Path, default: Path) -> Path:
    if value is None:
        return default.resolve()
    return _resolve_path(value, root=root, source="live.state_path")


def _bool_value(value: object, source: str) -> bool:
    if not isinstance(value, bool):
        raise RunConfigurationError(f"{source} must be a boolean")
    return value


__all__ = ["configured_live_target"]
