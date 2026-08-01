from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Protocol

from kairospy.config import LaunchAccountConfig
from kairospy.application.support.launch.modes import RuntimeMode
from kairospy.application.usecases.market.subscriptions import MarketDataSubscriptionSpec
from kairospy.application.usecases.account.bootstrap import AccountBootstrapGateway
from kairospy.application.usecases.strategy.protocol import Strategy
from kairospy.core.account import AccountBookRef, AccountContext
from kairospy.application.support.runtime.services.market.feed import MarketStreamGateway

from kairospy.application.support.launch.config.common import (
    AccountPerformanceMixin,
    AccountResolver,
    ConfiguredAccount,
    FeedConfig,
    bool_value as common_bool_value,
    configured_account_ref as common_configured_account_ref,
    int_value as common_int_value,
    load_required_launch_config,
    load_strategy as common_load_strategy,
    parse_feeds as common_parse_feeds,
    strategy_params as common_strategy_params,
    table as common_table,
)
from kairospy.application.support.runtime.services.execution.live import LiveTradingSafetyPolicy
from kairospy.application.support.runtime.services.market.modes.live import LiveMarketDataService


class LiveConfigurationError(ValueError):
    pass


class BrokerFactory(Protocol):
    def __call__(self, account: AccountBookRef, credential: str | None) -> AccountBootstrapGateway:
        ...


class MarketFeedFactory(Protocol):
    def __call__(self, source: str) -> MarketStreamGateway:
        ...


class MarketFeedResolverFactory(Protocol):
    def __call__(self, spec: MarketDataSubscriptionSpec) -> MarketStreamGateway | None:
        ...


class MarketFeedResolverBuilder(Protocol):
    def __call__(self, feeds: Mapping[str, FeedConfig]) -> MarketFeedResolverFactory:
        ...


@dataclass(frozen=True, slots=True)
class LiveLaunchResult(AccountPerformanceMixin):
    launch_id: str
    mode: RuntimeMode
    runtime: object
    views: object
    intents: object
    account: AccountContext
    account_view: object | None
    fills: tuple[object, ...] = ()
    trades: tuple[object, ...] = ()
    decision_trace: tuple[object, ...] = ()
    risk_snapshots: tuple[object, ...] = ()
    metrics: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConfiguredLive:
    launch_id: str
    strategy: Strategy
    market_data: LiveMarketDataService
    account_config: ConfiguredAccount
    launch_account_configs: Mapping[str, ConfiguredAccount]
    broker_factory: BrokerFactory | None
    launch_accounts: Mapping[str, LaunchAccountConfig]
    safety_policy: LiveTradingSafetyPolicy
    private_sync: LivePrivateSyncConfig
    normalized_config: Mapping[str, object]
    launch_directory: Path
    state_path: Path


@dataclass(frozen=True, slots=True)
class LivePrivateSyncConfig:
    enabled: bool
    max_balance_events: int = 0
    max_order_events: int = 0
    max_trade_events: int = 0


def configured_live(
    config_path: Path,
    *,
    market_feed_factory: MarketFeedFactory | None = None,
    market_feed_resolver: MarketFeedResolverFactory | None = None,
    market_feed_resolver_builder: MarketFeedResolverBuilder | None = None,
    broker_factory: BrokerFactory | None = None,
    account_resolver: AccountResolver | None = None,
    strategy_ref: str | None = None,
) -> ConfiguredLive:
    launch_config = load_required_launch_config(config_path, mode=RuntimeMode.LIVE, error_type=LiveConfigurationError, strategy_ref=strategy_ref)
    live = _table(launch_config.values.get("live"), "live")
    feeds_config = common_parse_feeds(launch_config.values.get("feeds"), error_type=LiveConfigurationError)
    timeline_config = _table(launch_config.values.get("timeline"), "timeline") if launch_config.values.get("timeline") is not None else {}
    account_ref = launch_config.account_ref or _primary_launch_account_ref(launch_config.launch_accounts)
    account_config = _configured_account(account_ref, account_resolver=account_resolver, venue=None)
    launch_account_configs = _configured_launch_accounts(launch_config.launch_accounts, account_resolver=account_resolver, venue=None)
    venue = account_config.venue
    feed_resolver = market_feed_resolver or (market_feed_resolver_builder(feeds_config) if market_feed_resolver_builder is not None else _market_feed_resolver(market_feed_factory))
    state_path = _state_path(live, root=launch_config.root, launch_id=launch_config.launch_id)
    market_data = LiveMarketDataService(feed_resolver=feed_resolver, source_name=str(live.get("source_name") or f"{venue}-live"))
    return ConfiguredLive(
        launch_id=launch_config.launch_id,
        strategy=_load_strategy(launch_config.strategy, root=launch_config.root, params=_strategy_params(launch_config.values)),
        market_data=market_data,
        account_config=account_config,
        launch_account_configs=launch_account_configs,
        broker_factory=broker_factory,
        launch_accounts=launch_config.launch_accounts,
        safety_policy=_safety_policy(live.get("safety")),
        private_sync=_private_sync_config(live, account_ref=account_ref),
        normalized_config={
            "launch": {"id": launch_config.launch_id, "mode": RuntimeMode.LIVE.value, "strategy": launch_config.strategy},
            "strategy": {"params": dict(_strategy_params(launch_config.values))},
            "live": dict(live),
            "feeds": {key: dict(feed.values or {}) for key, feed in feeds_config.items()},
            "account": {"ref": account_ref, "account_id": account_config.account_id, "venue": venue, "currency": account_config.currency},
            "accounts": {key: {"ref": value.ref, "index": value.index, "books": list(value.books), "trade": value.trade} for key, value in launch_config.launch_accounts.items()},
            "timeline": dict(timeline_config),
        },
        launch_directory=state_path.parent,
        state_path=state_path,
    )


