from __future__ import annotations

from pathlib import Path
from typing import Mapping, Protocol

from kairospy.application.usecases.market import MarketDataResolver, MarketDataSpec
from kairospy.application.support.launch.config.backtest import BacktestLaunchResult, ConfiguredBacktest
from kairospy.application.support.launch.host.resources import TradingRuntimeResources
from kairospy.application.support.launch.modes import RuntimeMode
from kairospy.application.support.runtime.components import RuntimeComponents
from kairospy.application.support.runtime.connections import DefaultConnectionManager
from kairospy.application.support.runtime.services.market.modes.backtest import BacktestMarketDataService
from kairospy.application.support.launch.composition.resources import DriverName, ExchangeName, exchange
from kairospy.application.support.launch.composition.accounts import BacktestAccountResources
from kairospy.core.reference import MarketResolver
from kairospy.infrastructure.persistence.market_data.catalog import DataStore

from .common import RuntimeLauncher, optional_default_text, reference_runtime


class AccountStatusWriter(Protocol):
    def __call__(self, path: Path, result: object) -> None:
        ...


class ArtifactWriter(Protocol):
    def __call__(self, path: Path, result: object, config: Mapping[str, object]) -> None:
        ...


class BacktestComposition:
    def __init__(
        self,
        *,
        launch_runtime: RuntimeLauncher,
        write_account_status: AccountStatusWriter,
        write_artifacts: ArtifactWriter,
    ) -> None:
        self.launch_runtime = launch_runtime
        self.write_account_status = write_account_status
        self.write_artifacts = write_artifacts

    def launch(self, configured: ConfiguredBacktest) -> BacktestLaunchResult:
        data = backtest_market_data_service(configured)
        self._configure_market_downloads(configured, data)
        account_resources = BacktestAccountResources.from_configured(configured)
        connections = DefaultConnectionManager()
        runtime = self.launch_runtime(
            launch_id=configured.launch_id,
            mode=RuntimeMode.BACKTEST,
            strategy=configured.strategy,
            launch_directory=configured.launch_directory,
            normalized_config=configured.normalized_config,
            resources=TradingRuntimeResources(
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
                connections=connections,
            ),
            lifecycle=None,
        )
        result = account_resources.build_result(configured, runtime)
        self.write_account_status(configured.launch_directory, result)
        self.write_artifacts(configured.launch_directory, result, configured.normalized_config)
        return result

    def _configure_market_downloads(self, configured: ConfiguredBacktest, data: BacktestMarketDataService) -> None:
        if configured.market_policy.on_missing != "download":
            return

        def factory(spec: MarketDataSpec):
            return exchange(_exchange_name(spec.venue), DriverName.ccxt)

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
