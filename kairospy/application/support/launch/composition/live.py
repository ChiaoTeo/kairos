from __future__ import annotations

from pathlib import Path
from typing import Mapping, Protocol

from kairospy.application.support.launch.config.live import ConfiguredLive, LiveLaunchResult
from kairospy.application.support.launch.host.resources import TradingRuntimeResources
from kairospy.application.support.launch.modes import RuntimeMode
from kairospy.application.support.runtime.connections import DefaultConnectionManager
from kairospy.application.support.runtime.services.account.live import LiveAccountService
from kairospy.application.support.launch.composition.accounts import LiveAccountResources
from kairospy.core.execution import ExecutionCoordinator
from kairospy.infrastructure.persistence.runtime_state.live_json_store import JsonLiveRuntimeStateStore

from .common import RuntimeLauncher, reference_runtime


class LiveLaunchRun(Protocol):
    def __call__(self) -> LiveLaunchResult:
        ...


class WithAccountLeases(Protocol):
    def __call__(self, configured: ConfiguredLive, mode: RuntimeMode, run: LiveLaunchRun) -> LiveLaunchResult:
        ...


class AccountStatusWriter(Protocol):
    def __call__(self, path: Path, result: object) -> None:
        ...


class ArtifactWriter(Protocol):
    def __call__(self, path: Path, result: object, config: Mapping[str, object]) -> None:
        ...


class LiveComposition:
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

    def launch(self, configured: ConfiguredLive) -> LiveLaunchResult:
        def run() -> LiveLaunchResult:
            connections = DefaultConnectionManager()
            configured.market_data.set_connection_manager(connections)
            account_resources = LiveAccountResources.from_configured(configured)
            account_resources.account.set_connection_manager(connections)
            runtime = self.launch_runtime(
                launch_id=configured.launch_id,
                mode=RuntimeMode.LIVE,
                strategy=configured.strategy,
                launch_directory=configured.launch_directory,
                normalized_config=configured.normalized_config,
                resources=TradingRuntimeResources(
                    source=configured.market_data,
                    data=configured.market_data,
                    account=account_resources.account,
                    reference=reference_runtime(
                        configured.launch_directory,
                        default_venue=configured.account_config.venue,
                        default_market="spot",
                    ),
                    trading_execution=account_resources.execution,
                    connections=connections,
                ),
                lifecycle=LiveConfiguredLifecycle(
                    configured.state_path,
                    account=account_resources.account,
                    coordinator=account_resources.coordinator,
                ),
            )
            result = account_resources.build_result(configured, runtime)
            self.write_account_status(configured.launch_directory, result)
            self.write_artifacts(configured.launch_directory, result, configured.normalized_config)
            return result

        return self.with_account_leases(configured, RuntimeMode.LIVE, run)


class LiveConfiguredLifecycle:
    def __init__(self, state_path: Path, *, account: LiveAccountService, coordinator: ExecutionCoordinator) -> None:
        self.account = account
        self.coordinator = coordinator
        self.state_store = JsonLiveRuntimeStateStore(state_path)

    def prepare(self) -> None:
        snapshot = self.state_store.load()
        if snapshot is not None:
            snapshot.restore_into(self.coordinator, self.account.private_stream_state)
        self.account.refresh()

    def complete(self) -> None:
        self.state_store.save(self.coordinator, self.account.private_stream_state)


__all__ = ["LiveComposition", "LiveConfiguredLifecycle"]