def _load_strategy(ref: str, *, root: Path, params: Mapping[str, object]) -> Strategy:
    return common_load_strategy(ref, root=root, params=params, error_type=LiveConfigurationError)


def _strategy_params(values: Mapping[str, object]) -> Mapping[str, object]:
    return common_strategy_params(values, LiveConfigurationError)


def _configured_account(account_ref: str | None, *, account_resolver: AccountResolver | None, venue: str | None) -> ConfiguredAccount:
    account = common_configured_account_ref(
        account_ref,
        account_resolver=account_resolver,
        venue=venue,
        mode_label="live",
        error_type=LiveConfigurationError,
    )
    if account.environment and account.environment not in {"live", "testnet"}:
        raise LiveConfigurationError(
            f"account {account.account_id!r} has environment {account.environment!r}; live launches require a live account"
        )
    return account


def _configured_launch_accounts(
    accounts: Mapping[str, LaunchAccountConfig],
    *,
    account_resolver: AccountResolver | None,
    venue: str | None,
) -> Mapping[str, ConfiguredAccount]:
    return {alias: _configured_account(config.ref, account_resolver=account_resolver, venue=venue) for alias, config in accounts.items()}


def _primary_launch_account_ref(accounts: Mapping[str, object]) -> str | None:
    if not accounts:
        return None
    first = next(iter(accounts.values()))
    return str(getattr(first, "ref", "") or "") or None


def _safety_policy(raw: object) -> LiveTradingSafetyPolicy:
    safety = _table(raw, "live.safety") if raw is not None else {}
    max_notional = safety.get("max_order_notional")
    return LiveTradingSafetyPolicy(
        trading_enabled=_bool_value(safety.get("trading_enabled", False), "live.safety.trading_enabled"),
        require_limit_orders=_bool_value(safety.get("require_limit_orders", True), "live.safety.require_limit_orders"),
        max_order_notional=None if max_notional is None else Decimal(str(max_notional)),
    )


def _market_feed_resolver(factory: MarketFeedFactory | None) -> MarketFeedResolverFactory:
    if factory is not None:
        return lambda spec: factory(str(spec.market.venue))
    return _missing_market_feed


def _missing_market_feed(spec: MarketDataSubscriptionSpec) -> MarketStreamGateway:
    venue = str(spec.market.venue)
    market = str(spec.market.market)
    symbol = str(spec.market.source_symbol)
    raise LiveConfigurationError(f"no market feed resolver configured for live subscription: venue={venue} market={market} symbol={symbol}")


def _private_sync_config(live: Mapping[str, object], *, account_ref: str | None) -> LivePrivateSyncConfig:
    private_sync = live.get("private_sync")
    if isinstance(private_sync, Mapping) and "enabled" in private_sync:
        enabled = _bool_value(private_sync.get("enabled"), "live.private_sync.enabled")
    else:
        enabled = account_ref is not None
    return LivePrivateSyncConfig(
        enabled=enabled,
        max_balance_events=_account_stream_limit(live, "max_balance_events"),
        max_order_events=_account_stream_limit(live, "max_order_events"),
        max_trade_events=_account_stream_limit(live, "max_trade_events"),
    )


def _account_stream_limit(live: Mapping[str, object], key: str) -> int:
    account_stream = live.get("account_stream")
    if isinstance(account_stream, Mapping) and key in account_stream:
        return _int_value(account_stream.get(key), f"live.account_stream.{key}")
    return 0


def _state_path(live: Mapping[str, object], *, root: Path, launch_id: str) -> Path:
    value = live.get("state_path")
    if value is None:
        return Path(".kairos/launches").resolve() / RuntimeMode.LIVE.value / launch_id / "live_state.json"
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = root / path
    return path


def _table(value: object, name: str) -> Mapping[str, object]:
    return common_table(value, name, LiveConfigurationError, allow_none=False)


def _bool_value(value: object, source: str) -> bool:
    return common_bool_value(value, source, LiveConfigurationError)


def _int_value(value: object, source: str) -> int:
    return common_int_value(value, source, LiveConfigurationError)


__all__ = ["ConfiguredLive", "LiveConfigurationError", "LiveLaunchResult", "LivePrivateSyncConfig", "configured_live"]
