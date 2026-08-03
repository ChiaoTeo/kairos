from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Mapping

from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.application.support.runtime.domain.lines import MergedRuntimeEventLine, RuntimeEventLine
from kairospy.application.support.runtime.domain.modes import RuntimeMode
from kairospy.application.support.runtime.domain.sources import RuntimeDataSource
from kairospy.application.usecases.strategy.protocol import Strategy
from kairospy.application.support.system.application.artifacts import LaunchOutputLog
from kairospy.application.support.system.application.artifacts import write_launch_log_section
from kairospy.application.support.composition.application.artifacts import launch_output
from kairospy.application.support.composition.application.runtime_services import compose_runtime_assembly
from kairospy.application.support.runtime.application.launch.resources import TradingRuntimeResources, TradingLaunchSpec
from kairospy.application.support.system.application.runtime import TradingSystem


class LaunchBuilder:
    def __init__(self, env: object) -> None:
        self.env = env
        self._strategy: Strategy | None = None
        self._sources: list[RuntimeEventLine] = []
        self._data: object | None = None
        self._account: object | None = None
        self._reference: object | None = None
        self._trading_execution: object | None = None
        self._echo = False

    def strategy(self, strategy: Strategy) -> "LaunchBuilder":
        self._strategy = strategy
        return self

    def source(self, source: RuntimeEventLine | RuntimeDataSource) -> "LaunchBuilder":
        self._sources.append(source)
        return self

    def clock(self, source: RuntimeEventLine | RuntimeDataSource) -> "LaunchBuilder":
        return self.source(source)

    def clocks(self, sources: Iterable[RuntimeEventLine | RuntimeDataSource]) -> "LaunchBuilder":
        for source in sources:
            self.clock(source)
        return self

    def sources(self, sources: Iterable[RuntimeEventLine | RuntimeDataSource]) -> "LaunchBuilder":
        for source in sources:
            self.source(source)
        return self

    def data(self, data: object) -> "LaunchBuilder":
        self._data = data
        return self

    def account(self, account: object) -> "LaunchBuilder":
        self._account = account
        return self

    def reference(self, reference: object) -> "LaunchBuilder":
        self._reference = reference
        return self

    def trading_execution(self, trading_execution: object) -> "LaunchBuilder":
        self._trading_execution = trading_execution
        return self

    def echo(self, enabled: bool = True) -> "LaunchBuilder":
        self._echo = enabled
        return self

    def launch(self):
        strategy = self._require_strategy()
        launch_id = str(getattr(self.env, "launch_id"))
        mode = RuntimeMode(str(getattr(self.env, "mode")))
        launch_directory = Path(getattr(self.env, "instance_dir"))
        self._validate_instance_directory(launch_directory)
        normalized_config = getattr(self.env, "normalized_config")
        if not isinstance(normalized_config, Mapping):
            normalized_config = {}
        self._write_current(launch_directory, launch_id=launch_id, mode=mode)
        stdout = sys.stdout if self._echo else None
        write_launch_log_section(
            launch_directory,
            "Launch Environment",
            {
                "launch_id": launch_id,
                "mode": mode.value,
                "launch_instance_id": getattr(self.env, "launch_instance_id", None),
                "launch_directory": launch_directory,
                "strategy_id": getattr(strategy, "strategy_id", None),
                "timezone": getattr(self.env, "timezone_name", None),
                "language": getattr(self.env, "language", None),
            },
            stdout=stdout,
        )
        write_launch_log_section(launch_directory, "System Status", {"phase": "starting"}, stdout=stdout)
        source = _source_line(self._sources)
        resources = TradingRuntimeResources(
            source=source,
            data=self._data,
            account=self._account,
            reference=self._reference,
            trading_execution=self._trading_execution,
            assembly=compose_runtime_assembly(),
        )
        with LaunchOutputLog(launch_directory, stdout=sys.stdout if self._echo else None, stderr=sys.stderr if self._echo else None):
            result = TradingSystem(
                TradingLaunchSpec(
                    launch_id=launch_id,
                    mode=mode,
                    strategy=strategy,
                    launch_directory=launch_directory,
                    normalized_config=normalized_config,
                    resources=resources,
                )
            ).run()
        write_launch_log_section(
            launch_directory,
            "System Status",
            {
                "phase": "stopped",
                "events": getattr(result.runtime, "event_count", None),
                "intents": getattr(result.runtime, "intent_count", None),
            },
            stdout=stdout,
        )
        launch_output(launch_directory).write_result(result=result, normalized_config=normalized_config)
        return result

    def _require_strategy(self) -> Strategy:
        if self._strategy is None:
            raise ValueError("launch builder requires a strategy")
        return self._strategy

    def _write_current(self, launch_directory: Path, *, launch_id: str, mode: RuntimeMode) -> None:
        group_directory = getattr(self.env, "run_group_dir", None)
        if group_directory is None:
            return
        group = Path(group_directory)
        group.mkdir(parents=True, exist_ok=True)
        payload = {
            "launch_id": launch_id,
            "mode": mode.value,
            "launch_instance_id": getattr(self.env, "launch_instance_id", None),
            "directory": str(launch_directory),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        (group / "current.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _validate_instance_directory(self, launch_directory: Path) -> None:
        group_directory = getattr(self.env, "run_group_dir", None)
        if group_directory is None:
            return
        group = Path(group_directory).resolve()
        directory = launch_directory.resolve()
        if directory == group:
            raise ValueError("launch artifacts cannot be written directly to the launch group directory")
        if directory.parent != group / "instances":
            raise ValueError("launch artifacts must be written under <launch-group>/instances/<launch-instance-id>")


class OrderedEventLine:
    def __init__(self, lines: Iterable[RuntimeEventLine]) -> None:
        self.lines = tuple(lines)

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        events: list[RuntimeEnvelope] = []
        for line in self.lines:
            async for event in line.events():
                events.append(event)
        for sequence, event in enumerate(sorted(events, key=lambda item: (item.time, item.sequence)), start=1):
            yield RuntimeEnvelope(event.domain, event.kind, event.time, sequence, event.payload)


def _source_line(sources: Iterable[RuntimeEventLine]) -> RuntimeEventLine | None:
    lines = tuple(sources)
    if not lines:
        return None
    if len(lines) == 1:
        return lines[0]
    if all(getattr(line, "is_finite", False) for line in lines):
        return OrderedEventLine(lines)
    return MergedRuntimeEventLine(lines)


__all__ = ["OrderedEventLine", "LaunchBuilder"]
