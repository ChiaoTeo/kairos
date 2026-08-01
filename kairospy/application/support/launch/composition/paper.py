from __future__ import annotations

from pathlib import Path
from typing import Mapping, Protocol

from kairospy.application.support.launch.config.paper import ConfiguredPaper, PaperLaunchResult
from kairospy.application.support.launch.host.resources import TradingRuntimeResources
from kairospy.application.support.launch.modes import RuntimeMode
from kairospy.application.support.runtime.connections import DefaultConnectionManager
from kairospy.application.support.launch.composition.accounts import PaperAccountResources

from .common import RuntimeLauncher, optional_default_text, reference_runtime


class PaperLaunchRun(Protocol):
    def __call__(self) -> PaperLaunchResult:
        ...


class WithAccountLeases(Protocol):
    def __call__(self, configured: ConfiguredPaper, mode: RuntimeMode, run: PaperLaunchRun) -> PaperLaunchResult:
        ...


class AccountStatusWriter(Protocol):
    def __call__(self, path: Path, result: object) -> None:
        ...


class ArtifactWriter(Protocol):
    def __call__(self, path: Path, result: object, config: Mapping[str, object]) -> None:
        ...


class PaperComposition:
    def __init__(
        self,
        *,
        launch_runtime: RuntimeLauncher,
        with_account_leases: WithAccountLeases,
        write_account_status: AccountStatusWriter,
        write_artifacts: ArtifactWriter,
    ) -> None:
        self.launch_runtime = launch_runtime
        self.with_account_leases = with_account_leases
        self.write_account_status = write_account_status
        self.write_artifacts = write_artifacts

    def launch(self, configured: ConfiguredPaper) -> PaperLaunchResult:
        def run() -> PaperLaunchResult:
            connections = DefaultConnectionManager()
            configured.market_data.set_connection_manager(connections)
            resources = PaperAccountResources.from_configured(configured)
            runtime = self.launch_runtime(
                launch_id=configured.launch_id,
                mode=RuntimeMode.PAPER,
                strategy=configured.strategy,
                launch_directory=configured.launch_directory,
                normalized_config=configured.normalized_config,
                resources=TradingRuntimeResources(
                    source=configured.market_data,
                    data=configured.market_data,
                    account=resources.account,
                    reference=reference_runtime(
                        configured.launch_directory,
                        default_venue=configured.account_config.venue,
                        default_market=_paper_default_market(configured.normalized_config),
                    ),
                    trading_execution=resources.execution,
                    connections=connections,
                ),
                lifecycle=None,
            )
            result = resources.build_result(configured, runtime)
            self.write_account_status(configured.launch_directory, result)
            self.write_artifacts(configured.launch_directory, result, configured.normalized_config)
            return result

        return self.with_account_leases(configured, RuntimeMode.PAPER, run)


def _paper_default_market(config: Mapping[str, object]) -> str:
    market = config.get("market")
    if isinstance(market, Mapping):
        return optional_default_text(market.get("market")) or "spot"
    return "spot"


__all__ = ["PaperComposition"]
