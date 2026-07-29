from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from kairospy.application.runtime import RuntimeMode
from kairospy.application.runtime.ports import MarketDataSubscriptionSpec
from kairospy.application.runtime.processors.account import account_current_view_key
from kairospy.application.runtime.run import RuntimeRunResult
from kairospy.application.system.accounts import SystemAccount
from kairospy.application.system.run.state import JsonLiveRuntimeStateStore, LiveRuntimeStateStore
from kairospy.application.strategy import Strategy
from kairospy.core.account import AccountContext, AccountRef, Environment
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.market import Quote
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.integrations.payloads import CcxtAccountPayloadAdapter
from kairospy.infrastructure.integrations.protocols import BrokerClient, LiveMarketDataFeed

from .account import LiveAccountService
from ..common import (
    AccountPerformanceMixin,
    bool_value as common_bool_value,
    configured_account as common_configured_account,
    default_broker as common_default_broker,
    default_market_feed as common_default_market_feed,
    int_value as common_int_value,
    load_required_run_config,
    load_strategy as common_load_strategy,
    params_table as common_params_table,
    required_text as common_required_text,
    strategy_params as common_strategy_params,
    table as common_table,
)
from .execution import LiveExecutionService, LiveTradingSafetyPolicy
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
    metrics: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConfiguredLive:
    run_id: str
    strategy: Strategy
    market_data: LiveMarketDataService
    account: LiveAccountService
    execution: LiveExecutionService
    coordinator: ExecutionCoordinator
    normalized_config: Mapping[str, object]
    run_directory: Path
    state_store: LiveRuntimeStateStore | None = None

    def build_result(self, runtime: RuntimeRunResult) -> LiveRunResult:
        account_view = runtime.views.get(account_current_view_key(self.account.account), None)
        return LiveRunResult(
            run_id=self.run_id,
            mode=RuntimeMode.LIVE,
            runtime=runtime.runtime,
            views=runtime.views,
            intents=runtime.intents,
            controls=runtime.controls,
            account=self.account.account,
            account_view=account_view,
        )

    def prepare(self) -> None:
        self._restore_state()
        self.account.refresh()

    def complete(self) -> None:
        self._save_state()

    def _restore_state(self) -> None:
        if self.state_store is None:
            return
        snapshot = self.state_store.load()
        if snapshot is not None:
            snapshot.restore_into(self.coordinator, self.account.private_stream_state)

    def _save_state(self) -> None:
        if self.state_store is not None:
            self.state_store.save(self.coordinator, self.account.private_stream_state)


def configured_live(
    config_path: Path,
    *,
    market_feed_factory: MarketFeedFactory | None = None,
    broker_factory: BrokerFactory | None = None,
) -> ConfiguredLive:
    run_config = load_required_run_config(config_path, mode=RuntimeMode.LIVE, error_type=LiveConfigurationError)
    live = _table(run_config.values.get("live"), "live")
    venue = _required_text(live.get("venue"), "live.venue")
    market = str(live.get("market", "spot"))
    symbol = _required_text(live.get("symbol"), "live.symbol")
    account_config = _configured_account(run_config.accounts.values(), venue=venue, mode_config=live)
    market_resolver = MarketResolver(default_venue=venue, default_market=market)
    market_ref = market_resolver.resolve(symbol)
    feed = (market_feed_factory or _default_market_feed)(venue)
    broker = (broker_factory or _default_broker)(venue, account_config.credential)
    account = AccountContext(AccountRef(venue, account_config.account_id, market), Environment.LIVE)
    coordinator = ExecutionCoordinator(broker=broker, broker_symbol_resolver=market_resolver.broker_symbol)
    state_path = _state_path(live, root=run_config.root, run_id=run_config.run_id)
    state_store = JsonLiveRuntimeStateStore(state_path)
    market_data = LiveMarketDataService(feed=feed, source_name=str(live.get("source_name") or f"{venue}-live"))
    market_data.subscribe(MarketDataSubscriptionSpec(market_ref, (Quote,), params=_params_table(live.get("stream"), default={"type": market})))
    account_service = LiveAccountService(
        account,
        coordinator,
        broker=broker,
        parser=CcxtAccountPayloadAdapter(market_resolver),
        balance_params=_params_table(live.get("balance_params"), default={"type": market}),
        open_order_params=_params_table(live.get("order_params"), default={"type": market}),
        stream=broker if _bool_value(live.get("watch_private", False), "live.watch_private") else None,
        stream_symbol=symbol,
        max_balance_events=_int_value(live.get("max_balance_events", 0), "live.max_balance_events"),
        max_order_events=_int_value(live.get("max_order_events", 0), "live.max_order_events"),
        max_trade_events=_int_value(live.get("max_trade_events", 0), "live.max_trade_events"),
    )
    execution = LiveExecutionService(
        coordinator,
        account=account,
        snapshot_provider=account_service.snapshot,
        safety_policy=_safety_policy(live.get("safety")),
        order_params=_params_table(live.get("order_params"), default={"type": market}),
    )
    return ConfiguredLive(
        run_id=run_config.run_id,
        strategy=_load_strategy(run_config.strategy, root=run_config.root, params=_strategy_params(run_config.values)),
        market_data=market_data,
        account=account_service,
        execution=execution,
        coordinator=coordinator,
        normalized_config={
            "run": {"id": run_config.run_id, "mode": RuntimeMode.LIVE.value, "strategy": run_config.strategy},
            "strategy": {"params": dict(_strategy_params(run_config.values))},
            "live": dict(live),
            "account": {"account_id": account_config.account_id, "venue": venue, "currency": account_config.currency},
        },
        run_directory=state_path.parent,
        state_store=state_store,
    )


def _load_strategy(ref: str, *, root: Path, params: Mapping[str, object]) -> Strategy:
    return common_load_strategy(ref, root=root, params=params, error_type=LiveConfigurationError)


def _strategy_params(values: Mapping[str, object]) -> Mapping[str, object]:
    return common_strategy_params(values, LiveConfigurationError)


def _configured_account(accounts: object, *, venue: str, mode_config: Mapping[str, object]) -> SystemAccount:
    return common_configured_account(
        accounts,
        venue=venue,
        mode_config=mode_config,
        mode_label="live",
        error_type=LiveConfigurationError,
    )


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


def _default_broker(venue: str, credential: str | None) -> BrokerClient:
    return common_default_broker(venue, credential, mode_label="live", error_type=LiveConfigurationError)


def _params_table(value: object, *, default: Mapping[str, object] | None = None) -> Mapping[str, object]:
    return common_params_table(value, default=default, source="live", error_type=LiveConfigurationError)


def _state_path(live: Mapping[str, object], *, root: Path, run_id: str) -> Path:
    value = live.get("state_path")
    if value is None:
        return root / ".kairos" / "runs" / RuntimeMode.LIVE.value / run_id / "live_state.json"
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
