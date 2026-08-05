from __future__ import annotations

from typing import Mapping

from kairospy.application.usecases.market.application.resolver import MarketDataResolver
from kairospy.application.usecases.market.application.requests import MarketDataSpec
from kairospy.application.usecases.market.protocol import MarketHistoricalClient
from kairospy.application.support.launch.application.configuration import ConfiguredBacktest
from kairospy.application.support.launch.application.results import backtest_result
from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.actor.support.services.connections import IntegrationConnectionScope
from kairospy.application.usecases.market.application.runtime import BacktestMarketDataService, build_backtest_market
from kairospy.application.system.application.business import SystemApplication
from kairospy.application.support.composition.application.resources import DriverName, ExchangeName, historical_market_access
from kairospy.application.support.composition.application.accounts import BacktestAccountResources
from kairospy.domain.reference import MarketResolver
from kairospy.infrastructure.persistence.application.market_data import DataStore

from .common import ComposedLaunch, in_memory_message_bus, optional_default_text, reference_runtime
from .runtime import compose_runtime_assembly
from .notifications import build_notification_application, notification_runtime_settings


class BacktestComposition:
    def compose(self, configured: ConfiguredBacktest) -> ComposedLaunch:
        data = backtest_market_data_service(configured)
        self._configure_market_downloads(configured, data)
        account_resources = BacktestAccountResources.from_configured(configured)
        connections = IntegrationConnectionScope()
        from kairospy.application.system.application.resources import TradingSystemResources

        resources = TradingSystemResources(
            business=SystemApplication(),
            data=data,
            reference=reference_runtime(
                configured.launch_directory,
                default_venue=_backtest_default_venue(configured.backtest_config),
                default_market=_backtest_default_market(configured.backtest_config),
            ),
            connection_scope=connections,
            message_bus=in_memory_message_bus(),
            notifications=build_notification_application(configured.normalized_config),
            notification_settings=notification_runtime_settings(configured.normalized_config),
            assembly=compose_runtime_assembly(),
        )
        return ComposedLaunch(
            mode=RuntimeMode.BACKTEST,
            launch_id=configured.launch_id,
            strategy=configured.strategy,
            launch_directory=configured.launch_directory,
            normalized_config=configured.normalized_config,
            resources=resources,
            lifecycle=None,
            build_result=lambda runtime: backtest_result(configured, account_resources, runtime),
        )

    def _configure_market_downloads(self, configured: ConfiguredBacktest, data: BacktestMarketDataService) -> None:
        if configured.market_policy.on_missing != "download":
            return

        def factory(spec: MarketDataSpec) -> MarketHistoricalClient:
            return historical_market_access(
                _exchange_name(spec.venue),
                DriverName.ccxt,
                product=_product_family(spec.market),
            )

        data.set_historical_client_factory(factory)


def backtest_market_data_service(configured: ConfiguredBacktest) -> BacktestMarketDataService:
    return build_backtest_market(
        DataStore(configured.data_root, storage_format=configured.storage_format),
        resolver=MarketDataResolver(
            MarketResolver(
                default_venue=configured.default_venue,
                default_market=configured.default_market,
            )
        ),
        policy=configured.market_policy,
    )


def _backtest_default_venue(config: Mapping[str, object]) -> str:
    return optional_default_text(config.get("venue")) or "simulated"


def _backtest_default_market(config: Mapping[str, object]) -> str:
    market = config.get("market")
    if isinstance(market, Mapping):
        return optional_default_text(market.get("market")) or "spot"
    return optional_default_text(config.get("market")) or "spot"


def _exchange_name(value: object) -> ExchangeName:
    try:
        return ExchangeName(str(value))
    except ValueError as error:
        raise ValueError(f"unsupported exchange: {value}") from error


def _product_family(value: object) -> object:
    from kairospy.infrastructure.integrations.domain import ProductFamily

    text = str(value).strip().lower()
    if text in {"equity", "stock", "stocks"}:
        return ProductFamily.SPOT
    if text in {"swap", "perp", "perpetual", "future", "futures", "usd_margined_futures"}:
        return ProductFamily.USD_M_FUTURES
    return ProductFamily.SPOT


__all__ = ["BacktestComposition", "backtest_market_data_service"]
