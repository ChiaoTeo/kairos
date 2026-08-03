from __future__ import annotations

from typing import Mapping

from kairospy.application.usecases.market.application.resolver import MarketDataResolver
from kairospy.application.usecases.market.domain.specs import MarketDataSpec
from kairospy.application.support.launch.application.configuration import ConfiguredBacktest
from kairospy.application.support.runtime.domain.modes import RuntimeMode
from kairospy.application.support.runtime.domain.components import RuntimeComponents
from kairospy.application.support.runtime.domain.connections import DefaultConnectionManager
from kairospy.application.usecases.market.application.runtime import BacktestMarketDataService
from kairospy.application.support.composition.application.resources import DriverName, ExchangeName, public_market_access
from kairospy.application.support.composition.application.accounts import BacktestAccountResources
from kairospy.domain.reference import MarketResolver
from kairospy.infrastructure.persistence.application.market_data import DataStore

from .common import ComposedLaunch, optional_default_text, reference_runtime


class BacktestComposition:
    def compose(self, configured: ConfiguredBacktest) -> ComposedLaunch:
        data = backtest_market_data_service(configured)
        self._configure_market_downloads(configured, data)
        account_resources = BacktestAccountResources.from_configured(configured)
        connections = DefaultConnectionManager()
        from kairospy.application.support.runtime.application.launch.resources import TradingRuntimeResources

        resources = TradingRuntimeResources(
            source=data,
            components=RuntimeComponents(
                market=data,
                account=account_resources.account,
                account_catalog=account_resources.account,
                execution=account_resources.execution,
                reference=reference_runtime(
                    configured.launch_directory,
                    default_venue=_backtest_default_venue(configured.backtest_config),
                    default_market=_backtest_default_market(configured.backtest_config),
                ),
            ),
            connection_scope=connections,
        )
        return ComposedLaunch(
            mode=RuntimeMode.BACKTEST,
            launch_id=configured.launch_id,
            strategy=configured.strategy,
            launch_directory=configured.launch_directory,
            normalized_config=configured.normalized_config,
            resources=resources,
            lifecycle=None,
            build_result=lambda runtime: account_resources.build_result(configured, runtime),
        )

    def _configure_market_downloads(self, configured: ConfiguredBacktest, data: BacktestMarketDataService) -> None:
        if configured.market_policy.on_missing != "download":
            return

        def factory(spec: MarketDataSpec):
            return public_market_access(_exchange_name(spec.venue), DriverName.ccxt)

        data.set_historical_client_factory(factory)


def backtest_market_data_service(configured: ConfiguredBacktest) -> BacktestMarketDataService:
    return BacktestMarketDataService(
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


__all__ = ["BacktestComposition", "backtest_market_data_service"]
