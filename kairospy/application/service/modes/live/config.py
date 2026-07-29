from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
import importlib
from pathlib import Path
import sys
from typing import Mapping

from kairospy.application.runtime import RuntimeMode, RuntimeRunSpec, RuntimeRunner
from kairospy.application.runtime.services import MarketDataSubscriptionSpec
from kairospy.application.runtime.services.account import account_current_view_key
from kairospy.application.service.system.run.accounts import AccountRegistry, RuntimeAccount
from kairospy.application.strategy import Strategy
from kairospy.config import ConfigError, load_run_config
from kairospy.core.account import AccountContext, AccountRef, Environment
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.market import Quote
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.integrations import BinanceBroker, BinanceMarketDataConnector, CcxtDriver, OkxBroker, OkxMarketDataConnector
from kairospy.infrastructure.integrations.payloads import CcxtAccountPayloadAdapter
from kairospy.infrastructure.integrations.protocols import BrokerClient, LiveMarketDataFeed

from .account import LiveAccountService
from .execution import LiveExecutionService, LiveTradingSafetyPolicy
from .market import LiveMarketDataService
from .state import JsonLiveRuntimeStateStore, LiveRuntimeStateStore


class LiveConfigurationError(ValueError):
    pass


BrokerFactory = Callable[[str, str | None], BrokerClient]
MarketFeedFactory = Callable[[str], LiveMarketDataFeed]


@dataclass(frozen=True, slots=True)
class LiveRunResult:
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
    state_store: LiveRuntimeStateStore | None = None

    def run(self) -> LiveRunResult:
        self._restore_state()
        self.account.refresh()
        runtime = RuntimeRunner.run_sync(
            RuntimeRunSpec(
                run_id=self.run_id,
                mode=RuntimeMode.LIVE,
                strategy=self.strategy,
                source=self.market_data,
                data=self.market_data,
                account=self.account,
                execution=self.coordinator,
                providers=(self.execution,),
            )
        )
        self._save_state()
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
    try:
        run_config = load_run_config(config_path)
        run_config.require_mode(RuntimeMode.LIVE.value)
    except ConfigError as error:
        raise LiveConfigurationError(str(error)) from error
    if run_config.strategy is None:
        raise LiveConfigurationError("run.strategy is required")
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
    state_store = JsonLiveRuntimeStateStore(_state_path(live, root=run_config.root, run_id=run_config.run_id))
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
        state_store=state_store,
    )


def _load_strategy(ref: str, *, root: Path, params: Mapping[str, object]) -> Strategy:
    if ":" not in ref:
        raise LiveConfigurationError("run.strategy must be module:callable")
    module_name, attr_name = ref.split(":", 1)
    inserted = False
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
        inserted = True
    if inserted:
        sys.modules.pop(module_name, None)
    try:
        module = importlib.import_module(module_name)
    finally:
        if inserted:
            try:
                sys.path.remove(str(root))
            except ValueError:
                pass
    factory = getattr(module, attr_name)
    strategy = factory(**dict(params)) if callable(factory) else factory
    if not hasattr(strategy, "strategy_id"):
        raise LiveConfigurationError(f"strategy factory did not return a Strategy: {ref}")
    return strategy


def _strategy_params(values: Mapping[str, object]) -> Mapping[str, object]:
    strategy = _table(values.get("strategy"), "strategy") if values.get("strategy") is not None else {}
    params = strategy.get("params", {})
    if not isinstance(params, Mapping):
        raise LiveConfigurationError("[strategy.params] must be a table")
    return params


def _configured_account(accounts: object, *, venue: str, mode_config: Mapping[str, object]) -> RuntimeAccount:
    try:
        return AccountRegistry.from_config(accounts).resolve(  # type: ignore[arg-type]
            venue=venue,
            account=_account_selector(mode_config.get("account"), "live.account"),
            account_id=_optional_text(mode_config.get("account_id"), "live.account_id"),
            account_index=_optional_int(mode_config.get("account_index"), "live.account_index"),
        )
    except ValueError as error:
        raise LiveConfigurationError(str(error)) from error


def _safety_policy(raw: object) -> LiveTradingSafetyPolicy:
    safety = _table(raw, "live.safety") if raw is not None else {}
    max_notional = safety.get("max_order_notional")
    return LiveTradingSafetyPolicy(
        trading_enabled=_bool_value(safety.get("trading_enabled", False), "live.safety.trading_enabled"),
        require_limit_orders=_bool_value(safety.get("require_limit_orders", True), "live.safety.require_limit_orders"),
        max_order_notional=None if max_notional is None else Decimal(str(max_notional)),
    )


def _default_market_feed(venue: str) -> LiveMarketDataFeed:
    normalized = venue.strip().lower()
    if normalized == "binance":
        return BinanceMarketDataConnector(CcxtDriver())
    if normalized in {"okx", "okex"}:
        return OkxMarketDataConnector()
    raise LiveConfigurationError(f"unsupported live market data venue: {venue}")


def _default_broker(venue: str, credential: str | None) -> BrokerClient:
    normalized = venue.strip().lower()
    if normalized == "binance":
        return BinanceBroker(CcxtDriver())
    if normalized in {"okx", "okex"}:
        return OkxBroker.from_credential(credential)
    raise LiveConfigurationError(f"unsupported live broker venue: {venue}")


def _params_table(value: object, *, default: Mapping[str, object] | None = None) -> Mapping[str, object]:
    values = dict(default or {})
    if value is None:
        return values
    if not isinstance(value, Mapping):
        raise LiveConfigurationError("live params must be a table")
    values.update(value)
    return values


def _state_path(live: Mapping[str, object], *, root: Path, run_id: str) -> Path:
    value = live.get("state_path")
    if value is None:
        return root / ".kairos" / "runs" / RuntimeMode.LIVE.value / run_id / "live_state.json"
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = root / path
    return path


def _table(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise LiveConfigurationError(f"[{name}] must be a table")
    return value


def _required_text(value: object, source: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise LiveConfigurationError(f"{source} is required")
    return text


def _optional_text(value: object, source: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, source)


def _optional_int(value: object, source: str) -> int | None:
    if value is None:
        return None
    return _int_value(value, source)


def _account_selector(value: object, source: str) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise LiveConfigurationError(f"{source} must be an account id or integer account index")
    if isinstance(value, int):
        return value
    return _required_text(value, source)


def _bool_value(value: object, source: str) -> bool:
    if not isinstance(value, bool):
        raise LiveConfigurationError(f"{source} must be a boolean")
    return value


def _int_value(value: object, source: str) -> int:
    if not isinstance(value, int):
        raise LiveConfigurationError(f"{source} must be an integer")
    if value < 0:
        raise LiveConfigurationError(f"{source} cannot be negative")
    return value


__all__ = ["ConfiguredLive", "LiveConfigurationError", "LiveRunResult", "configured_live"]
