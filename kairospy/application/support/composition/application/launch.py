"""Composition-root factories for launch application entrypoints."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from typing import Callable, Mapping

from kairospy.application.support.launch.application.control import LaunchApplication
from kairospy.application.support.launch.application.launcher import SystemCommandProducer, TradingSystemLauncher, TradingConfigurationError
from kairospy.application.support.launch.application.protocol import LaunchTarget, LaunchTargetDescriptor
from kairospy.application.support.launch.application.configuration import (
    BacktestConfigurationError,
    ConfiguredBacktest,
    ConfiguredLive,
    ConfiguredPaper,
    LiveConfigurationError,
    PaperConfigurationError,
    configured_backtest,
    configured_live,
    configured_paper,
    load_launch_config,
)
from kairospy.application.support.launch.application.protocol import LaunchRequest
from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.support.launch.application.sources import IterableEventSource
from kairospy.application.support.launch.application.runtime import LaunchRuntimeResult
from kairospy.application.actor.support.services.connections import IntegrationConnectionScope
from kairospy.application.system.application.resources import TradingLaunchSpec, TradingSystemResources
from kairospy.application.system.application.runtime import TradingSystem, TradingSystemSession
from kairospy.application.system.application.business import SystemApplication
from kairospy.application.support.messaging import Message

from .launcher import ConfiguredLaunchComposer
from .common import in_memory_message_bus, reference_runtime
from .runtime import compose_runtime_assembly
from .system import compose_system
from .compose import market_feed_resolver_builder


class DefaultLaunchTargetFactory:
    """Composition-owned adapter for the launch target boundary."""

    def __init__(self) -> None:
        self._composer = ConfiguredLaunchComposer()
        self._launcher = TradingSystemLauncher(composer=self._composer)

    def resolve(self, request: LaunchRequest) -> LaunchTarget:
        path = Path(request.config_path)
        try:
            if request.mode is RuntimeMode.BACKTEST:
                configured: object = configured_backtest(path, strategy_ref=request.strategy_ref)
            elif request.mode is RuntimeMode.PAPER:
                configured = configured_paper(
                    path,
                    market_feed_resolver_builder=market_feed_resolver_builder("paper", error_type=TradingConfigurationError),
                    account_resolver=self._launcher.account_resolver(path),
                    strategy_ref=request.strategy_ref,
                )
            elif request.mode is RuntimeMode.LIVE:
                configured = configured_live(
                    path,
                    market_feed_resolver_builder=market_feed_resolver_builder("live", error_type=TradingConfigurationError),
                    account_resolver=self._launcher.account_resolver(path),
                    strategy_ref=request.strategy_ref,
                )
            else:
                raise ValueError("config targets support backtest, paper, and live modes")
        except (BacktestConfigurationError, PaperConfigurationError, LiveConfigurationError) as error:
            raise TradingConfigurationError(str(error)) from error

        directory = request.launch_directory
        if directory is not None:
            configured = replace(configured, launch_directory=directory)
            if isinstance(configured, ConfiguredLive):
                configured = replace(configured, state_path=directory / "live_state.json")
        composer = self._composer_for(configured)
        composed = composer(configured)
        stop_requested: list[Callable[[], bool] | None] = [None]

        def run() -> object:
            return self._launcher.run_composed(composed, configured, stop_requested=stop_requested[0])

        return LaunchTarget(
            mode=request.mode,
            launch_id=str(getattr(configured, "launch_id")),
            launch_directory=Path(getattr(configured, "launch_directory")),
            _runner=run,
            _bind_stop=lambda callback: stop_requested.__setitem__(0, callback),
        )

    def describe(self, *, mode: RuntimeMode, config_path: Path) -> LaunchTargetDescriptor:
        launch_config = load_launch_config(Path(config_path))
        launch_config.require_mode(mode.value)
        mode_config = launch_config.values.get(mode.value)
        root = Path(".kairos/launches").resolve()
        if isinstance(mode_config, Mapping) and mode_config.get("launches_root") is not None:
            root_value = Path(str(mode_config["launches_root"]))
            root = root_value if root_value.is_absolute() else launch_config.root / root_value
        return LaunchTargetDescriptor(mode, launch_config.launch_id, root / mode.value / launch_config.launch_id)

    def launch_system(self, *, launch_id: str, launch_directory: Path) -> object:
        composed = compose_system(
            launch_directory=launch_directory,
            launch_id=launch_id,
            producer_source=SystemCommandProducer(launch_directory),
        )
        return self._launcher.run_resources(
            launch_id=launch_id,
            mode=RuntimeMode.SYSTEM,
            strategy=self._builtin_strategy(),
            launch_directory=launch_directory,
            normalized_config={
                "launch": {"id": launch_id, "mode": RuntimeMode.SYSTEM.value, "strategy": "builtin:CliStrategyBase"},
                "system": {"builtin": True, "interactive": True},
            },
            resources=composed.resources,
            lifecycle=composed.lifecycle,
        )

    def launch_events(
        self,
        *,
        strategy_path: str,
        events_path: str | Path,
        launch_id: str,
        mode: RuntimeMode,
    ) -> LaunchRuntimeResult:
        event_path = Path(events_path)
        strategy = self._launcher.load_strategy(strategy_path)
        return self._launcher.run_resources(
            launch_id=launch_id,
            mode=mode,
            strategy=strategy,
            launch_directory=Path(".kairos/launches") / mode.value / launch_id,
            normalized_config={"launch": {"id": launch_id, "mode": mode.value, "strategy": strategy_path}, "events": {"source": str(event_path)}},
            resources=TradingSystemResources(
                business=SystemApplication(),
                input_streams=(IterableEventSource("cli.events", self._read_event_jsonl(event_path)),),
                reference=reference_runtime(event_path),
                connection_scope=IntegrationConnectionScope(),
                message_bus=in_memory_message_bus(),
                assembly=compose_runtime_assembly(),
            ),
        )

    def open_system_session(self, *, strategy_path: str, launch_id: str, mode: RuntimeMode) -> TradingSystemSession:
        strategy = self._launcher.load_strategy(strategy_path)
        launch_directory = Path(".kairos/launches") / mode.value / launch_id
        return TradingSystem(
            TradingLaunchSpec(
                launch_id=launch_id,
                mode=mode,
                strategy=strategy,
                launch_directory=launch_directory,
                normalized_config={"launch": {"id": launch_id, "mode": mode.value, "strategy": strategy_path}, "system": {"interactive": True}},
                resources=TradingSystemResources(
                    business=SystemApplication(),
                    reference=reference_runtime(launch_directory),
                    connection_scope=IntegrationConnectionScope(),
                    message_bus=in_memory_message_bus(),
                    assembly=compose_runtime_assembly(),
                ),
            )
        ).start()

    def _composer_for(self, configured: object) -> Callable[[object], object]:
        if isinstance(configured, ConfiguredBacktest):
            return self._composer.backtest
        if isinstance(configured, ConfiguredPaper):
            return self._composer.paper
        if isinstance(configured, ConfiguredLive):
            return self._composer.live
        raise TypeError("unsupported configured launch")

    @staticmethod
    def _builtin_strategy() -> object:
        from kairospy.application.usecases.strategy.application.cli import CliStrategyBase

        return CliStrategyBase()

    @staticmethod
    def _read_event_jsonl(path: Path) -> tuple[Message, ...]:
        if not path.exists():
            raise ValueError(f"events file does not exist: {path}")
        events: list[Message] = []
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, Mapping):
                raise ValueError(f"event row {index} must be a JSON object")
            raw_time = str(row.get("time") or row.get("timestamp"))
            events.append(Message(topic=f"{str(row.get('domain') or row.get('stream') or 'data')}.{str(row.get('kind') or 'event')}", published_at=datetime.fromisoformat(raw_time), producer="cli.events", producer_sequence=int(row.get("sequence") or index), payload=row.get("payload", dict(row))))
        return tuple(events)


def launch_application() -> LaunchApplication:
    """Build the launch facade with concrete composition injected."""

    return LaunchApplication(target_factory=DefaultLaunchTargetFactory())


__all__ = ["DefaultLaunchTargetFactory", "launch_application"]
