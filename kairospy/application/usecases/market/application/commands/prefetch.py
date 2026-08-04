from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kairospy.application.support.runtime.application.interaction import SystemCallDecision, SystemCallResult
from kairospy.application.support.runtime.application.views import ViewStore
from kairospy.application.support.runtime.domain.commands import CommandHandle, RuntimeCommand, RuntimeCommandStatus
from kairospy.application.usecases.market.application.component import MarketApplication
from .source import MarketSourceQueryService
from .resources import DriverName, ExchangeName, MarketCommandResources
from kairospy.application.usecases.market.application.runtime import build_backtest_market
from kairospy.application.usecases.market.application.data import MarketDataSpec
from kairospy.application.usecases.market.application.replay import specs_from_subscription
from kairospy.application.usecases.market.application.resolver import MarketDataResolver
from kairospy.application.usecases.strategy.application.context import StrategyContext
from kairospy.application.usecases.strategy.protocol import Strategy
from kairospy.application.support.launch.application.configuration import BacktestConfigurationError, configured_backtest
from kairospy.domain.reference import MarketResolver


@dataclass(slots=True)
class _PlanningInteraction:
    market: MarketApplication

    def __post_init__(self) -> None:
        self.handles: dict[str, CommandHandle] = {}

    def call(self, command: RuntimeCommand) -> SystemCallResult:
        existing = self.handles.get(command.command_id)
        if existing is not None:
            return _result(existing)
        handle = CommandHandle(command.command_id, command.kind)
        self.handles[command.command_id] = handle
        if command.kind != "market.subscribe":
            handle._reject(f"unsupported planning command: {command.kind}")
            return _result(handle)
        if not hasattr(command.payload, "market"):
            handle._reject("market.subscribe requires a typed subscription specification")
            return _result(handle)
        subscription = self.market.subscriptions.subscribe(command.payload)  # type: ignore[arg-type]
        handle._accept({"subscription_id": subscription.key})
        return _result(handle)

    def apply_event(self, event: object) -> None:
        return None


def _result(handle: CommandHandle) -> SystemCallResult:
    decision = {
        RuntimeCommandStatus.DEFERRED: SystemCallDecision.DEFERRED,
        RuntimeCommandStatus.IGNORED: SystemCallDecision.IGNORED,
        RuntimeCommandStatus.REJECTED: SystemCallDecision.REJECTED,
    }.get(handle.status, SystemCallDecision.ACCEPTED)
    return SystemCallResult(
        request_id=handle.command_id,
        decision=decision,
        handle=handle,
        result=handle.result,
        error=handle.error,
    )


class MarketBacktestPrefetchCommandService:
    """Builds a historical download plan from strategy subscription commands."""

    def __init__(self, resources: MarketCommandResources) -> None:
        self._resources = resources

    def prefetch(
        self,
        *,
        config_path: str | Path,
        driver_name: DriverName,
        limit: int,
        mode: str,
        dry_run: bool = False,
    ) -> dict[str, object]:
        try:
            configured = configured_backtest(Path(config_path))
        except BacktestConfigurationError as error:
            raise ValueError(str(error)) from error

        subscriptions = self._subscriptions(configured.strategy)
        if not subscriptions:
            raise ValueError("strategy did not subscribe to market data")
        data = build_backtest_market(
            self._resources.data_store(configured.data_root, configured.storage_format),
            resolver=MarketDataResolver(
                MarketResolver(
                    default_venue=configured.default_venue,
                    default_market=configured.default_market,
                )
            ),
            policy=configured.market_policy,
        )
        source_query = MarketSourceQueryService(self._resources)
        downloads: list[dict[str, object]] = []
        for subscription in subscriptions:
            for spec in _historical_specs(subscription.spec, configured.market_policy.start, configured.market_policy.end, limit):
                exchange_name = _exchange_name(spec.venue)
                capability = source_query.check(
                    symbol=spec.symbol,
                    exchange_name=exchange_name,
                    market=spec.market,
                    kind=spec.kind,
                    data_mode="historical",
                    timeframe=spec.timeframe,
                    driver_name=driver_name,
                )
                if not capability["valid"]:
                    raise ValueError(str(capability.get("reason") or "historical market data is not supported"))
                resolved = data.resolve(spec)
                path = None if dry_run else data.download(spec, self._resources.public_market_access(exchange_name, driver_name), mode=mode)
                downloads.append(
                    {
                        "subscription": subscription.key,
                        "dataset": resolved.dataset_id,
                        "path": None if path is None else str(path),
                        "kind": spec.kind,
                        "symbol": spec.symbol,
                        "venue": spec.venue,
                        "market": spec.market,
                        "timeframe": spec.timeframe,
                        "start": spec.start,
                        "end": spec.end,
                        "supported": True,
                        "status": capability["capability"]["status"],
                    }
                )
        return {
            "launch_id": configured.launch_id,
            "config": str(Path(config_path)),
            "dry_run": dry_run,
            "count": len(downloads),
            "plan": downloads,
            "downloads": () if dry_run else downloads,
        }

    @staticmethod
    def _subscriptions(strategy: Strategy) -> tuple[object, ...]:
        interaction = _PlanningInteraction(MarketApplication())
        context = StrategyContext(
            strategy.strategy_id,
            system_call=interaction,
            views=ViewStore(),
        )
        strategy.on_start(context)
        return tuple(interaction.market.subscriptions.subscriptions())


def _historical_specs(subscription: object, start: object, end: object, limit: int) -> tuple[MarketDataSpec, ...]:
    specs = specs_from_subscription(subscription, start=start, end=end)
    if any(spec.kind != "ohlcv" for spec in specs):
        kinds = ", ".join(sorted({spec.kind for spec in specs}))
        raise ValueError(f"historical prefetch only supports ohlcv dataset subscriptions, got {kinds}")
    return tuple(
        MarketDataSpec(
            spec.symbol,
            spec.kind,
            venue=spec.venue,
            market=spec.market,
            timeframe=spec.timeframe,
            start=spec.start,
            end=spec.end,
            limit=limit,
            dataset=spec.dataset,
            stream=spec.stream,
        )
        for spec in specs
    )


def _exchange_name(value: object) -> ExchangeName:
    try:
        return ExchangeName(str(value))
    except ValueError as error:
        raise ValueError(f"unsupported market data exchange: {value}") from error


__all__ = ["MarketBacktestPrefetchCommandService"]
