from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from kairospy.application.runtime import RuntimeMode
from kairospy.application.runtime.ports import MarketDataSubscriptionSpec
from kairospy.application.strategy import Strategy
from kairospy.core.account import AccountContext
from kairospy.core.market import Quote
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.integrations.protocols import BrokerClient, LiveMarketDataFeed

from ..common import (
    AccountPerformanceMixin,
    AccountResolver,
    ConfiguredAccount,
    bool_value as common_bool_value,
    configured_account_ref as common_configured_account_ref,
    default_market_feed as common_default_market_feed,
    int_value as common_int_value,
    load_required_run_config,
    load_strategy as common_load_strategy,
    params_table as common_params_table,
    required_text as common_required_text,
    strategy_params as common_strategy_params,
    table as common_table,
)
from .execution import LiveTradingSafetyPolicy
from .market import LiveMarketDataService


class LiveConfigurationError(ValueError):
    pass


BrokerFactory = Callable[[str, str | None], BrokerClient]
MarketFeedFactory = Callable[[str], LiveMarketDataFeed]


@dataclass(frozen=True, slots=True)
class LiveRunResult(AccountPerformanceMixin):
    run_id: str
    mode: RuntimeMode
    runtime: object
    views: object
    intents: object
    controls: object
    account: AccountContext
    account_view: object | None
    fills: tuple[object, ...] = ()
    trades: tuple[object, ...] = ()
    decision_trace: tuple[object, ...] = ()
    risk_snapshots: tuple[object, ...] = ()
    metrics: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConfiguredLive:
    run_id: str
    strategy: Strategy
    market_data: LiveMarketDataService
    account_config: ConfiguredAccount
    live_config: Mapping[str, object]
    venue: str
    market: str
    symbol: str
    broker_factory: BrokerFactory | None
    balance_params: Mapping[str, object]
    order_params: Mapping[str, object]
    safety_policy: LiveTradingSafetyPolicy
    watch_private: bool
    max_balance_events: int
    max_order_events: int
    max_trade_events: int
    normalized_config: Mapping[str, object]
    run_directory: Path
    state_path: Path


def configured_live(
    config_path: Path,
    *,
    market_feed_factory: MarketFeedFactory | None = None,
    broker_factory: BrokerFactory | None = None,
    account_resolver: AccountResolver | None = None,
    strategy_ref: str | None = None,
) -> ConfiguredLive:
    run_config = load_required_run_config(config_path, mode=RuntimeMode.LIVE, error_type=LiveConfigurationError, strategy_ref=strategy_ref)
    live = _table(run_config.values.get("live"), "live")
    timeline_config = _table(run_config.values.get("timeline"), "timeline") if run_config.values.get("timeline") is not None else {}
    venue = _required_text(live.get("venue"), "live.venue")
    market = str(live.get("market", "spot"))
    symbol = _required_text(live.get("symbol"), "live.symbol")
    account_config = _configured_account(run_config.account_ref, account_resolver=account_resolver, venue=venue)
    market_resolver = MarketResolver(default_venue=venue, default_market=market)
    market_ref = market_resolver.resolve(symbol)
    feed = (market_feed_factory or _default_market_feed)(venue)
    state_path = _state_path(live, root=run_config.root, run_id=run_config.run_id)
    market_data = LiveMarketDataService(feed=feed, source_name=str(live.get("source_name") or f"{venue}-live"))
    market_data.subscribe(MarketDataSubscriptionSpec(market_ref, (Quote,), params=_params_table(live.get("stream"), default={"type": market})))
    balance_params = _params_table(live.get("balance_params"), default={"type": market})
    order_params = _params_table(live.get("order_params"), default={"type": market})
    return ConfiguredLive(
        run_id=run_config.run_id,
        strategy=_load_strategy(run_config.strategy, root=run_config.root, params=_strategy_params(run_config.values)),
        market_data=market_data,
        account_config=account_config,
        live_config=live,
        venue=venue,
        market=market,
        symbol=symbol,
        broker_factory=broker_factory,
        balance_params=balance_params,
        order_params=order_params,
        safety_policy=_safety_policy(live.get("safety")),
        watch_private=_bool_value(live.get("watch_private", False), "live.watch_private"),
        max_balance_events=_int_value(live.get("max_balance_events", 0), "live.max_balance_events"),
        max_order_events=_int_value(live.get("max_order_events", 0), "live.max_order_events"),
        max_trade_events=_int_value(live.get("max_trade_events", 0), "live.max_trade_events"),
        normalized_config={
            "run": {"id": run_config.run_id, "mode": RuntimeMode.LIVE.value, "strategy": run_config.strategy},
            "strategy": {"params": dict(_strategy_params(run_config.values))},
            "live": dict(live),
            "account": {"account_id": account_config.account_id, "venue": venue, "currency": account_config.currency},
            "timeline": dict(timeline_config),
        },
        run_directory=state_path.parent,
        state_path=state_path,
    )


def _load_strategy(ref: str, *, root: Path, params: Mapping[str, object]) -> Strategy:
    return common_load_strategy(ref, root=root, params=params, error_type=LiveConfigurationError)


def _strategy_params(values: Mapping[str, object]) -> Mapping[str, object]:
    return common_strategy_params(values, LiveConfigurationError)


def _configured_account(account_ref: str | None, *, account_resolver: AccountResolver | None, venue: str) -> ConfiguredAccount:
    account = common_configured_account_ref(
        account_ref,
        account_resolver=account_resolver,
        venue=venue,
        mode_label="live",
        error_type=LiveConfigurationError,
    )
    if account.environment and account.environment not in {"live", "testnet"}:
        raise LiveConfigurationError(
            f"account {account.account_id!r} has environment {account.environment!r}; live runs require a live account"
        )
    return account


def _safety_policy(raw: object) -> LiveTradingSafetyPolicy:
    safety = _table(raw, "live.safety") if raw is not None else {}
    max_notional = safety.get("max_order_notional")
    return LiveTradingSafetyPolicy(
        trading_enabled=_bool_value(safety.get("trading_enabled", False), "live.safety.trading_enabled"),
        require_limit_orders=_bool_value(safety.get("require_limit_orders", True), "live.safety.require_limit_orders"),
        max_order_notional=None if max_notional is None else Decimal(str(max_notional)),
    )


def _default_market_feed(venue: str) -> LiveMarketDataFeed:
    return common_default_market_feed(venue, mode_label="live", error_type=LiveConfigurationError)


def _params_table(value: object, *, default: Mapping[str, object] | None = None) -> Mapping[str, object]:
    return common_params_table(value, default=default, source="live", error_type=LiveConfigurationError)


def _state_path(live: Mapping[str, object], *, root: Path, run_id: str) -> Path:
    value = live.get("state_path")
    if value is None:
        return Path(".kairos/runs").resolve() / RuntimeMode.LIVE.value / run_id / "live_state.json"
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = root / path
    return path


def _table(value: object, name: str) -> Mapping[str, object]:
    return common_table(value, name, LiveConfigurationError, allow_none=False)


def _required_text(value: object, source: str) -> str:
    return common_required_text(value, source, LiveConfigurationError)


def _bool_value(value: object, source: str) -> bool:
    return common_bool_value(value, source, LiveConfigurationError)


def _int_value(value: object, source: str) -> int:
    return common_int_value(value, source, LiveConfigurationError)


__all__ = ["ConfiguredLive", "LiveConfigurationError", "LiveRunResult", "configured_live"]
